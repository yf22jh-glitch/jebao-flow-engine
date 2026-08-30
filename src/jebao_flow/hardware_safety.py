"""Deployment-wide safety paths shared by every physical-write process.

The hardware-test tools deliberately do not derive these paths from an instance name or from a
normal runtime state file.  Every sanctioned container mounts the same persistent volume at this
fixed root so an unfinished operation or emergency latch cannot be bypassed by selecting another
instance configuration.
"""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path

_HARDWARE_SAFETY_ROOT = Path("/hardware-safety")


class HardwareSafetyRootError(RuntimeError):
    """The deployment-wide persistent safety volume is absent or unsafe."""


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


def _validate_hardware_safety_root_descriptor(descriptor: int) -> None:
    """Bind an open root descriptor to the still-mounted fixed pathname."""

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


def _open_hardware_safety_root() -> int:
    """Open, durably probe, and return the fixed root as a retained capability."""

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


def validate_hardware_safety_root() -> None:
    """Prove the fixed root is a private, durably writable mounted directory."""

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
    "HardwareSafetyRootError",
    "emergency_stop_latch_path",
    "exact_restore_journal_path",
    "global_operation_lock_path",
    "hardware_safety_root",
    "native_linkage_intent_path",
    "native_linkage_journal_path",
    "physical_lock_directory",
    "qualification_directory",
    "schedule_linkage_intent_path",
    "schedule_linkage_journal_path",
    "temporary_schedule_journal_path",
    "verification_intent_path",
    "verification_journal_path",
    "validate_hardware_safety_root",
]
