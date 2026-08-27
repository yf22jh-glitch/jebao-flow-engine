import os
from pathlib import Path

import pytest

from jebao_flow import hardware_safety


def test_every_operation_uses_one_deployment_wide_safety_root(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "shared-hardware-safety"
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)

    assert hardware_safety.native_linkage_journal_path() == root / "native-linkage.json"
    assert hardware_safety.native_linkage_intent_path() == root / "native-linkage-intent.json"
    assert hardware_safety.emergency_stop_latch_path() == root / "emergency-stop.latch"
    assert hardware_safety.physical_lock_directory() == root / "physical-locks"
    assert hardware_safety.verification_journal_path() == root / "device-verification.json"
    assert hardware_safety.verification_intent_path() == root / "device-verification-intent.json"
    assert hardware_safety.temporary_schedule_journal_path() == root / "temporary-schedule.json"
    assert hardware_safety.qualification_directory() == root / "qualifications"


def test_safety_paths_never_depend_on_instance_or_runtime_state() -> None:
    assert hardware_safety.hardware_safety_root() == Path("/hardware-safety")


def test_missing_or_unmounted_safety_root_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "not-mounted"
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="unavailable"):
        hardware_safety.validate_hardware_safety_root()

    root.mkdir(mode=0o700)
    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="not a mounted volume"):
        hardware_safety.validate_hardware_safety_root()


def test_private_writable_mount_validation_leaves_no_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "mounted"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda path: path == root)

    hardware_safety.validate_hardware_safety_root()

    assert list(root.iterdir()) == []


def test_group_writable_safety_mount_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "mounted"
    root.mkdir(mode=0o770)
    os.chmod(root, 0o770)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda path: path == root)

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="mode 0700"):
        hardware_safety.validate_hardware_safety_root()
