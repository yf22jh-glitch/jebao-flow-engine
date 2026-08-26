"""Deployment-wide lease and fail-closed interlock for physical writes.

Every sanctioned physical-write workflow uses the same advisory lock and persistent emergency
stop marker.  The default paths live under the fixed ``/hardware-safety`` mount; optional path
arguments exist for isolated tests only.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from jebao_flow.devices.linkage import LinkageSafetyInterlock
from jebao_flow.hardware_safety import (
    emergency_stop_latch_path,
    global_operation_lock_path,
    validate_hardware_safety_root,
)


class HardwareOperationLockError(RuntimeError):
    """The deployment-wide operation lease could not be established safely."""


class HardwareOperationBusyError(HardwareOperationLockError):
    """Another process already owns the deployment-wide physical-write lease."""


class DeploymentHardwareGuard(LinkageSafetyInterlock):
    """One process-local interlock backed by deployment-wide persistent primitives.

    ``permitted`` is fail-closed when any filesystem object exists at the emergency latch path.
    Observing a latch also trips the process-local epoch, so removing the file cannot revive an
    operation that was already in flight.  ``lease`` is deliberately synchronous because it only
    performs local, nonblocking filesystem operations and is held around an asynchronous workflow.
    """

    def __init__(
        self,
        *,
        operation_lock_path: Path | None = None,
        latch_path: Path | None = None,
        poll_interval_seconds: float = 0.1,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        super().__init__(initially_permitted=False)
        self._operation_lock_path = (
            global_operation_lock_path()
            if operation_lock_path is None
            else Path(operation_lock_path)
        )
        self._latch_path = (
            emergency_stop_latch_path() if latch_path is None else Path(latch_path)
        )
        self._poll_interval_seconds = poll_interval_seconds
        self._uses_fixed_operation_path = operation_lock_path is None

    @property
    def permitted(self) -> bool:
        self._observe_persistent_latch()
        return super().permitted

    @property
    def epoch(self) -> int:
        self._observe_persistent_latch()
        return super().epoch

    def clear(self) -> None:
        """Permit a new operation only while the persistent emergency latch is absent."""

        if self._persistent_latch_present():
            self._trip_if_active()
            return
        super().clear()
        # Close the check-then-clear race.  A later race is still caught by every ``permitted``
        # check, including the adapter's guard immediately before a wire send.
        self._observe_persistent_latch()

    async def wait_until_blocked(self) -> None:
        """Wake for either a local trip or a persistent latch created by another process."""

        while self.permitted:
            try:
                await asyncio.wait_for(
                    super().wait_until_blocked(),
                    timeout=self._poll_interval_seconds,
                )
            except TimeoutError:
                continue

    @contextmanager
    def lease(self) -> Iterator[None]:
        """Acquire the single deployment-wide physical-write lease without waiting."""

        if self._uses_fixed_operation_path:
            validate_hardware_safety_root()

        descriptor = self._open_lock_file()
        locked = False
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError as error:
                raise HardwareOperationBusyError(
                    "another physical-write workflow owns the deployment lease"
                ) from error
            except OSError as error:
                raise HardwareOperationLockError(
                    "cannot acquire the deployment-wide physical-write lease"
                ) from error

            # Ensure a path replacement cannot make another process lock a different inode after
            # this process opened the expected file.
            self._validate_open_lock(descriptor)
            yield
        finally:
            if locked:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(descriptor)

    def _open_lock_file(self) -> int:
        if not hasattr(os, "O_NOFOLLOW"):
            raise HardwareOperationLockError("O_NOFOLLOW is required for the hardware lease")
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        try:
            descriptor = os.open(self._operation_lock_path, flags, 0o600)
        except OSError as error:
            raise HardwareOperationLockError(
                "cannot open the deployment-wide physical-write lease"
            ) from error

        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid():
                raise HardwareOperationLockError(
                    "deployment-wide physical-write lease has unsafe ownership or type"
                )
            if stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1:
                raise HardwareOperationLockError(
                    "deployment-wide physical-write lease has unsafe metadata"
                )
            self._validate_open_lock(descriptor)
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def _validate_open_lock(self, descriptor: int) -> None:
        try:
            opened = os.fstat(descriptor)
            path_metadata = os.stat(self._operation_lock_path, follow_symlinks=False)
        except OSError as error:
            raise HardwareOperationLockError(
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
            raise HardwareOperationLockError(
                "deployment-wide physical-write lease has unsafe metadata"
            )

    def _persistent_latch_present(self) -> bool:
        try:
            self._latch_path.lstat()
        except FileNotFoundError:
            return False
        except OSError:
            # An unreadable latch location is indistinguishable from an active safety marker.
            return True
        return True

    def _observe_persistent_latch(self) -> None:
        if self._persistent_latch_present():
            self._trip_if_active()

    def _trip_if_active(self) -> None:
        if super().permitted:
            super().trip()


__all__ = [
    "DeploymentHardwareGuard",
    "HardwareOperationBusyError",
    "HardwareOperationLockError",
]
