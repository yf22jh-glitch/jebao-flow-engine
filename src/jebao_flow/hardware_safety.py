"""Deployment-wide safety paths shared by every physical-write process.

The hardware-test tools deliberately do not derive these paths from an instance name or from a
normal runtime state file.  Every sanctioned container mounts the same persistent volume at this
fixed root so an unfinished operation or emergency latch cannot be bypassed by selecting another
instance configuration.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
from pathlib import Path

_HARDWARE_SAFETY_ROOT = Path("/hardware-safety")
HARDWARE_SAFETY_VOLUME_ID_ENV = "JEBAO_FLOW_HARDWARE_SAFETY_VOLUME_ID"
HARDWARE_SAFETY_IDENTITY_FILENAME = ".hardware-safety-identity"
_HARDWARE_SAFETY_IDENTITY_VERSION = 1
_HARDWARE_SAFETY_IDENTITY_MAX_BYTES = 128
_HARDWARE_SAFETY_VOLUME_ID_PATTERN = re.compile(r"[0-9a-f]{32}")
_HARDWARE_SAFETY_IDENTITY_TEMP_PATTERN = re.compile(
    rf"{re.escape(HARDWARE_SAFETY_IDENTITY_FILENAME)}\.[0-9a-f]{{24}}\.tmp"
)


class HardwareSafetyRootError(RuntimeError):
    """The deployment-wide persistent safety volume is absent or unsafe."""


class _HardwareSafetyIdentityMissing(HardwareSafetyRootError):
    """The mounted root has not been explicitly provisioned with an identity."""


def hardware_safety_root() -> Path:
    """Return the fixed deployment-wide physical-write safety root."""

    return _HARDWARE_SAFETY_ROOT


def _require_safe_hardware_safety_root_metadata(metadata: os.stat_result) -> None:
    if not stat.S_ISDIR(metadata.st_mode):
        raise HardwareSafetyRootError("shared hardware-safety root must be a real directory")
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise HardwareSafetyRootError(
            "shared hardware-safety root must be owned by this process with mode 0700"
        )


def _validate_hardware_safety_mount_descriptor(descriptor: int) -> None:
    """Bind an open descriptor to the still-mounted fixed pathname and metadata."""

    root = hardware_safety_root()
    try:
        opened = os.fstat(descriptor)
        named = root.lstat()
    except OSError as error:
        raise HardwareSafetyRootError("shared hardware-safety mount is unavailable") from error

    _require_safe_hardware_safety_root_metadata(opened)
    _require_safe_hardware_safety_root_metadata(named)
    if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
        raise HardwareSafetyRootError("shared hardware-safety root changed during validation")
    if not os.path.ismount(root):
        raise HardwareSafetyRootError("shared hardware-safety root is not a mounted volume")

    # Close the ismount check's pathname race before the descriptor is trusted by a caller.
    try:
        current = root.lstat()
    except OSError as error:
        raise HardwareSafetyRootError("shared hardware-safety mount is unavailable") from error
    _require_safe_hardware_safety_root_metadata(current)
    if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        raise HardwareSafetyRootError("shared hardware-safety root changed during validation")


def _expected_hardware_safety_volume_id() -> str:
    value = os.environ.get(HARDWARE_SAFETY_VOLUME_ID_ENV)
    if value is None:
        raise HardwareSafetyRootError("shared hardware-safety volume identity is not configured")
    if _HARDWARE_SAFETY_VOLUME_ID_PATTERN.fullmatch(value) is None:
        raise HardwareSafetyRootError("shared hardware-safety volume identity is invalid")
    return value


def _canonical_hardware_safety_identity(volume_id: str) -> bytes:
    return (
        json.dumps(
            {
                "version": _HARDWARE_SAFETY_IDENTITY_VERSION,
                "volume_id": volume_id,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )


def _require_safe_identity_metadata(
    metadata: os.stat_result,
    *,
    allow_extra_links: bool,
) -> None:
    links_are_safe = metadata.st_nlink >= 1 if allow_extra_links else metadata.st_nlink == 1
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or not links_are_safe
        or metadata.st_size > _HARDWARE_SAFETY_IDENTITY_MAX_BYTES
    ):
        raise HardwareSafetyRootError("shared hardware-safety identity marker has unsafe metadata")


def _read_identity_payload(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    remaining = _HARDWARE_SAFETY_IDENTITY_MAX_BYTES + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(remaining, 4096))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if len(payload) > _HARDWARE_SAFETY_IDENTITY_MAX_BYTES:
        raise HardwareSafetyRootError("shared hardware-safety identity marker is too large")
    return payload


def _open_hardware_safety_identity_marker(
    root_descriptor: int,
    expected_volume_id: str,
    *,
    allow_extra_links: bool = False,
) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    try:
        try:
            descriptor = os.open(
                HARDWARE_SAFETY_IDENTITY_FILENAME,
                flags,
                dir_fd=root_descriptor,
            )
        except FileNotFoundError as error:
            raise _HardwareSafetyIdentityMissing(
                "shared hardware-safety identity marker is missing"
            ) from error

        root_metadata = os.fstat(root_descriptor)
        opened = os.fstat(descriptor)
        named = os.stat(
            HARDWARE_SAFETY_IDENTITY_FILENAME,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        _require_safe_identity_metadata(opened, allow_extra_links=allow_extra_links)
        _require_safe_identity_metadata(named, allow_extra_links=allow_extra_links)
        identity = (opened.st_dev, opened.st_ino)
        if identity != (named.st_dev, named.st_ino):
            raise HardwareSafetyRootError(
                "shared hardware-safety identity marker changed while opening"
            )
        if opened.st_dev != root_metadata.st_dev or named.st_dev != root_metadata.st_dev:
            raise HardwareSafetyRootError(
                "shared hardware-safety identity marker is not stored on the shared mount"
            )

        payload = _read_identity_payload(descriptor)
        after = os.fstat(descriptor)
        current = os.stat(
            HARDWARE_SAFETY_IDENTITY_FILENAME,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
        _require_safe_identity_metadata(after, allow_extra_links=allow_extra_links)
        _require_safe_identity_metadata(current, allow_extra_links=allow_extra_links)
        if identity != (after.st_dev, after.st_ino) or identity != (
            current.st_dev,
            current.st_ino,
        ):
            raise HardwareSafetyRootError(
                "shared hardware-safety identity marker changed while reading"
            )
        expected = _canonical_hardware_safety_identity(expected_volume_id)
        if not secrets.compare_digest(payload, expected):
            raise HardwareSafetyRootError(
                "shared hardware-safety identity marker does not match the expected identity"
            )

        retained = descriptor
        descriptor = -1
        return retained, after
    except (_HardwareSafetyIdentityMissing, HardwareSafetyRootError):
        raise
    except OSError as error:
        raise HardwareSafetyRootError(
            "shared hardware-safety identity marker is unavailable"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_hardware_safety_identity_marker(
    root_descriptor: int,
    expected_volume_id: str | None = None,
) -> str:
    expected = (
        _expected_hardware_safety_volume_id() if expected_volume_id is None else expected_volume_id
    )
    descriptor, _metadata = _open_hardware_safety_identity_marker(
        root_descriptor,
        expected,
    )
    os.close(descriptor)
    return expected


def _validate_hardware_safety_root_descriptor(descriptor: int) -> None:
    """Bind an open root descriptor to its fixed mount and provisioned identity."""

    _validate_hardware_safety_mount_descriptor(descriptor)
    _validate_hardware_safety_identity_marker(descriptor)
    _validate_hardware_safety_mount_descriptor(descriptor)


def _validate_probe_file(
    root_descriptor: int,
    probe_descriptor: int,
    probe_name: str,
) -> None:
    try:
        opened = os.fstat(probe_descriptor)
        named = os.stat(probe_name, dir_fd=root_descriptor, follow_symlinks=False)
    except OSError as error:
        raise HardwareSafetyRootError("shared hardware-safety probe changed") from error
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(named.st_mode)
        or opened.st_uid != os.geteuid()
        or named.st_uid != os.geteuid()
        or stat.S_IMODE(opened.st_mode) != 0o600
        or stat.S_IMODE(named.st_mode) != 0o600
        or opened.st_nlink != 1
        or named.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
    ):
        raise HardwareSafetyRootError("shared hardware-safety probe has unsafe metadata")


def _probe_hardware_safety_root(root_descriptor: int) -> None:
    probe_descriptor = -1
    probe_name: str | None = None
    try:
        for _ in range(128):
            candidate = f".write-probe.{secrets.token_hex(12)}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            try:
                probe_descriptor = os.open(
                    candidate,
                    flags,
                    0o600,
                    dir_fd=root_descriptor,
                )
            except FileExistsError:
                continue
            probe_name = candidate
            break
        else:  # pragma: no cover - cryptographically improbable without injected faults
            raise HardwareSafetyRootError("cannot allocate shared hardware-safety probe")

        os.fchmod(probe_descriptor, 0o600)
        _validate_probe_file(root_descriptor, probe_descriptor, probe_name)
        pending = memoryview(b"safety-volume-probe\n")
        while pending:
            written = os.write(probe_descriptor, pending)
            if written <= 0:
                raise OSError("short write while validating shared hardware-safety mount")
            pending = pending[written:]
        os.fsync(probe_descriptor)
        _validate_probe_file(root_descriptor, probe_descriptor, probe_name)
        os.close(probe_descriptor)
        probe_descriptor = -1
        os.unlink(probe_name, dir_fd=root_descriptor)
        probe_name = None
        os.fsync(root_descriptor)
    except HardwareSafetyRootError:
        raise
    except OSError as error:
        raise HardwareSafetyRootError(
            "shared hardware-safety mount is not durably writable"
        ) from error
    finally:
        if probe_descriptor >= 0:
            try:
                os.close(probe_descriptor)
            except OSError:
                pass
        if probe_name is not None:
            try:
                os.unlink(probe_name, dir_fd=root_descriptor)
            except OSError:
                pass


def _open_hardware_safety_mount() -> int:
    if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise HardwareSafetyRootError(
            "safe shared hardware-safety access requires O_NOFOLLOW and O_DIRECTORY"
        )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    descriptor = -1
    try:
        descriptor = os.open(hardware_safety_root(), flags)
        _validate_hardware_safety_mount_descriptor(descriptor)
        retained = descriptor
        descriptor = -1
        return retained
    except HardwareSafetyRootError:
        raise
    except OSError as error:
        raise HardwareSafetyRootError("shared hardware-safety mount is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _identity_marker_exists(root_descriptor: int) -> bool:
    try:
        os.stat(
            HARDWARE_SAFETY_IDENTITY_FILENAME,
            dir_fd=root_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return False
    except OSError as error:
        raise HardwareSafetyRootError(
            "shared hardware-safety identity marker is unavailable"
        ) from error
    return True


def _validate_named_identity_file(
    root_descriptor: int,
    descriptor: int,
    name: str,
    *,
    expected_size: int,
) -> os.stat_result:
    try:
        opened = os.fstat(descriptor)
        named = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
    except OSError as error:
        raise HardwareSafetyRootError(
            "shared hardware-safety identity temporary changed"
        ) from error
    _require_safe_identity_metadata(opened, allow_extra_links=False)
    _require_safe_identity_metadata(named, allow_extra_links=False)
    if (
        (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        or opened.st_size != expected_size
        or named.st_size != expected_size
    ):
        raise HardwareSafetyRootError("shared hardware-safety identity temporary changed")
    return opened


def _write_identity_temporary(root_descriptor: int, payload: bytes) -> str:
    descriptor = -1
    temporary_name = ""
    for _ in range(128):
        temporary_name = f"{HARDWARE_SAFETY_IDENTITY_FILENAME}.{secrets.token_hex(12)}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=root_descriptor,
            )
            break
        except FileExistsError:
            continue
    else:  # pragma: no cover - cryptographically improbable without injected faults
        raise HardwareSafetyRootError("cannot allocate shared hardware-safety identity temporary")

    try:
        os.fchmod(descriptor, 0o600)
        _validate_named_identity_file(
            root_descriptor,
            descriptor,
            temporary_name,
            expected_size=0,
        )
        pending = memoryview(payload)
        while pending:
            written = os.write(descriptor, pending)
            if written <= 0:
                raise OSError("short write while persisting hardware-safety identity")
            pending = pending[written:]
        os.fsync(descriptor)
        _validate_named_identity_file(
            root_descriptor,
            descriptor,
            temporary_name,
            expected_size=len(payload),
        )
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        try:
            os.unlink(temporary_name, dir_fd=root_descriptor)
        except OSError:
            pass
        raise
    try:
        os.close(descriptor)
    except OSError:
        try:
            os.unlink(temporary_name, dir_fd=root_descriptor)
        except OSError:
            pass
        raise
    return temporary_name


def _repair_interrupted_identity_install(
    root_descriptor: int,
    marker_identity: tuple[int, int],
) -> None:
    removed = False
    try:
        _validate_hardware_safety_mount_descriptor(root_descriptor)
        names = os.listdir(root_descriptor)
        for name in names:
            if _HARDWARE_SAFETY_IDENTITY_TEMP_PATTERN.fullmatch(name) is None:
                continue
            try:
                candidate = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if (candidate.st_dev, candidate.st_ino) != marker_identity:
                continue

            final_now = os.stat(
                HARDWARE_SAFETY_IDENTITY_FILENAME,
                dir_fd=root_descriptor,
                follow_symlinks=False,
            )
            candidate_now = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
            if (final_now.st_dev, final_now.st_ino) != marker_identity:
                raise HardwareSafetyRootError(
                    "shared hardware-safety identity marker changed during recovery"
                )
            if (candidate_now.st_dev, candidate_now.st_ino) != marker_identity:
                continue
            _validate_hardware_safety_mount_descriptor(root_descriptor)
            os.unlink(name, dir_fd=root_descriptor)
            removed = True
        if not removed:
            raise HardwareSafetyRootError(
                "shared hardware-safety identity marker has unexplained extra links"
            )
        os.fsync(root_descriptor)
        _validate_hardware_safety_mount_descriptor(root_descriptor)
    except HardwareSafetyRootError:
        raise
    except OSError as error:
        raise HardwareSafetyRootError(
            "cannot finish interrupted hardware-safety identity installation"
        ) from error


def _confirm_existing_hardware_safety_identity(
    root_descriptor: int,
    expected_volume_id: str,
) -> None:
    descriptor = -1
    try:
        _validate_hardware_safety_mount_descriptor(root_descriptor)
        descriptor, metadata = _open_hardware_safety_identity_marker(
            root_descriptor,
            expected_volume_id,
            allow_extra_links=True,
        )
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        if metadata.st_nlink > 1:
            _repair_interrupted_identity_install(
                root_descriptor,
                (metadata.st_dev, metadata.st_ino),
            )
        _validate_hardware_safety_mount_descriptor(root_descriptor)
        os.fsync(root_descriptor)
        _validate_hardware_safety_mount_descriptor(root_descriptor)
        _validate_hardware_safety_identity_marker(root_descriptor, expected_volume_id)
    except HardwareSafetyRootError:
        raise
    except OSError as error:
        raise HardwareSafetyRootError(
            "cannot confirm existing hardware-safety identity durably"
        ) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _install_hardware_safety_identity(
    root_descriptor: int,
    expected_volume_id: str,
) -> None:
    payload = _canonical_hardware_safety_identity(expected_volume_id)
    temporary_name: str | None = None
    try:
        temporary_name = _write_identity_temporary(root_descriptor, payload)
        _validate_hardware_safety_mount_descriptor(root_descriptor)
        try:
            os.link(
                temporary_name,
                HARDWARE_SAFETY_IDENTITY_FILENAME,
                src_dir_fd=root_descriptor,
                dst_dir_fd=root_descriptor,
                follow_symlinks=False,
            )
        except FileExistsError as error:
            raise HardwareSafetyRootError(
                "shared hardware-safety identity marker already exists"
            ) from error
        _validate_hardware_safety_mount_descriptor(root_descriptor)
        os.fsync(root_descriptor)
        _validate_hardware_safety_mount_descriptor(root_descriptor)
        os.unlink(temporary_name, dir_fd=root_descriptor)
        temporary_name = None
        os.fsync(root_descriptor)
        _validate_hardware_safety_mount_descriptor(root_descriptor)
        _validate_hardware_safety_identity_marker(root_descriptor, expected_volume_id)
    except HardwareSafetyRootError:
        raise
    except OSError as error:
        raise HardwareSafetyRootError("cannot install hardware-safety identity durably") from error
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name, dir_fd=root_descriptor)
                os.fsync(root_descriptor)
            except OSError:
                pass


def _open_hardware_safety_root() -> int:
    """Open, identify, durably probe, and retain the fixed root capability."""

    descriptor = _open_hardware_safety_mount()
    try:
        _validate_hardware_safety_root_descriptor(descriptor)
        _probe_hardware_safety_root(descriptor)
        _validate_hardware_safety_root_descriptor(descriptor)
        retained = descriptor
        descriptor = -1
        return retained
    except HardwareSafetyRootError:
        raise
    except OSError as error:
        raise HardwareSafetyRootError("shared hardware-safety mount is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def provision_hardware_safety_volume_identity() -> bool:
    """Install or durably confirm the approved identity without replacing any marker."""

    root_descriptor = _open_hardware_safety_mount()
    try:
        expected_volume_id = _expected_hardware_safety_volume_id()
        if _identity_marker_exists(root_descriptor):
            _confirm_existing_hardware_safety_identity(root_descriptor, expected_volume_id)
            _validate_hardware_safety_root_descriptor(root_descriptor)
            _probe_hardware_safety_root(root_descriptor)
            _validate_hardware_safety_root_descriptor(root_descriptor)
            return False

        _probe_hardware_safety_root(root_descriptor)
        _validate_hardware_safety_mount_descriptor(root_descriptor)
        _install_hardware_safety_identity(root_descriptor, expected_volume_id)
        _validate_hardware_safety_root_descriptor(root_descriptor)
        return True
    finally:
        os.close(root_descriptor)


def hardware_safety_volume_identity_sha256() -> str:
    """Return only a public digest of the configured private deployment identity."""

    return hashlib.sha256(_expected_hardware_safety_volume_id().encode("ascii")).hexdigest()


def validate_hardware_safety_root() -> None:
    """Prove the fixed root is the identified, private, durably writable mount."""

    descriptor = _open_hardware_safety_root()
    os.close(descriptor)


def native_linkage_journal_path() -> Path:
    return hardware_safety_root() / "native-linkage.json"


def native_linkage_intent_path() -> Path:
    return hardware_safety_root() / "native-linkage-intent.json"


def emergency_stop_latch_path() -> Path:
    return hardware_safety_root() / "emergency-stop.latch"


def physical_lock_directory() -> Path:
    return hardware_safety_root() / "physical-locks"


def global_operation_lock_path() -> Path:
    """Return the single deployment-wide lease shared by every write workflow."""

    return hardware_safety_root() / "hardware-operation.lock"


def verification_journal_path() -> Path:
    return hardware_safety_root() / "device-verification.json"


def verification_intent_path() -> Path:
    return hardware_safety_root() / "device-verification-intent.json"


def schedule_linkage_journal_path() -> Path:
    """Return the fixed, deployment-wide role-only schedule journal path."""

    return hardware_safety_root() / "schedule-linkage.json"


def schedule_linkage_intent_path() -> Path:
    """Return the fixed, deployment-wide schedule diagnostic intent path."""

    return hardware_safety_root() / "schedule-linkage-intent.json"


def temporary_schedule_journal_path() -> Path:
    """Return the fixed byte-exact schedule restore journal path."""

    return hardware_safety_root() / "temporary-schedule.json"


def exact_restore_journal_path() -> Path:
    """Return the attended standalone exact-restore journal path."""

    return hardware_safety_root() / "exact-restore.json"


def qualification_directory() -> Path:
    return hardware_safety_root() / "qualifications"


__all__ = [
    "HARDWARE_SAFETY_IDENTITY_FILENAME",
    "HARDWARE_SAFETY_VOLUME_ID_ENV",
    "HardwareSafetyRootError",
    "emergency_stop_latch_path",
    "exact_restore_journal_path",
    "global_operation_lock_path",
    "hardware_safety_root",
    "hardware_safety_volume_identity_sha256",
    "native_linkage_intent_path",
    "native_linkage_journal_path",
    "physical_lock_directory",
    "provision_hardware_safety_volume_identity",
    "qualification_directory",
    "schedule_linkage_intent_path",
    "schedule_linkage_journal_path",
    "temporary_schedule_journal_path",
    "verification_intent_path",
    "verification_journal_path",
    "validate_hardware_safety_root",
]
