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


def _parked_tombstone() -> dict[str, object]:
    return {
        "version": 2,
        "journal_format": 2,
        "operation_id": "safe-operation-id",
        "phase": "parked_unqualified",
        "operation_sha256": "a" * 64,
        "disposition_id": f"ERD-{'b' * 32}",
        "disposition_sha256": "c" * 64,
        "journal_snapshot_sha256": "d" * 64,
    }


def _write_private_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


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
    ):
        paths[name].write_text("{}", encoding="ascii")
        paths[name].chmod(0o600)
        assert lease_module._legacy_write_workflows_idle() is False
        paths[name].unlink()

    _write_private_json(paths["exact_restore_journal_path"], {})
    assert lease_module._legacy_write_workflows_idle() is False
    _write_private_json(paths["exact_restore_journal_path"], _parked_tombstone())
    assert lease_module._legacy_write_workflows_idle() is True


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("version", 1),
        ("version", 2.0),
        ("journal_format", 1),
        ("journal_format", True),
        ("operation_id", "contains spaces"),
        ("phase", "parked"),
        ("operation_sha256", "A" * 64),
        ("disposition_id", "ERD-not-a-digest"),
        ("disposition_sha256", "c" * 63),
        ("journal_snapshot_sha256", "g" * 64),
    ),
)
def test_exact_restore_tombstone_requires_the_exact_terminal_envelope(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    path = tmp_path / "exact-restore.json"
    payload = _parked_tombstone()
    payload[field] = value
    _write_private_json(path, payload)

    assert lease_module._terminal_parked_exact_restore_or_absent(path) is False


def test_exact_restore_tombstone_rejects_extra_duplicate_truncated_and_oversize_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "exact-restore.json"
    payload = _parked_tombstone()
    payload["unexpected"] = True
    _write_private_json(path, payload)
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is False

    path.write_text(
        '{"version":2,"version":2}',
        encoding="ascii",
    )
    path.chmod(0o600)
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is False

    path.write_text('{"version":', encoding="ascii")
    path.chmod(0o600)
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is False

    _write_private_json(path, _parked_tombstone())
    path.write_bytes(path.read_bytes()[:-1] + b" \n")
    path.chmod(0o600)
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is False

    monkeypatch.setattr(lease_module, "_MAX_SAFETY_ARTIFACT_BYTES", 8)
    path.write_text("{}" + " " * 8, encoding="ascii")
    path.chmod(0o600)
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is False


def test_exact_restore_tombstone_is_idle_only_as_a_safe_regular_owner_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "exact-restore.json"
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is True

    _write_private_json(path, _parked_tombstone())
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is True

    path.chmod(0o644)
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is False
    path.chmod(0o600)

    linked = tmp_path / "linked.json"
    os.link(path, linked)
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is False
    linked.unlink()
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is True

    monkeypatch.setattr(lease_module.os, "geteuid", lambda: os.getuid() + 1)
    assert lease_module._terminal_parked_exact_restore_or_absent(path) is False


def test_exact_restore_tombstone_requires_nofollow_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "exact-restore.json"
    _write_private_json(path, _parked_tombstone())
    monkeypatch.delattr(lease_module.os, "O_NOFOLLOW")

    assert lease_module._terminal_parked_exact_restore_or_absent(path) is False


def test_exact_restore_tombstone_rejects_symlink_nonregular_and_inode_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.json"
    _write_private_json(target, _parked_tombstone())
    symlink = tmp_path / "symlink.json"
    symlink.symlink_to(target)
    assert lease_module._terminal_parked_exact_restore_or_absent(symlink) is False

    directory = tmp_path / "directory"
    directory.mkdir(mode=0o700)
    assert lease_module._terminal_parked_exact_restore_or_absent(directory) is False

    original_stat = Path.stat

    def changed_inode(selected: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        metadata = original_stat(selected, follow_symlinks=follow_symlinks)
        if selected == target and not follow_symlinks:
            fields = list(metadata)
            fields[1] += 1
            return os.stat_result(fields)
        return metadata

    monkeypatch.setattr(Path, "stat", changed_inode)
    assert lease_module._terminal_parked_exact_restore_or_absent(target) is False


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
