import os
import stat
from pathlib import Path

import pytest

from jebao_flow import hardware_safety
from tests.unit.hardware_safety_support import (
    TEST_HARDWARE_SAFETY_IDENTITY_BYTES,
    TEST_HARDWARE_SAFETY_VOLUME_ID,
    install_test_hardware_safety_identity,
)


def _mounted_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    with_identity: bool = True,
) -> Path:
    root = tmp_path / "mounted"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    monkeypatch.setattr(hardware_safety, "_HARDWARE_SAFETY_ROOT", root)
    monkeypatch.setattr(hardware_safety.os.path, "ismount", lambda path: path == root)
    monkeypatch.setenv(
        hardware_safety.HARDWARE_SAFETY_VOLUME_ID_ENV,
        TEST_HARDWARE_SAFETY_VOLUME_ID,
    )
    if with_identity:
        install_test_hardware_safety_identity(root, monkeypatch)
    return root


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
    assert hardware_safety.exact_restore_journal_path() == root / "exact-restore.json"
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
    monkeypatch.setattr(
        hardware_safety,
        "_probe_hardware_safety_root",
        lambda _descriptor: pytest.fail("an unmounted image directory must not be probed"),
    )
    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="not a mounted volume"):
        hardware_safety.validate_hardware_safety_root()


def test_private_writable_mount_validation_leaves_no_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch)

    hardware_safety.validate_hardware_safety_root()

    assert {path.name for path in root.iterdir()} == {
        hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    }


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


def test_probe_is_dirfd_relative_and_detects_fixed_root_swap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch)
    detached = tmp_path / "detached-mounted"
    real_open = os.open
    swapped = False

    def swap_before_probe_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        if dir_fd is not None and str(path).startswith(".write-probe.") and not swapped:
            swapped = True
            root.rename(detached)
            root.mkdir(mode=0o700)
            os.chmod(root, 0o700)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_before_probe_open)

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="changed"):
        hardware_safety.validate_hardware_safety_root()

    assert swapped
    assert list(root.iterdir()) == []
    assert {path.name for path in detached.iterdir()} == {
        hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    }


@pytest.mark.parametrize(
    "configured_identity",
    [None, "", "A" * 32, "0" * 31, "0" * 33, "g" * 32],
)
def test_expected_identity_must_be_present_lowercase_and_exactly_32_hex(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    configured_identity: str | None,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch)
    if configured_identity is None:
        monkeypatch.delenv(hardware_safety.HARDWARE_SAFETY_VOLUME_ID_ENV)
    else:
        monkeypatch.setenv(
            hardware_safety.HARDWARE_SAFETY_VOLUME_ID_ENV,
            configured_identity,
        )
    monkeypatch.setattr(
        hardware_safety,
        "_probe_hardware_safety_root",
        lambda _descriptor: pytest.fail("invalid identity must be rejected before the probe"),
    )

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="identity"):
        hardware_safety.validate_hardware_safety_root()

    assert (root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME).read_bytes() == (
        TEST_HARDWARE_SAFETY_IDENTITY_BYTES
    )


@pytest.mark.parametrize(
    "payload",
    [
        None,
        b"",
        b'{"version":1',
        b'{"version":2,"volume_id":"0123456789abcdef0123456789abcdef"}\n',
        b'{"extra":0,"version":1,"volume_id":"0123456789abcdef0123456789abcdef"}\n',
        b'{"volume_id":"0123456789abcdef0123456789abcdef","version":1}\n',
        b'{"version":1,"volume_id":"fedcba9876543210fedcba9876543210"}\n',
    ],
)
def test_missing_or_noncanonical_identity_is_rejected_before_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: bytes | None,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch, with_identity=False)
    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    if payload is not None:
        marker.write_bytes(payload)
        marker.chmod(0o600)
    monkeypatch.setattr(
        hardware_safety,
        "_probe_hardware_safety_root",
        lambda _descriptor: pytest.fail("an invalid marker must not be probed"),
    )

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="identity marker"):
        hardware_safety.validate_hardware_safety_root()


@pytest.mark.parametrize("unsafe_kind", ["mode", "hardlink", "symlink", "fifo"])
def test_unsafe_identity_metadata_is_rejected_before_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    unsafe_kind: str,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch, with_identity=False)
    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    if unsafe_kind == "symlink":
        target = root / "identity-target"
        target.write_bytes(TEST_HARDWARE_SAFETY_IDENTITY_BYTES)
        target.chmod(0o600)
        marker.symlink_to(target.name)
    elif unsafe_kind == "fifo":
        os.mkfifo(marker, mode=0o600)
    else:
        marker.write_bytes(TEST_HARDWARE_SAFETY_IDENTITY_BYTES)
        marker.chmod(0o644 if unsafe_kind == "mode" else 0o600)
        if unsafe_kind == "hardlink":
            os.link(marker, root / "unexpected-marker-link")
    monkeypatch.setattr(
        hardware_safety,
        "_probe_hardware_safety_root",
        lambda _descriptor: pytest.fail("unsafe marker metadata must precede the probe"),
    )

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="identity marker"):
        hardware_safety.validate_hardware_safety_root()


def test_identity_owner_mismatch_is_rejected_before_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch)
    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    marker_identity = (marker.stat().st_dev, marker.stat().st_ino)
    real_fstat = os.fstat

    def wrong_marker_owner(descriptor: int) -> os.stat_result:
        metadata = real_fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == marker_identity:
            values = list(metadata)
            values[4] = os.geteuid() + 1
            return os.stat_result(values)
        return metadata

    monkeypatch.setattr(os, "fstat", wrong_marker_owner)
    monkeypatch.setattr(
        hardware_safety,
        "_probe_hardware_safety_root",
        lambda _descriptor: pytest.fail("wrong marker ownership must precede the probe"),
    )

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="unsafe metadata"):
        hardware_safety.validate_hardware_safety_root()


def test_identity_on_another_filesystem_is_rejected_before_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch)
    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    marker_identity = (marker.stat().st_dev, marker.stat().st_ino)
    foreign_device = marker_identity[0] + 1
    real_fstat = os.fstat
    real_stat = os.stat

    def with_foreign_device(metadata: os.stat_result) -> os.stat_result:
        values = list(metadata)
        values[2] = foreign_device
        return os.stat_result(values)

    def foreign_marker_fstat(descriptor: int) -> os.stat_result:
        metadata = real_fstat(descriptor)
        if (metadata.st_dev, metadata.st_ino) == marker_identity:
            return with_foreign_device(metadata)
        return metadata

    def foreign_marker_stat(
        path,
        *,
        dir_fd=None,
        follow_symlinks=True,
    ) -> os.stat_result:
        metadata = real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if (
            dir_fd is not None
            and path == hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
            and (metadata.st_dev, metadata.st_ino) == marker_identity
        ):
            return with_foreign_device(metadata)
        return metadata

    monkeypatch.setattr(os, "fstat", foreign_marker_fstat)
    monkeypatch.setattr(os, "stat", foreign_marker_stat)
    monkeypatch.setattr(
        hardware_safety,
        "_probe_hardware_safety_root",
        lambda _descriptor: pytest.fail("a foreign marker mount must precede the probe"),
    )

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="shared mount"):
        hardware_safety.validate_hardware_safety_root()


def test_provision_requires_expected_identity_before_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch, with_identity=False)
    monkeypatch.delenv(hardware_safety.HARDWARE_SAFETY_VOLUME_ID_ENV)
    monkeypatch.setattr(
        hardware_safety,
        "_probe_hardware_safety_root",
        lambda _descriptor: pytest.fail("an absent expected identity must precede the probe"),
    )

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="not configured"):
        hardware_safety.provision_hardware_safety_volume_identity()

    assert list(root.iterdir()) == []


def test_runtime_rechecks_marker_after_the_writability_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch)
    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    conflicting = b'{"version":1,"volume_id":"fedcba9876543210fedcba9876543210"}\n'
    real_probe = hardware_safety._probe_hardware_safety_root

    def probe_then_change_marker(descriptor: int) -> None:
        real_probe(descriptor)
        marker.write_bytes(conflicting)
        marker.chmod(0o600)

    monkeypatch.setattr(hardware_safety, "_probe_hardware_safety_root", probe_then_change_marker)

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="expected identity"):
        hardware_safety.validate_hardware_safety_root()

    assert {path.name for path in root.iterdir()} == {marker.name}


def test_provision_installs_canonical_private_marker_without_temporary_residue(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch, with_identity=False)

    assert hardware_safety.provision_hardware_safety_volume_identity()

    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    assert marker.read_bytes() == TEST_HARDWARE_SAFETY_IDENTITY_BYTES
    assert stat.S_IMODE(marker.stat().st_mode) == 0o600
    assert marker.stat().st_nlink == 1
    assert {path.name for path in root.iterdir()} == {marker.name}


def test_provision_exact_replay_is_idempotent_and_preserves_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch)
    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    before = (marker.stat().st_dev, marker.stat().st_ino)

    assert not hardware_safety.provision_hardware_safety_volume_identity()

    assert (marker.stat().st_dev, marker.stat().st_ino) == before
    assert marker.read_bytes() == TEST_HARDWARE_SAFETY_IDENTITY_BYTES


def test_provision_never_probes_or_replaces_a_conflicting_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch, with_identity=False)
    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    conflicting = b'{"version":1,"volume_id":"fedcba9876543210fedcba9876543210"}\n'
    marker.write_bytes(conflicting)
    marker.chmod(0o600)
    before = (marker.stat().st_dev, marker.stat().st_ino)
    monkeypatch.setattr(
        hardware_safety,
        "_probe_hardware_safety_root",
        lambda _descriptor: pytest.fail("a conflicting marker must not be probed"),
    )

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="expected identity"):
        hardware_safety.provision_hardware_safety_volume_identity()

    assert marker.read_bytes() == conflicting
    assert (marker.stat().st_dev, marker.stat().st_ino) == before


def test_interrupted_link_install_is_rejected_by_runtime_and_repaired_only_by_provision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch)
    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    linked_temporary = root / (
        f"{hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME}.{'a' * 24}.tmp"
    )
    os.link(marker, linked_temporary)
    unrelated_temporary = root / (
        f"{hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME}.{'b' * 24}.tmp"
    )
    unrelated_temporary.write_bytes(TEST_HARDWARE_SAFETY_IDENTITY_BYTES)
    unrelated_temporary.chmod(0o600)
    unrelated = root / "unrelated-evidence"
    unrelated.write_text("keep", encoding="ascii")
    calls = 0
    real_probe = hardware_safety._probe_hardware_safety_root

    def counted_probe(descriptor: int) -> None:
        nonlocal calls
        calls += 1
        real_probe(descriptor)

    monkeypatch.setattr(hardware_safety, "_probe_hardware_safety_root", counted_probe)

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="unsafe metadata"):
        hardware_safety.validate_hardware_safety_root()
    assert calls == 0

    assert not hardware_safety.provision_hardware_safety_volume_identity()

    assert calls == 1
    assert marker.stat().st_nlink == 1
    assert not linked_temporary.exists()
    assert unrelated_temporary.exists()
    assert unrelated.read_text(encoding="ascii") == "keep"


def test_unexplained_marker_link_is_preserved_and_rejected_by_provision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch)
    marker = root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME
    unexplained = root / "not-an-identity-temporary"
    os.link(marker, unexplained)
    monkeypatch.setattr(
        hardware_safety,
        "_probe_hardware_safety_root",
        lambda _descriptor: pytest.fail("unexplained links must not reach the probe"),
    )

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="extra links"):
        hardware_safety.provision_hardware_safety_volume_identity()

    assert marker.stat().st_nlink == 2
    assert unexplained.exists()


def test_provision_install_order_is_file_fsync_link_dir_unlink_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mounted_root(tmp_path, monkeypatch, with_identity=False)
    events: list[str] = []
    temporary_descriptors: set[int] = set()
    real_open = os.open
    real_close = os.close
    real_fsync = os.fsync
    real_link = os.link
    real_unlink = os.unlink

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if hardware_safety._HARDWARE_SAFETY_IDENTITY_TEMP_PATTERN.fullmatch(str(path)):
            temporary_descriptors.add(descriptor)
        return descriptor

    def tracked_fsync(descriptor: int) -> None:
        if descriptor in temporary_descriptors:
            events.append("temporary_fsync")
        elif stat.S_ISDIR(os.fstat(descriptor).st_mode):
            events.append("directory_fsync")
        real_fsync(descriptor)

    def tracked_close(descriptor: int) -> None:
        if descriptor in temporary_descriptors:
            events.append("temporary_close")
            temporary_descriptors.remove(descriptor)
        real_close(descriptor)

    def tracked_link(src, dst, **kwargs) -> None:
        events.append("link")
        real_link(src, dst, **kwargs)

    def tracked_unlink(path, **kwargs) -> None:
        if hardware_safety._HARDWARE_SAFETY_IDENTITY_TEMP_PATTERN.fullmatch(str(path)):
            events.append("temporary_unlink")
        real_unlink(path, **kwargs)

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "close", tracked_close)
    monkeypatch.setattr(os, "fsync", tracked_fsync)
    monkeypatch.setattr(os, "link", tracked_link)
    monkeypatch.setattr(os, "unlink", tracked_unlink)

    assert hardware_safety.provision_hardware_safety_volume_identity()

    start = events.index("temporary_fsync")
    assert events[start : start + 6] == [
        "temporary_fsync",
        "temporary_close",
        "link",
        "directory_fsync",
        "temporary_unlink",
        "directory_fsync",
    ]


def test_partial_temporary_write_never_publishes_a_final_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _mounted_root(tmp_path, monkeypatch, with_identity=False)
    temporary_descriptors: set[int] = set()
    real_open = os.open
    real_write = os.write

    def tracked_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = real_open(path, flags, mode, dir_fd=dir_fd)
        if hardware_safety._HARDWARE_SAFETY_IDENTITY_TEMP_PATTERN.fullmatch(str(path)):
            temporary_descriptors.add(descriptor)
        return descriptor

    def fail_identity_write(descriptor: int, payload) -> int:
        if descriptor in temporary_descriptors:
            real_write(descriptor, bytes(payload[:5]))
            raise OSError("injected partial identity write")
        return real_write(descriptor, payload)

    monkeypatch.setattr(os, "open", tracked_open)
    monkeypatch.setattr(os, "write", fail_identity_write)

    with pytest.raises(hardware_safety.HardwareSafetyRootError, match="install"):
        hardware_safety.provision_hardware_safety_volume_identity()

    assert not (root / hardware_safety.HARDWARE_SAFETY_IDENTITY_FILENAME).exists()
