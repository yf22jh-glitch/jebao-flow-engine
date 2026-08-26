import asyncio
import os
import stat
from pathlib import Path

import pytest

from jebao_flow.hardware_guard import (
    DeploymentHardwareGuard,
    HardwareOperationBusyError,
    HardwareOperationLockError,
)


def _guard(tmp_path: Path, *, poll_interval_seconds: float = 0.01) -> DeploymentHardwareGuard:
    return DeploymentHardwareGuard(
        operation_lock_path=tmp_path / "hardware-operation.lock",
        latch_path=tmp_path / "emergency-stop.latch",
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
        "jebao_flow.hardware_guard.validate_hardware_safety_root",
        lambda: calls.append("validated"),
    )

    guard = DeploymentHardwareGuard()
    with guard.lease():
        pass

    assert calls == ["validated"]


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
