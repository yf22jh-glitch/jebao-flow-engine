import json
import os
import stat
from pathlib import Path

import pytest

from jebao_flow.exact_restore_guard import ExactRestoreGuard
from jebao_flow.local_control import lease as lease_module
from jebao_flow.local_control.lease import (
    LocalControlLease,
    LocalControlLeaseBusyError,
    LocalControlLeaseError,
    LocalControlReadLease,
)


def _lease(tmp_path: Path, *, idle: bool = True) -> LocalControlLease:
    guard = ExactRestoreGuard._for_test(
        operation_lock_path=tmp_path / "hardware-operation.lock",
        latch_path=tmp_path / "emergency-stop.latch",
    )
    return LocalControlLease(
        guard=guard,
        device_lock_directory=tmp_path / "physical-locks",
        legacy_workflows_idle_probe=lambda: idle,
    )


def test_lease_uses_stable_physical_key_filename_and_shared_global_lock(tmp_path: Path) -> None:
    key = "a" * 64
    owner = _lease(tmp_path)
    contender = _lease(tmp_path)

    with owner.acquire((key,)) as active:
        active.require_permitted()
        lock = tmp_path / "physical-locks" / f"{key}.lock"
        assert stat.S_IMODE(lock.stat().st_mode) == 0o600
        assert lock.stat().st_nlink == 1
        with pytest.raises(LocalControlLeaseError):
            with contender.acquire((key,)):
                raise AssertionError("concurrent global lease was acquired")


def test_nonterminal_exact_restore_blocks_before_device_lock_creation(tmp_path: Path) -> None:
    key = "b" * 64

    with pytest.raises(LocalControlLeaseBusyError):
        with _lease(tmp_path, idle=False).acquire((key,)):
            raise AssertionError("nonterminal exact restore admitted a local write")

    assert not (tmp_path / "physical-locks").exists()


def test_fixed_workflow_inventory_blocks_every_live_journal_and_accepts_terminal_intents(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = {
        "native_linkage_journal_path": tmp_path / "native-journal.json",
        "verification_journal_path": tmp_path / "verification-journal.json",
        "schedule_linkage_journal_path": tmp_path / "schedule-journal.json",
        "temporary_schedule_journal_path": tmp_path / "temporary-schedule.json",
        "exact_restore_journal_path": tmp_path / "exact-restore.json",
        "native_linkage_intent_path": tmp_path / "native-intent.json",
        "verification_intent_path": tmp_path / "verification-intent.json",
        "schedule_linkage_intent_path": tmp_path / "schedule-intent.json",
    }
    for name, path in paths.items():
        monkeypatch.setattr(lease_module, name, lambda selected=path: selected)

    assert lease_module._legacy_write_workflows_idle() is True
    terminal = {
        "version": 1,
        "instance_id": "instance",
        "operation_id": "operation",
        "phase": "terminal",
        "outcome": "restored",
        "created_at": "2026-09-08T00:00:00Z",
        "updated_at": "2026-09-08T00:01:00Z",
    }
    paths["native_linkage_intent_path"].write_text(json.dumps(terminal), encoding="ascii")
    paths["native_linkage_intent_path"].chmod(0o600)
    assert lease_module._legacy_write_workflows_idle() is True

    for name in (
        "native_linkage_journal_path",
        "verification_journal_path",
        "schedule_linkage_journal_path",
        "temporary_schedule_journal_path",
        "exact_restore_journal_path",
    ):
        paths[name].write_text("{}", encoding="ascii")
        paths[name].chmod(0o600)
        assert lease_module._legacy_write_workflows_idle() is False
        paths[name].unlink()


def test_nonterminal_or_malformed_intent_never_counts_as_idle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    intent_path = tmp_path / "native-intent.json"
    for name in (
        "native_linkage_journal_path",
        "verification_journal_path",
        "schedule_linkage_journal_path",
        "temporary_schedule_journal_path",
        "exact_restore_journal_path",
    ):
        monkeypatch.setattr(lease_module, name, lambda selected=tmp_path / name: selected)
    monkeypatch.setattr(lease_module, "native_linkage_intent_path", lambda: intent_path)
    monkeypatch.setattr(
        lease_module,
        "verification_intent_path",
        lambda: tmp_path / "verification-intent.json",
    )
    monkeypatch.setattr(
        lease_module,
        "schedule_linkage_intent_path",
        lambda: tmp_path / "schedule-intent.json",
    )

    intent_path.write_text(
        json.dumps(
            {
                "version": 1,
                "instance_id": "instance",
                "operation_id": "operation",
                "phase": "recovery_required",
                "created_at": "2026-09-08T00:00:00Z",
                "updated_at": "2026-09-08T00:01:00Z",
            }
        ),
        encoding="ascii",
    )
    intent_path.chmod(0o600)
    assert lease_module._legacy_write_workflows_idle() is False

    intent_path.write_text('{"phase":"terminal"}', encoding="ascii")
    assert lease_module._legacy_write_workflows_idle() is False


def test_persistent_emergency_latch_blocks_write_admission(tmp_path: Path) -> None:
    (tmp_path / "emergency-stop.latch").write_text("stop\n", encoding="ascii")

    with pytest.raises(LocalControlLeaseError, match="emergency stop"):
        with _lease(tmp_path).acquire(("c" * 64,)):
            raise AssertionError("emergency latch admitted a local write")


def test_lease_rejects_duplicate_or_non_digest_identity(tmp_path: Path) -> None:
    lease = _lease(tmp_path)
    with pytest.raises(ValueError, match="distinct"):
        with lease.acquire(("a" * 64, "a" * 64)):
            pass
    with pytest.raises(ValueError, match="SHA-256"):
        with lease.acquire(("not-a-digest",)):
            pass


def test_shared_state_reads_coexist_but_never_overlap_an_exclusive_writer(
    tmp_path: Path,
) -> None:
    key = "d" * 64
    directory = tmp_path / "physical-locks"
    first_reader = LocalControlReadLease(device_lock_directory=directory)
    second_reader = LocalControlReadLease(device_lock_directory=directory)

    with first_reader.acquire(key):
        with second_reader.acquire(key):
            pass
        with pytest.raises(LocalControlLeaseBusyError, match="owned"):
            with _lease(tmp_path).acquire((key,)):
                raise AssertionError("writer overlapped a fresh state read")

    with _lease(tmp_path).acquire((key,)):
        with pytest.raises(LocalControlLeaseBusyError, match="write workflow"):
            with first_reader.acquire(key):
                raise AssertionError("state read observed an intermediate write")


def test_default_read_lease_validates_the_identified_root_before_creating_locks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "hardware-safety"
    root.mkdir(mode=0o700)
    directory = root / "physical-locks"
    validations: list[bool] = []
    monkeypatch.setattr(lease_module, "physical_lock_directory", lambda: directory)
    monkeypatch.setattr(
        lease_module,
        "_open_hardware_safety_mount",
        lambda: os.open(root, os.O_RDONLY),
    )
    monkeypatch.setattr(
        lease_module,
        "_validate_hardware_safety_root_descriptor",
        lambda _descriptor: validations.append(directory.exists()),
    )

    with LocalControlReadLease().acquire("e" * 64):
        assert (directory / f"{'e' * 64}.lock").is_file()

    assert validations == [False, True, True]


def test_default_read_lease_never_creates_an_absent_safety_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "absent-hardware-safety"
    monkeypatch.setattr(
        lease_module,
        "physical_lock_directory",
        lambda: root / "physical-locks",
    )
    monkeypatch.setattr(
        lease_module,
        "_open_hardware_safety_mount",
        lambda: (_ for _ in ()).throw(
            lease_module.HardwareSafetyRootError("root unavailable")
        ),
    )

    with pytest.raises(LocalControlLeaseError, match="identified hardware-safety root"):
        with LocalControlReadLease().acquire("f" * 64):
            raise AssertionError("an absent safety root admitted a fresh read")

    assert not root.exists()
