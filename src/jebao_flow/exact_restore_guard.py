"""Independent deployment-wide guard for an attended exact-restore operation.

This module deliberately does not import ``jebao_flow.devices`` or the frozen native ASYNC
write harness.  The exact-restore path still shares the deployment-wide operation lease and
emergency-stop latch with every other physical-write workflow through ``hardware_safety``.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import stat
from pathlib import Path
from threading import get_ident
from types import TracebackType

from jebao_flow.hardware_safety import (
    HardwareSafetyRootError,
    _open_hardware_safety_root,
    _validate_hardware_safety_root_descriptor,
    emergency_stop_latch_path,
    global_operation_lock_path,
    hardware_safety_root,
)


class ExactRestoreOperationLockError(RuntimeError):
    """The deployment-wide exact-restore lease could not be established safely."""


class ExactRestoreOperationBusyError(ExactRestoreOperationLockError):
    """Another process already owns the deployment-wide physical-write lease."""


class _GuardLeaseContext:
    """Release a lease only from the exact process and thread that acquired it."""

    def __init__(self, guard: ExactRestoreGuard) -> None:
        self._guard = guard
        self._active = False
        self._owner_pid = -1
        self._owner_thread_id = -1

    def __enter__(self) -> None:
        if self._active:
            raise ExactRestoreOperationLockError("exact-restore lease context is already active")
        self._guard._acquire_lease()
        self._owner_pid = os.getpid()
        self._owner_thread_id = get_ident()
        self._active = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, exc_value, traceback
        if not self._active:
            raise ExactRestoreOperationLockError("exact-restore lease context is not active")
        if self._owner_pid != os.getpid() or self._owner_thread_id != get_ident():
            raise ExactRestoreOperationLockError(
                "exact-restore lease must be released by its owner process and thread"
            )
        try:
            self._guard._release_lease()
        finally:
            self._active = False
        return False


class ExactRestoreGuard:
    """Fail-closed process interlock backed by deployment-wide persistent primitives.

    A new guard starts blocked and must be explicitly cleared before use.  Its epoch advances
    whenever ``trip`` is called, so a stale in-flight operation cannot be revived by a later
    ``clear``.  Any filesystem object at the emergency latch path is treated as an active latch;
    unreadable latch metadata is treated the same way.

    ``lease`` is synchronous because it performs only local, nonblocking filesystem operations.
    The descriptor may then be held around an asynchronous exact-restore workflow.
    """

    def __init__(
        self,
        *,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")

        self._permitted = False
        self._epoch = 0
        self._blocked = asyncio.Event()
        self._blocked.set()
        self._operation_lock_path = global_operation_lock_path()
        self._latch_path = emergency_stop_latch_path()
        self._poll_interval_seconds = poll_interval_seconds
        self._validate_fixed_paths = True
        self._lease_descriptor = -1
        self._lease_root_descriptor = -1
        self._lease_compromised = False
        self._lease_pid = -1
        self._lease_thread_id = -1

    @classmethod
    def _for_test(
        cls,
        *,
        operation_lock_path: Path,
        latch_path: Path,
        poll_interval_seconds: float = 0.1,
    ) -> ExactRestoreGuard:
        """Build a guard with isolated paths for tests; production callers use fixed paths."""

        guard = cls(poll_interval_seconds=poll_interval_seconds)
        guard._operation_lock_path = Path(operation_lock_path)
        guard._latch_path = Path(latch_path)
        guard._validate_fixed_paths = False
        return guard

    @property
    def permitted(self) -> bool:
        self._observe_lease_integrity()
        self._observe_persistent_latch()
        return self._permitted

    @property
    def epoch(self) -> int:
        self._observe_lease_integrity()
        self._observe_persistent_latch()
        return self._epoch

    def trip(self) -> None:
        """Block the process-local operation and invalidate its captured epoch."""

        self._permitted = False
        self._epoch += 1
        self._blocked.set()

    def clear(self) -> None:
        """Permit an operation only while its fixed lease and emergency latch are intact."""

        if self._lease_descriptor < 0 or self._lease_compromised:
            self._trip_if_active()
            return
        self._observe_lease_integrity()
        if self._lease_compromised or self._persistent_latch_present():
            self._trip_if_active()
            return
        self._permitted = True
        self._blocked.clear()
        # Close the check-then-clear race.  Later races remain visible through every permitted
        # or epoch check and through wait_until_blocked's bounded poll.
        self._observe_persistent_latch()

    async def wait_until_blocked(self) -> None:
        """Wake for either a local trip or a latch created by another process."""

        while self.permitted:
            try:
                await asyncio.wait_for(
                    self._blocked.wait(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue

    def lease(self) -> _GuardLeaseContext:
        """Acquire the single deployment-wide physical-write lease without waiting."""

        return _GuardLeaseContext(self)

    def _acquire_lease(self) -> None:
        root_descriptor = -1
        descriptor = -1
        locked = False
        try:
            if self._validate_fixed_paths:
                root = hardware_safety_root()
                if self._operation_lock_path.parent != root or self._latch_path.parent != root:
                    raise ExactRestoreOperationLockError(
                        "exact-restore guard paths left the fixed hardware-safety root"
                    )
                root_descriptor = _open_hardware_safety_root()

            if self._lease_descriptor >= 0:
                raise ExactRestoreOperationLockError(
                    "exact-restore guard already owns a deployment-wide lease"
                )

            descriptor = self._open_lock_file(root_descriptor)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError as error:
                raise ExactRestoreOperationBusyError(
                    "another physical-write workflow owns the deployment lease"
                ) from error
            except OSError as error:
                raise ExactRestoreOperationLockError(
                    "cannot acquire the deployment-wide physical-write lease"
                ) from error

            self._validate_open_lock(descriptor, root_descriptor)
            self._lease_descriptor = descriptor
            self._lease_root_descriptor = root_descriptor
            self._lease_compromised = False
            self._lease_pid = os.getpid()
            self._lease_thread_id = get_ident()
            descriptor = -1
            root_descriptor = -1
        except HardwareSafetyRootError as error:
            raise ExactRestoreOperationLockError(
                "fixed hardware-safety root is unavailable for exact restore"
            ) from error
        finally:
            if locked and descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            if descriptor >= 0:
                os.close(descriptor)
            if root_descriptor >= 0:
                os.close(root_descriptor)

    def _release_lease(self) -> None:
        descriptor = self._lease_descriptor
        root_descriptor = self._lease_root_descriptor
        if descriptor < 0 or self._lease_pid != os.getpid() or self._lease_thread_id != get_ident():
            raise ExactRestoreOperationLockError(
                "exact-restore lease must be released by its owner process and thread"
            )

        self._observe_lease_integrity()
        compromised = self._lease_compromised
        self.trip()
        self._lease_descriptor = -1
        self._lease_root_descriptor = -1
        self._lease_compromised = False
        self._lease_pid = -1
        self._lease_thread_id = -1
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        finally:
            try:
                os.close(descriptor)
            finally:
                if root_descriptor >= 0:
                    os.close(root_descriptor)
        if compromised:
            raise ExactRestoreOperationLockError(
                "exact-restore lease integrity was lost before release"
            )

    def _open_lock_file(self, root_descriptor: int) -> int:
        if not hasattr(os, "O_NOFOLLOW"):
            raise ExactRestoreOperationLockError("O_NOFOLLOW is required for the hardware lease")
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        target: str | Path
        directory_descriptor: int | None
        if root_descriptor >= 0:
            target = self._operation_lock_path.name
            directory_descriptor = root_descriptor
        else:
            target = self._operation_lock_path
            directory_descriptor = None
        try:
            descriptor = os.open(target, flags, 0o600, dir_fd=directory_descriptor)
        except OSError as error:
            raise ExactRestoreOperationLockError(
                "cannot open the deployment-wide physical-write lease"
            ) from error

        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise ExactRestoreOperationLockError(
                    "deployment-wide physical-write lease has unsafe ownership or type"
                )
            if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
                raise ExactRestoreOperationLockError(
                    "deployment-wide physical-write lease has unsafe metadata"
                )
            self._validate_open_lock(descriptor, root_descriptor)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _validate_open_lock(self, descriptor: int, root_descriptor: int = -1) -> None:
        try:
            if root_descriptor >= 0:
                _validate_hardware_safety_root_descriptor(root_descriptor)
            opened = os.fstat(descriptor)
            if root_descriptor >= 0:
                path_metadata = os.stat(
                    self._operation_lock_path.name,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            else:
                path_metadata = os.stat(self._operation_lock_path, follow_symlinks=False)
            if root_descriptor >= 0:
                _validate_hardware_safety_root_descriptor(root_descriptor)
        except (HardwareSafetyRootError, OSError) as error:
            raise ExactRestoreOperationLockError(
                "deployment-wide physical-write lease changed while opening"
            ) from error
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(path_metadata.st_mode)
            or opened.st_uid != os.geteuid()
            or path_metadata.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or stat.S_IMODE(path_metadata.st_mode) != 0o600
            or opened.st_nlink != 1
            or path_metadata.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (path_metadata.st_dev, path_metadata.st_ino)
        ):
            raise ExactRestoreOperationLockError(
                "deployment-wide physical-write lease has unsafe metadata"
            )

    def _persistent_latch_present(self) -> bool:
        root_descriptor = self._lease_root_descriptor
        try:
            if root_descriptor >= 0:
                _validate_hardware_safety_root_descriptor(root_descriptor)
                os.stat(
                    self._latch_path.name,
                    dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
                _validate_hardware_safety_root_descriptor(root_descriptor)
            else:
                self._latch_path.lstat()
        except FileNotFoundError:
            if root_descriptor >= 0:
                try:
                    _validate_hardware_safety_root_descriptor(root_descriptor)
                except HardwareSafetyRootError:
                    self._lease_compromised = True
                    return True
            return False
        except HardwareSafetyRootError:
            self._lease_compromised = True
            return True
        except OSError:
            # An unreadable latch path is indistinguishable from an active safety marker.
            if root_descriptor >= 0:
                self._lease_compromised = True
            return True
        return True

    def _observe_persistent_latch(self) -> None:
        if self._persistent_latch_present():
            self._trip_if_active()

    def _observe_lease_integrity(self) -> None:
        descriptor = self._lease_descriptor
        if descriptor < 0:
            return
        if self._lease_pid != os.getpid() or self._lease_thread_id != get_ident():
            self._lease_compromised = True
            self._trip_if_active()
            return
        try:
            self._validate_open_lock(descriptor, self._lease_root_descriptor)
        except ExactRestoreOperationLockError:
            self._lease_compromised = True
            self._trip_if_active()

    def _trip_if_active(self) -> None:
        if self._permitted:
            self.trip()


__all__ = [
    "ExactRestoreGuard",
    "ExactRestoreOperationBusyError",
    "ExactRestoreOperationLockError",
]
