import ast
import asyncio
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from threading import Thread

import pytest

from jebao_flow import hardware_safety
from jebao_flow.exact_restore_guard import (
    ExactRestoreGuard,
    ExactRestoreOperationBusyError,
    ExactRestoreOperationLockError,
)


def _guard(tmp_path: Path, *, poll_interval_seconds: float = 0.01) -> ExactRestoreGuard:
    return ExactRestoreGuard._for_test(
        operation_lock_path=tmp_path / "hardware-operation.lock",
        latch_path=tmp_path / "emergency-stop.latch",
        poll_interval_seconds=poll_interval_seconds,
    )


def test_guard_starts_fail_closed(tmp_path: Path) -> None:
    guard = _guard(tmp_path)

    assert guard.permitted is False
    assert guard.epoch == 0


def test_trip_changes_epoch_and_clear_does_not_revive_stale_epoch(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    with guard.lease():
        guard.clear()
        original_epoch = guard.epoch
        assert guard.permitted

        guard.trip()
        assert not guard.permitted
        assert guard.epoch == original_epoch + 1

        guard.clear()
        assert guard.permitted
        assert guard.epoch == original_epoch + 1

    assert not guard.permitted


def test_existing_latch_prevents_clear_including_broken_symlink(tmp_path: Path) -> None:
    latch_path = tmp_path / "emergency-stop.latch"
    latch_path.symlink_to(tmp_path / "missing-target")
    guard = _guard(tmp_path)

    with guard.lease():
        guard.clear()

        assert not guard.permitted
    assert guard.epoch == 1


def test_unreadable_latch_metadata_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guard = _guard(tmp_path)
    real_lstat = Path.lstat
    latch_path = tmp_path / "emergency-stop.latch"

    def fail_lstat(path: Path):
        if path == latch_path:
            raise PermissionError("denied")
        return real_lstat(path)

    monkeypatch.setattr(Path, "lstat", fail_lstat)
    with guard.lease():
        guard.clear()

        assert guard.permitted is False


@pytest.mark.asyncio
async def test_wait_returns_immediately_when_guard_is_already_blocked(tmp_path: Path) -> None:
    await asyncio.wait_for(_guard(tmp_path).wait_until_blocked(), timeout=0.1)


@pytest.mark.asyncio
async def test_wait_detects_latch_race_and_remains_tripped_after_file_removal(
    tmp_path: Path,
) -> None:
    guard = _guard(tmp_path)
    with guard.lease():
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
    with guard.lease():
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


def test_lease_creates_private_regular_single_link_owner_file(tmp_path: Path) -> None:
    guard = _guard(tmp_path)
    lock_path = tmp_path / "hardware-operation.lock"

    with guard.lease():
        metadata = lock_path.lstat()
        assert stat.S_ISREG(metadata.st_mode)
        assert metadata.st_uid == os.geteuid()
        assert stat.S_IMODE(metadata.st_mode) == 0o600
        assert metadata.st_nlink == 1


def test_lease_rejects_symlink_without_touching_target(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.write_text("unchanged", encoding="utf-8")
    (tmp_path / "hardware-operation.lock").symlink_to(target)
    guard = _guard(tmp_path)

    with pytest.raises(ExactRestoreOperationLockError):
        with guard.lease():
            raise AssertionError("unsafe lease was acquired")

    assert target.read_text(encoding="utf-8") == "unchanged"


def test_lease_is_nonblocking_across_guard_instances(tmp_path: Path) -> None:
    owner = _guard(tmp_path)
    contender = _guard(tmp_path)

    with owner.lease():
        with pytest.raises(ExactRestoreOperationBusyError):
            with contender.lease():
                raise AssertionError("concurrent lease was acquired")

    with contender.lease():
        pass


def test_lease_rejects_non_regular_lock_path(tmp_path: Path) -> None:
    (tmp_path / "hardware-operation.lock").mkdir()
    guard = _guard(tmp_path)

    with pytest.raises(ExactRestoreOperationLockError):
        with guard.lease():
            raise AssertionError("directory lease was acquired")


def test_lease_rejects_hardlinked_or_overpermissive_lock(tmp_path: Path) -> None:
    lock_path = tmp_path / "hardware-operation.lock"
    lock_path.write_text("lock\n", encoding="utf-8")
    lock_path.chmod(0o600)
    os.link(lock_path, tmp_path / "lock-alias")

    with pytest.raises(ExactRestoreOperationLockError, match="metadata"):
        with _guard(tmp_path).lease():
            raise AssertionError("hardlinked lease was acquired")

    (tmp_path / "lock-alias").unlink()
    lock_path.chmod(0o640)
    with pytest.raises(ExactRestoreOperationLockError, match="metadata"):
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

    with pytest.raises(ExactRestoreOperationLockError, match="ownership"):
        with _guard(tmp_path).lease():
            raise AssertionError("wrong-owner lease was acquired")


def test_lease_rejects_path_replacement_after_open(tmp_path: Path) -> None:
    class _ReplacingGuard(ExactRestoreGuard):
        replaced = False

        def _validate_open_lock(self, descriptor: int, root_descriptor: int = -1) -> None:
            if not self.replaced:
                self.replaced = True
                lock_path = tmp_path / "hardware-operation.lock"
                lock_path.unlink()
                lock_path.touch(mode=0o600)
                lock_path.chmod(0o600)
            super()._validate_open_lock(descriptor, root_descriptor)

    guard = _ReplacingGuard._for_test(
        operation_lock_path=tmp_path / "hardware-operation.lock",
        latch_path=tmp_path / "emergency-stop.latch",
    )

    with pytest.raises(ExactRestoreOperationLockError, match="metadata"):
        with guard.lease():
            raise AssertionError("replaced lease inode was acquired")


def test_default_paths_validate_fixed_safety_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "hardware-safety"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda path: path == root)

    guard = ExactRestoreGuard()
    with guard.lease():
        pass

    assert {path.name for path in root.iterdir()} == {"hardware-operation.lock"}


def test_fixed_root_swap_after_validation_never_opens_replacement_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "hardware-safety"
    detached = tmp_path / "detached-hardware-safety"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda path: path == root)
    guard = ExactRestoreGuard()
    real_open_root = hardware_safety._open_hardware_safety_root

    def open_then_swap_root() -> int:
        descriptor = real_open_root()
        root.rename(detached)
        root.mkdir(mode=0o700)
        os.chmod(root, 0o700)
        return descriptor

    monkeypatch.setattr(
        "jebao_flow.exact_restore_guard._open_hardware_safety_root",
        open_then_swap_root,
    )

    with pytest.raises(ExactRestoreOperationLockError, match="changed|unavailable"):
        with guard.lease():
            raise AssertionError("lease escaped into a replacement safety root")

    assert list(root.iterdir()) == []


def test_clear_without_active_lease_remains_fail_closed(tmp_path: Path) -> None:
    guard = _guard(tmp_path)

    guard.clear()

    assert not guard.permitted


def test_replaced_lock_trips_active_guard_and_invalidates_stale_epoch(tmp_path: Path) -> None:
    owner = _guard(tmp_path)
    contender = _guard(tmp_path)
    lock_path = tmp_path / "hardware-operation.lock"

    with pytest.raises(ExactRestoreOperationLockError, match="integrity was lost"):
        with owner.lease():
            owner.clear()
            captured_epoch = owner.epoch
            assert owner.permitted

            lock_path.unlink()
            with contender.lease():
                contender.clear()
                assert contender.permitted
                assert not owner.permitted
                assert owner.epoch != captured_epoch

            assert not owner.permitted


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires POSIX fork semantics")
def test_fork_child_context_unwind_does_not_unlock_parent_lease(tmp_path: Path) -> None:
    owner = _guard(tmp_path)
    contender = _guard(tmp_path)
    read_descriptor, write_descriptor = os.pipe()

    context = owner.lease()
    context.__enter__()
    owner.clear()
    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.close(read_descriptor)
            with pytest.raises(ExactRestoreOperationLockError, match="owner process and thread"):
                context.__exit__(None, None, None)
            os.write(write_descriptor, b"x")
            os.close(write_descriptor)
        finally:
            os._exit(0)

    os.close(write_descriptor)
    assert os.read(read_descriptor, 1) == b"x"
    with pytest.raises(ExactRestoreOperationBusyError):
        with contender.lease():
            raise AssertionError("fork child released the parent lease")
    assert owner.permitted
    waited_pid, status = os.waitpid(child_pid, 0)
    assert waited_pid == child_pid
    assert os.waitstatus_to_exitcode(status) == 0
    context.__exit__(None, None, None)
    os.close(read_descriptor)


def test_wrong_thread_context_exit_leaves_owner_lease_intact(tmp_path: Path) -> None:
    owner = _guard(tmp_path)
    contender = _guard(tmp_path)
    context = owner.lease()
    context.__enter__()
    owner.clear()
    captured_epoch = owner.epoch
    failures: list[BaseException] = []

    def release_from_other_thread() -> None:
        try:
            context.__exit__(None, None, None)
        except BaseException as error:
            failures.append(error)

    thread = Thread(target=release_from_other_thread)
    thread.start()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], ExactRestoreOperationLockError)
    with pytest.raises(ExactRestoreOperationBusyError):
        with contender.lease():
            raise AssertionError("wrong-thread cleanup released the owner lease")
    assert owner.permitted
    assert owner.epoch == captured_epoch

    context.__exit__(None, None, None)
    with contender.lease():
        pass


def test_missing_o_nofollow_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(os, "O_NOFOLLOW")

    with pytest.raises(ExactRestoreOperationLockError, match="O_NOFOLLOW"):
        with _guard(tmp_path).lease():
            raise AssertionError("lease without O_NOFOLLOW was acquired")


def test_import_graph_does_not_load_devices_frozen_harness_or_old_guard() -> None:
    module_path = Path("src/jebao_flow/exact_restore_guard.py")
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    forbidden_prefixes = (
        "jebao_flow.devices",
        "jebao_flow.hardware_guard",
        "jebao_flow.schedule_flow_experiment_cli",
        "jebao_flow.schedule_linkage_cli",
    )
    assert not any(
        name == prefix or name.startswith(f"{prefix}.")
        for name in imported
        for prefix in forbidden_prefixes
    )

    source_root = str(Path("src").resolve())
    script = f"""
import json
import sys
sys.path.insert(0, {source_root!r})
import jebao_flow.exact_restore_guard
forbidden = sorted(
    name for name in sys.modules
    if name.startswith("jebao_flow.devices")
    or name in {{
        "jebao_flow.hardware_guard",
        "jebao_flow.schedule_flow_experiment_cli",
        "jebao_flow.schedule_linkage_cli",
    }}
)
print(json.dumps(forbidden))
"""
    result = subprocess.run(
        [sys.executable, "-P", "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == []
