"""Deployment-wide safety paths shared by every physical-write process.

The hardware-test tools deliberately do not derive these paths from an instance name or from a
normal runtime state file.  Every sanctioned container mounts the same persistent volume at this
fixed root so an unfinished operation or emergency latch cannot be bypassed by selecting another
instance configuration.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

_HARDWARE_SAFETY_ROOT = Path("/hardware-safety")


class HardwareSafetyRootError(RuntimeError):
    """The deployment-wide persistent safety volume is absent or unsafe."""


def hardware_safety_root() -> Path:
    """Return the fixed deployment-wide physical-write safety root."""

    return _HARDWARE_SAFETY_ROOT


def validate_hardware_safety_root() -> None:
    """Prove the fixed root is a private, writable mount before discovery or connection."""

    root = hardware_safety_root()
    try:
        metadata = root.lstat()
    except OSError as error:
        raise HardwareSafetyRootError("shared hardware-safety mount is unavailable") from error
    if not stat.S_ISDIR(metadata.st_mode) or root.is_symlink():
        raise HardwareSafetyRootError("shared hardware-safety root must be a real directory")
    if not os.path.ismount(root):
        raise HardwareSafetyRootError("shared hardware-safety root is not a mounted volume")
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise HardwareSafetyRootError(
            "shared hardware-safety root must be owned by this process with mode 0700"
        )

    flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = -1
    probe_descriptor = -1
    probe_path: Path | None = None
    try:
        descriptor = os.open(root, flags)
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
            raise HardwareSafetyRootError("shared hardware-safety root changed during validation")
        probe_descriptor, probe_name = tempfile.mkstemp(prefix=".write-probe.", dir=root)
        probe_path = Path(probe_name)
        os.fchmod(probe_descriptor, 0o600)
        os.write(probe_descriptor, b"safety-volume-probe\n")
        os.fsync(probe_descriptor)
        os.close(probe_descriptor)
        probe_descriptor = -1
        probe_path.unlink()
        probe_path = None
        os.fsync(descriptor)
    except HardwareSafetyRootError:
        raise
    except OSError as error:
        raise HardwareSafetyRootError(
            "shared hardware-safety mount is not durably writable"
        ) from error
    finally:
        if probe_descriptor >= 0:
            os.close(probe_descriptor)
        if probe_path is not None:
            probe_path.unlink(missing_ok=True)
        if descriptor >= 0:
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


def qualification_directory() -> Path:
    return hardware_safety_root() / "qualifications"


__all__ = [
    "HardwareSafetyRootError",
    "emergency_stop_latch_path",
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
