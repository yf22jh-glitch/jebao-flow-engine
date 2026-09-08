"""Shared deployment and stable-device leases for local-control writes."""

from __future__ import annotations

import fcntl
import json
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from jebao_flow.exact_restore_guard import ExactRestoreGuard
from jebao_flow.hardware_safety import (
    exact_restore_journal_path,
    native_linkage_intent_path,
    native_linkage_journal_path,
    physical_lock_directory,
    schedule_linkage_intent_path,
    schedule_linkage_journal_path,
    temporary_schedule_journal_path,
    verification_intent_path,
    verification_journal_path,
)

_MAX_SAFETY_ARTIFACT_BYTES = 1024 * 1024


class LocalControlLeaseError(RuntimeError):
    """The shared local-control write lease could not be established safely."""


class LocalControlLeaseBusyError(LocalControlLeaseError):
    """Another workflow or unfinished exact-restore state owns write admission."""


class ActiveLocalControlLease:
    """Capability checked immediately before every physical send."""

    def __init__(self, guard: ExactRestoreGuard) -> None:
        self._guard = guard
        self._epoch = guard.epoch

    def require_permitted(self) -> None:
        if not self._guard.permitted or self._guard.epoch != self._epoch:
            raise LocalControlLeaseError("local-control write lease is no longer permitted")


class LocalControlLease:
    """Acquire the existing global lease followed by stable per-device locks."""

    def __init__(
        self,
        *,
        guard: ExactRestoreGuard | None = None,
        device_lock_directory: Path | None = None,
        legacy_workflows_idle_probe: Callable[[], bool] | None = None,
    ) -> None:
        self._guard = guard or ExactRestoreGuard()
        self._device_lock_directory = (
            physical_lock_directory()
            if device_lock_directory is None
            else Path(device_lock_directory)
        )
        self._legacy_workflows_idle_probe = (
            _legacy_write_workflows_idle
            if legacy_workflows_idle_probe is None
            else legacy_workflows_idle_probe
        )

    @contextmanager
    def acquire(self, physical_identity_keys: tuple[str, ...]) -> Iterator[ActiveLocalControlLease]:
        keys = tuple(sorted(physical_identity_keys))
        if not keys or len(keys) != len(set(keys)):
            raise ValueError("local-control lease requires distinct physical identities")
        if any(
            len(key) != 64
            or any(character not in "0123456789abcdef" for character in key)
            for key in keys
        ):
            raise ValueError("local-control lease keys must be lowercase SHA-256")

        with ExitStack() as stack:
            try:
                stack.enter_context(self._guard.lease())
            except Exception as error:
                raise LocalControlLeaseError(
                    "local-control global lease acquisition failed"
                ) from error
            self._guard.clear()
            if not self._guard.permitted:
                raise LocalControlLeaseError("persistent emergency stop blocks local control")
            if not self._legacy_workflows_idle_probe():
                raise LocalControlLeaseBusyError(
                    "another durable write workflow is unfinished"
                )
            stack.enter_context(_acquire_device_locks(self._device_lock_directory, keys))
            active = ActiveLocalControlLease(self._guard)
            active.require_permitted()
            yield active


def _legacy_write_workflows_idle() -> bool:
    """Reject every live journal and every malformed or non-terminal durable intent."""

    journal_paths = (
        native_linkage_journal_path(),
        verification_journal_path(),
        schedule_linkage_journal_path(),
        temporary_schedule_journal_path(),
        exact_restore_journal_path(),
    )
    if any(not _artifact_absent(path) for path in journal_paths):
        return False
    return all(
        _terminal_intent_or_absent(path)
        for path in (
            native_linkage_intent_path(),
            verification_intent_path(),
            schedule_linkage_intent_path(),
        )
    )


def _artifact_absent(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    return False


def _terminal_intent_or_absent(path: Path) -> bool:
    """Inspect only the common terminal envelope without importing a frozen harness."""

    if not hasattr(os, "O_NOFOLLOW"):
        return False
    try:
        named = path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if (
        path.is_symlink()
        or not stat.S_ISREG(named.st_mode)
        or named.st_uid != os.geteuid()
        or stat.S_IMODE(named.st_mode) != 0o600
        or named.st_nlink != 1
        or named.st_size > _MAX_SAFETY_ARTIFACT_BYTES
    ):
        return False
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
            if len(payload) > _MAX_SAFETY_ARTIFACT_BYTES:
                return False
        current = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or opened.st_uid != os.geteuid()
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or stat.S_IMODE(current.st_mode) != 0o600
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            return False
        decoded = json.loads(payload)
    except (OSError, TypeError, ValueError):
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return (
        isinstance(decoded, dict)
        and isinstance(decoded.get("version"), int)
        and decoded["version"] >= 1
        and isinstance(decoded.get("instance_id"), str)
        and bool(decoded["instance_id"])
        and isinstance(decoded.get("operation_id"), str)
        and bool(decoded["operation_id"])
        and decoded.get("phase") == "terminal"
        and isinstance(decoded.get("outcome"), str)
        and bool(decoded["outcome"])
        and isinstance(decoded.get("created_at"), str)
        and isinstance(decoded.get("updated_at"), str)
    )


@contextmanager
def _acquire_device_locks(directory: Path, keys: tuple[str, ...]) -> Iterator[None]:
    descriptors: list[int] = []
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = directory.lstat()
        if (
            directory.is_symlink()
            or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise LocalControlLeaseError("physical-device lock directory is unsafe")
        for key in keys:
            descriptor = _open_device_lock(directory / f"{key}.lock")
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                _validate_lock_descriptor(descriptor, directory / f"{key}.lock")
            except BlockingIOError as error:
                os.close(descriptor)
                raise LocalControlLeaseBusyError(
                    "a physical controller is owned by another write workflow"
                ) from error
            except BaseException:
                os.close(descriptor)
                raise
            descriptors.append(descriptor)
        yield
    finally:
        for descriptor in reversed(descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def _open_device_lock(path: Path) -> int:
    if not hasattr(os, "O_NOFOLLOW"):
        raise LocalControlLeaseError("O_NOFOLLOW is required for physical-device locks")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        raise LocalControlLeaseError("cannot open physical-device lock") from error
    try:
        _validate_lock_descriptor(descriptor, path)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _validate_lock_descriptor(descriptor: int, path: Path) -> None:
    opened = os.fstat(descriptor)
    try:
        named = path.stat(follow_symlinks=False)
    except OSError as error:
        raise LocalControlLeaseError("physical-device lock changed while opening") from error
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
        raise LocalControlLeaseError("physical-device lock has unsafe metadata")


__all__ = [
    "ActiveLocalControlLease",
    "LocalControlLease",
    "LocalControlLeaseBusyError",
    "LocalControlLeaseError",
]
