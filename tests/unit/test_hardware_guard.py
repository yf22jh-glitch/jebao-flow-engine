import asyncio
import errno
import os
import stat
from pathlib import Path

import pytest

from jebao_flow import hardware_safety
from jebao_flow.exact_restore_guard import ExactRestoreGuard
from jebao_flow.hardware_guard import (
    DeploymentHardwareGuard,
    HardwareOperationBusyError,
    HardwareOperationLockError,
)


def _guard(tmp_path: Path, *, poll_interval_seconds: float = 0.01) -> DeploymentHardwareGuard:
    return DeploymentHardwareGuard(
        operation_lock_path=tmp_path / "hardware-operation.lock",
        latch_path=tmp_path / "emergency-stop.latch",
        exact_restore_journal_path=tmp_path / "exact-restore.json",
        poll_interval_seconds=poll_interval_seconds,
    )


def test_lease_creates_private_regular_owner_file(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    lock_path = tmp_path / "hardware-operation.lock"

    with guard.lease():
        metadata = lock_path.lstat()
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_uid == os.geteuid()
        assert stat.S_IMODE(metadata.st_mode) == 0o600


def test_lease_rejects_symlink_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("unchanged", encoding="utf-8")
    (tmp_path / "hardware-operation.lock").symlink_to(target)
    guard = _guard(tmp_path)

    with pytest.raises(HardwareOperationLockError):
        with guard.lease():
            raise AssertionError("unsafe lease was acquired")

    assert target.read_text(encoding="utf-8") == "unchanged"


def test_lease_is_nonblocking_across_guard_instances(tmp_path: Path) -> None:
    owner = _guard(tmp_path)
    contender = _guard(tmp_path)

    with owner.lease():
        with pytest.raises(HardwareOperationBusyError):
            with contender.lease():
                raise AssertionError("concurrent lease was acquired")

    with contender.lease():
        pass


def test_replaced_lock_trips_active_guard_and_invalidates_stale_epoch(tmp_path: Path) -> None:
    owner = _guard(tmp_path)
    lock_path = tmp_path / "hardware-operation.lock"
    exact_contender = ExactRestoreGuard._for_test(
        operation_lock_path=lock_path,
        latch_path=tmp_path / "emergency-stop.latch",
    )

    with pytest.raises(HardwareOperationLockError, match="integrity was lost"):
        with owner.lease():
            owner.clear()
            captured_epoch = owner.epoch
            assert owner.permitted

            lock_path.unlink()
            with exact_contender.lease():
                exact_contender.clear()
                assert exact_contender.permitted
                assert not owner.permitted
                assert owner.epoch != captured_epoch

            assert not owner.permitted


def test_releasing_lease_blocks_guard_and_invalidates_stale_epoch(tmp_path: Path) -> None:
    guard = _guard(tmp_path)

    with guard.lease():
        guard.clear()
        captured_epoch = guard.epoch
        assert guard.permitted

    assert not guard.permitted
    assert guard.epoch != captured_epoch


def test_lease_yields_when_exact_restore_journal_is_absent(tmp_path: Path) -> None:
    entered = False

    with _guard(tmp_path).lease():
        entered = True

    assert entered


def test_lease_rejects_existing_exact_restore_journal_before_yield(tmp_path: Path) -> None:
    (tmp_path / "exact-restore.json").write_text("{}\n", encoding="utf-8")
    entered = False

    with pytest.raises(HardwareOperationBusyError, match="exact-restore journal"):
        with _guard(tmp_path).lease():
            entered = True

    assert not entered


def test_lease_rejects_exact_restore_journal_symlink(tmp_path: Path) -> None:
    target = tmp_path / "missing-journal-target"
    (tmp_path / "exact-restore.json").symlink_to(target)

    with pytest.raises(HardwareOperationBusyError, match="exact-restore journal"):
        with _guard(tmp_path).lease():
            raise AssertionError("lease yielded with a journal filesystem object")


@pytest.mark.parametrize(
    "error",
    [PermissionError(errno.EACCES, "denied"), OSError(errno.EIO, "I/O error")],
    ids=["unreadable", "filesystem-error"],
)
def test_lease_treats_exact_restore_journal_lookup_error_as_blocked(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: OSError,
) -> None:
    journal_path = tmp_path / "exact-restore.json"
    original_lstat = Path.lstat

    def fail_journal_lookup(path: Path) -> os.stat_result:
        if path == journal_path:
            raise error
        return original_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_journal_lookup)

    with pytest.raises(HardwareOperationBusyError, match="cannot prove"):
        with _guard(tmp_path).lease():
            raise AssertionError("lease yielded after an indeterminate journal lookup")


def test_isolated_paths_without_journal_do_not_read_production_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "jebao_flow.hardware_guard.default_exact_restore_journal_path",
        lambda: (_ for _ in ()).throw(AssertionError("production path was resolved")),
    )
    guard = DeploymentHardwareGuard(
        operation_lock_path=tmp_path / "hardware-operation.lock",
        latch_path=tmp_path / "emergency-stop.latch",
    )

    with guard.lease():
        pass


def test_lease_rejects_non_regular_lock_path(tmp_path: Path) -> None:
    (tmp_path / "hardware-operation.lock").mkdir()
    guard = _guard(tmp_path)

    with pytest.raises(HardwareOperationLockError):
        with guard.lease():
            raise AssertionError("directory lease was acquired")


def test_lease_rejects_hardlinked_or_overpermissive_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "hardware-operation.lock"
    lock_path.write_text("lock\n", encoding="utf-8")
    lock_path.chmod(0o600)
    os.link(lock_path, tmp_path / "lock-alias")

    with pytest.raises(HardwareOperationLockError, match="metadata"):
        with _guard(tmp_path).lease():
            raise AssertionError("hardlinked lease was acquired")

    (tmp_path / "lock-alias").unlink()
    lock_path.chmod(0o640)
    with pytest.raises(HardwareOperationLockError, match="metadata"):
        with _guard(tmp_path).lease():
            raise AssertionError("overpermissive lease was acquired")


def test_lease_rejects_wrong_owner_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lock_path = tmp_path / "hardware-operation.lock"
    lock_path.touch(mode=0o600)
    actual_euid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: actual_euid + 1)
    guard = _guard(tmp_path)

    with pytest.raises(HardwareOperationLockError, match="ownership"):
        with guard.lease():
            raise AssertionError("wrong-owner lease was acquired")


def test_default_paths_validate_fixed_safety_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "jebao_flow.hardware_guard.global_operation_lock_path",
        lambda: tmp_path / "hardware-operation.lock",
    )
    monkeypatch.setattr(
        "jebao_flow.hardware_guard.emergency_stop_latch_path",
        lambda: tmp_path / "emergency-stop.latch",
    )
    monkeypatch.setattr(
        "jebao_flow.hardware_guard.default_exact_restore_journal_path",
        lambda: tmp_path / "exact-restore.json",
    )
    monkeypatch.setattr(
        "jebao_flow.hardware_guard.validate_hardware_safety_root",
        lambda: calls.append("validated"),
    )
    monkeypatch.setattr("jebao_flow.hardware_guard.hardware_safety_root", lambda: tmp_path)
    monkeypatch.setattr("jebao_flow.hardware_safety.hardware_safety_root", lambda: tmp_path)
    monkeypatch.setattr("jebao_flow.hardware_safety.os.path.ismount", lambda _path: True)

    guard = DeploymentHardwareGuard()
    with guard.lease():
        pass

    assert calls == ["validated"]


def test_fixed_root_swap_never_opens_replacement_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "hardware-safety"
    detached = tmp_path / "detached-hardware-safety"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda path: path == root)
    guard = DeploymentHardwareGuard()
    real_open_root = hardware_safety._open_hardware_safety_root

    def open_then_swap_root() -> int:
        descriptor = real_open_root()
        root.rename(detached)
        root.mkdir(mode=0o700)
        os.chmod(root, 0o700)
        return descriptor

    monkeypatch.setattr(
        "jebao_flow.hardware_guard._open_hardware_safety_root",
        open_then_swap_root,
    )

    with pytest.raises(HardwareOperationLockError, match="changed|unavailable"):
        with guard.lease():
            raise AssertionError("lease escaped into a replacement safety root")

    assert list(root.iterdir()) == []


def test_trip_changes_epoch_and_clear_does_not_revive_stale_epoch(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    guard.clear()
    original_epoch = guard.epoch
    assert guard.permitted

    guard.trip()
    assert not guard.permitted
    assert guard.epoch == original_epoch + 1

    guard.clear()
    assert guard.permitted
    assert guard.epoch == original_epoch + 1


def test_existing_latch_prevents_clear_including_broken_symlink(tmp_path: Path) -> None:
    latch_path = tmp_path / "emergency-stop.latch"
    latch_path.symlink_to(tmp_path / "missing-target")
    guard = _guard(tmp_path)

    guard.clear()

    assert not guard.permitted
    assert guard.epoch == 0


@pytest.mark.asyncio
async def test_wait_detects_latch_race_and_remains_tripped_after_file_removal(
    tmp_path: Path,
) -> None:
    guard = _guard(tmp_path)
    guard.clear()
    waiter = asyncio.create_task(guard.wait_until_blocked())
    await asyncio.sleep(0)

    latch_path = tmp_path / "emergency-stop.latch"
    latch_path.write_text("emergency_stop\n", encoding="utf-8")
    await asyncio.wait_for(waiter, timeout=1)

    assert not guard.permitted
    assert guard.epoch == 1
    latch_path.unlink()
    assert not guard.permitted
    guard.clear()
    assert guard.permitted
    assert guard.epoch == 1


@pytest.mark.asyncio
async def test_wait_wakes_immediately_for_local_trip(tmp_path: Path) -> None:
    guard = _guard(tmp_path, poll_interval_seconds=1)
    guard.clear()
    waiter = asyncio.create_task(guard.wait_until_blocked())
    await asyncio.sleep(0)

    guard.trip()
    await asyncio.wait_for(waiter, timeout=0.1)

    assert not guard.permitted
    assert guard.epoch == 1


def test_poll_interval_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="positive"):
        _guard(tmp_path, poll_interval_seconds=0)
