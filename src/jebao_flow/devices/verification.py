"""Fail-closed qualification for the first physical Local Wavemaker Pro write.

This module is intentionally independent from the native-linkage transaction.  A first-write
qualification has a much narrower contract: one freshly connected controller, one verified
same-value frame, one small downward power step, and an exact compensating restore.  Every
potentially mutating phase is journaled before the corresponding device call.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal, Protocol, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from jebao_flow.devices.base import JebaoDevice
from jebao_flow.devices.identity import PhysicalDeviceBinding
from jebao_flow.protocol.models import Capability, DeviceState, DeviceTarget, LinkageRole
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO

OperationIdentifier = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$"),
]


class DeviceVerificationPhase(StrEnum):
    """Durable boundary reached by a first-write qualification."""

    PREPARED = "prepared"
    SAME_VALUE_PENDING = "same_value_pending"
    SAME_VALUE_VERIFIED = "same_value_verified"
    LOWER_POWER_PENDING = "lower_power_pending"
    LOWER_POWER_ACTIVE = "lower_power_active"
    RESTORE_PENDING = "restore_pending"
    RECOVERY_REQUIRED = "recovery_required"


class DeviceVerificationRecoveryReason(StrEnum):
    """Why an unfinished qualification remains latched."""

    SAFETY_INTERLOCK = "safety_interlock"
    SAFETY_STOP_FAILED = "safety_stop_failed"
    RESTORE_FAILED = "restore_failed"


class DeviceVerificationErrorCode(StrEnum):
    """Redacted failure categories safe to persist and publish."""

    SAFETY_INTERLOCK = "safety_interlock"
    ATTENDED_AUTHORITY_REQUIRED = "attended_authority_required"
    BINDING_MISMATCH = "binding_mismatch"
    FRESH_SESSION_REQUIRED = "fresh_session_required"
    UNSUPPORTED_DEVICE = "unsupported_device"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    INVALID_INITIAL_STATE = "invalid_initial_state"
    INVALID_POWER = "invalid_power"
    JOURNAL_BUSY = "journal_busy"
    OPERATION_BUSY = "operation_busy"
    OPERATION_EXPIRED = "operation_expired"
    SAME_VALUE_VERIFY_FAILED = "same_value_verify_failed"
    LOWER_POWER_VERIFY_FAILED = "lower_power_verify_failed"
    RESTORE_WRITE_FAILED = "restore_write_failed"
    RESTORE_VERIFY_FAILED = "restore_verify_failed"
    DEVICE_IO_FAILED = "device_io_failed"


class DeviceVerificationStopReason(StrEnum):
    TIMEOUT = "timeout"
    MANUAL = "manual"
    STOPPED_BEFORE_WRITE = "stopped_before_write"
    EXPIRED_BEFORE_WRITE = "expired_before_write"


class DeviceVerificationError(RuntimeError):
    """Base exception whose public text never includes a device address or raw exception."""

    def __init__(self, code: DeviceVerificationErrorCode) -> None:
        self.code = code
        super().__init__(f"device verification failed: {code.value}")


class DeviceVerificationPreflightError(DeviceVerificationError):
    pass


class DeviceVerificationBusyError(DeviceVerificationError):
    pass


class DeviceVerificationApplyError(DeviceVerificationError):
    pass


class DeviceVerificationRollbackError(DeviceVerificationError):
    pass


class DeviceVerificationRecoveryDeferred(DeviceVerificationError):
    pass


class DeviceVerificationJournalError(DeviceVerificationError):
    pass


class DeviceVerificationSpec(BaseModel):
    """One bounded downward-step qualification for one physical controller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: OperationIdentifier = Field(default_factory=lambda: uuid4().hex)
    target_power: int = Field(ge=0, le=45)
    duration_seconds: float = Field(gt=0, le=10)
    verification_interval_seconds: float = Field(default=0.25, ge=0.25, le=1)


class DeviceVerificationSnapshot(BaseModel):
    """Exact ON state that must be recovered after any attempted physical write."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    physical_binding: PhysicalDeviceBinding
    enabled: Literal[True]
    power: int = Field(ge=0, le=45)
    mode: Literal["constant"]
    frequency: int = Field(ge=0, le=100)
    linkage: Literal[LinkageRole.INDEPENDENT]
    timer_enabled: Literal[False]

    @classmethod
    def from_state(
        cls,
        state: DeviceState,
        *,
        physical_binding: PhysicalDeviceBinding,
    ) -> Self:
        if not state.online or state.error is not None:
            raise DeviceVerificationPreflightError(
                DeviceVerificationErrorCode.INVALID_INITIAL_STATE
            )
        if state.enabled is not True:
            raise DeviceVerificationPreflightError(
                DeviceVerificationErrorCode.INVALID_INITIAL_STATE
            )
        if state.mode != "constant":
            raise DeviceVerificationPreflightError(
                DeviceVerificationErrorCode.INVALID_INITIAL_STATE
            )
        if state.frequency is None:
            raise DeviceVerificationPreflightError(
                DeviceVerificationErrorCode.INVALID_INITIAL_STATE
            )
        if state.linkage is not LinkageRole.INDEPENDENT:
            raise DeviceVerificationPreflightError(
                DeviceVerificationErrorCode.INVALID_INITIAL_STATE
            )
        if state.timer_enabled is not False:
            raise DeviceVerificationPreflightError(
                DeviceVerificationErrorCode.INVALID_INITIAL_STATE
            )
        if state.power > 45:
            raise DeviceVerificationPreflightError(DeviceVerificationErrorCode.INVALID_POWER)
        return cls(
            physical_binding=physical_binding,
            enabled=True,
            power=state.power,
            mode="constant",
            frequency=state.frequency,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        )


class DeviceVerificationRecord(BaseModel):
    """Privacy-preserving durable recovery record for one controller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation_id: OperationIdentifier
    phase: DeviceVerificationPhase
    spec: DeviceVerificationSpec
    snapshot: DeviceVerificationSnapshot
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    write_started: bool = False
    recovery_reason: DeviceVerificationRecoveryReason | None = None
    error_code: DeviceVerificationErrorCode | None = None

    @field_validator("created_at", "updated_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("journal timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.operation_id != self.spec.operation_id:
            raise ValueError("record operation_id must match spec operation_id")
        if self.expires_at <= self.created_at:
            raise ValueError("record expiry must follow creation")
        if self.phase is DeviceVerificationPhase.PREPARED and self.write_started:
            raise ValueError("a prepared record cannot claim that writing started")
        if self.phase is not DeviceVerificationPhase.PREPARED and not self.write_started:
            raise ValueError("every mutating phase must conservatively claim writing started")
        if self.phase is DeviceVerificationPhase.RECOVERY_REQUIRED:
            if self.recovery_reason is None or self.error_code is None:
                raise ValueError("recovery records require a typed reason and error code")
        elif self.recovery_reason is not None or self.error_code is not None:
            raise ValueError("normal phases cannot contain recovery failure details")
        return self


class AttendedRestoreAuthority(BaseModel):
    """Short-lived, operation-bound acknowledgement for an ON restore after e-stop."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: OperationIdentifier
    physical_binding: PhysicalDeviceBinding
    issued_at: datetime
    expires_at: datetime
    permit_enabled_restore: Literal[True] = True

    @field_validator("issued_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("authority timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_lifetime(self) -> Self:
        lifetime = self.expires_at - self.issued_at
        if lifetime <= timedelta(0) or lifetime > timedelta(minutes=5):
            raise ValueError("attended authority lifetime must be at most five minutes")
        return self


class DeviceVerificationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: OperationIdentifier
    stop_reason: DeviceVerificationStopReason
    lower_power_applied: bool
    completed_at: datetime


class DeviceVerificationJournalStore(Protocol):
    def load(self) -> DeviceVerificationRecord | None: ...

    def create(self, record: DeviceVerificationRecord) -> None: ...

    def save(self, record: DeviceVerificationRecord) -> None: ...

    def clear(self) -> None: ...


class GlobalHardwareSafetyGuard(Protocol):
    """One external lock and interlock shared by every physical-write workflow."""

    @property
    def permitted(self) -> bool: ...

    @property
    def epoch(self) -> int: ...

    def lease(self) -> AbstractContextManager[None]: ...

    async def wait_until_blocked(self) -> None: ...


class JsonDeviceVerificationJournalStore:
    """Atomic, fsync-backed JSON journal with an exclusive-create first boundary."""

    _MAX_JOURNAL_BYTES = 1024 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def load(self) -> DeviceVerificationRecord | None:
        with self._lock:
            descriptor = self._open_existing(allow_absent=True)
            if descriptor is None:
                return None
            try:
                with os.fdopen(descriptor, encoding="utf-8") as stream:
                    descriptor = -1
                    payload = stream.read(self._MAX_JOURNAL_BYTES + 1)
                if len(payload.encode()) > self._MAX_JOURNAL_BYTES:
                    raise DeviceVerificationJournalError(
                        DeviceVerificationErrorCode.DEVICE_IO_FAILED
                    )
                return DeviceVerificationRecord.model_validate_json(payload)
            except DeviceVerificationJournalError:
                raise
            except (OSError, ValidationError, ValueError) as error:
                raise DeviceVerificationJournalError(
                    DeviceVerificationErrorCode.DEVICE_IO_FAILED
                ) from error
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    def create(self, record: DeviceVerificationRecord) -> None:
        """Create without replacing another process's unfinished qualification."""

        with self._lock:
            temporary_path: Path | None = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = self._write_temporary(record)
                os.link(temporary_path, self.path)
                self._fsync_parent()
            except FileExistsError as error:
                raise DeviceVerificationBusyError(
                    DeviceVerificationErrorCode.JOURNAL_BUSY
                ) from error
            except OSError as error:
                raise DeviceVerificationJournalError(
                    DeviceVerificationErrorCode.DEVICE_IO_FAILED
                ) from error
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def save(self, record: DeviceVerificationRecord) -> None:
        with self._lock:
            temporary_path: Path | None = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                existing = self._open_existing(allow_absent=True)
                if existing is not None:
                    os.close(existing)
                temporary_path = self._write_temporary(record)
                temporary_path.replace(self.path)
                self._fsync_parent()
            except OSError as error:
                raise DeviceVerificationJournalError(
                    DeviceVerificationErrorCode.DEVICE_IO_FAILED
                ) from error
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def clear(self) -> None:
        with self._lock:
            try:
                existing = self._open_existing(allow_absent=True)
                if existing is not None:
                    os.close(existing)
                self.path.unlink(missing_ok=True)
                self._fsync_parent()
            except OSError as error:
                raise DeviceVerificationJournalError(
                    DeviceVerificationErrorCode.DEVICE_IO_FAILED
                ) from error

    def _write_temporary(self, record: DeviceVerificationRecord) -> Path:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(record.model_dump_json(indent=2))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary_path.unlink(missing_ok=True)
            raise
        return temporary_path

    def _fsync_parent(self) -> None:
        if not self.path.parent.exists():
            return
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _open_existing(self, *, allow_absent: bool) -> int | None:
        if not hasattr(os, "O_NOFOLLOW"):
            raise DeviceVerificationJournalError(
                DeviceVerificationErrorCode.DEVICE_IO_FAILED
            )
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            if allow_absent:
                return None
            raise DeviceVerificationJournalError(
                DeviceVerificationErrorCode.DEVICE_IO_FAILED
            ) from None
        except OSError as error:
            raise DeviceVerificationJournalError(
                DeviceVerificationErrorCode.DEVICE_IO_FAILED
            ) from error
        self._require_safe_metadata(metadata)

        descriptor = -1
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            self._validate_open_file(descriptor)
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    @staticmethod
    def _require_safe_metadata(metadata: os.stat_result) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise DeviceVerificationJournalError(
                DeviceVerificationErrorCode.DEVICE_IO_FAILED
            )

    def _validate_open_file(self, descriptor: int) -> None:
        try:
            opened = os.fstat(descriptor)
            current = os.stat(self.path, follow_symlinks=False)
        except OSError as error:
            raise DeviceVerificationJournalError(
                DeviceVerificationErrorCode.DEVICE_IO_FAILED
            ) from error
        self._require_safe_metadata(opened)
        self._require_safe_metadata(current)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise DeviceVerificationJournalError(
                DeviceVerificationErrorCode.DEVICE_IO_FAILED
            )


class _ExecutionProgress:
    def __init__(self, record: DeviceVerificationRecord) -> None:
        self.record = record
        self.write_attempted = False
        self.lower_power_applied = False


class FirstPhysicalWriteVerifier:
    """Perform exactly one fail-closed qualification against one Pro controller."""

    # Restore is cancellation-shielded but still transport-bounded. If the adapter cannot finish
    # inside this window, the durable journal remains and a supervisor can retry exact restore;
    # an unbounded socket/read must never postpone the first recovery attempt indefinitely.
    _RESTORE_IO_TIMEOUT_SECONDS = 5.0
    _SAFETY_STOP_IO_TIMEOUT_SECONDS = 5.0
    _AUTOMATIC_RECOVERY_GRACE_SECONDS = 30.0

    _REQUIRED_WRITABLE = frozenset(
        {
            Capability.ENABLED,
            Capability.POWER,
            Capability.MODE,
            Capability.FREQUENCY,
            Capability.LINKAGE,
            Capability.TIMER,
        }
    )

    def __init__(
        self,
        device: JebaoDevice,
        store: DeviceVerificationJournalStore,
        *,
        global_guard: GlobalHardwareSafetyGuard,
    ) -> None:
        self._device = device
        self._store = store
        self._global_guard = global_guard
        self._run_lock = asyncio.Lock()
        self._guard_epoch: int | None = None
        self._active_operation_id: str | None = None
        self._stop_event: asyncio.Event | None = None
        self._monotonic_deadline: float | None = None

    @property
    def active_operation_id(self) -> str | None:
        return self._active_operation_id

    async def stop(self, operation_id: str | None = None) -> bool:
        if self._stop_event is None or self._active_operation_id is None:
            return False
        if operation_id is not None and operation_id != self._active_operation_id:
            return False
        self._stop_event.set()
        return True

    async def run(self, spec: DeviceVerificationSpec) -> DeviceVerificationResult:
        """Run the qualification, always restoring after any attempted write."""

        if self._run_lock.locked():
            raise DeviceVerificationBusyError(DeviceVerificationErrorCode.OPERATION_BUSY)
        async with self._run_lock:
            try:
                lease = self._global_guard.lease()
                lease.__enter__()
            except Exception as error:
                raise DeviceVerificationBusyError(
                    DeviceVerificationErrorCode.OPERATION_BUSY
                ) from error
            try:
                return await self._run_owned(spec)
            finally:
                lease.__exit__(None, None, None)

    async def recover_pending(
        self,
        *,
        attended_authority: AttendedRestoreAuthority | None = None,
    ) -> bool:
        """Recover an unfinished journal by exact restore; never resume its test steps."""

        if self._run_lock.locked():
            raise DeviceVerificationBusyError(DeviceVerificationErrorCode.OPERATION_BUSY)
        async with self._run_lock:
            try:
                lease = self._global_guard.lease()
                lease.__enter__()
            except Exception as error:
                raise DeviceVerificationBusyError(
                    DeviceVerificationErrorCode.OPERATION_BUSY
                ) from error
            try:
                return await self._recover_owned(attended_authority=attended_authority)
            finally:
                lease.__exit__(None, None, None)

    async def enforce_safety_stop(self, fallback_record: DeviceVerificationRecord) -> None:
        """Durably latch recovery, then make one bounded OFF attempt for a late e-stop."""

        if self._run_lock.locked():
            raise DeviceVerificationBusyError(DeviceVerificationErrorCode.OPERATION_BUSY)
        async with self._run_lock:
            try:
                lease = self._global_guard.lease()
                lease.__enter__()
            except Exception as error:
                raise DeviceVerificationBusyError(
                    DeviceVerificationErrorCode.OPERATION_BUSY
                ) from error
            connected = False
            try:
                record = self._store.load() or fallback_record
                self._validate_binding(record)
                if not self._device.connected:
                    await self._device.connect()
                    connected = True
                await self._persist_safety_recovery_and_stop(record)
            finally:
                if connected or self._device.connected:
                    await self._disconnect_uninterruptibly()
                lease.__exit__(None, None, None)

    async def _run_owned(self, spec: DeviceVerificationSpec) -> DeviceVerificationResult:
        if self._store.load() is not None:
            raise DeviceVerificationBusyError(DeviceVerificationErrorCode.JOURNAL_BUSY)
        if self._device.connected:
            # A fresh connection clears the LAN adapter's verified-value cache, proving the
            # same-value qualification reaches the controller instead of becoming a no-op.
            raise DeviceVerificationPreflightError(
                DeviceVerificationErrorCode.FRESH_SESSION_REQUIRED
            )
        if not self._global_guard.permitted:
            raise DeviceVerificationPreflightError(DeviceVerificationErrorCode.SAFETY_INTERLOCK)

        self._guard_epoch = self._global_guard.epoch
        self._active_operation_id = spec.operation_id
        self._stop_event = asyncio.Event()
        loop = asyncio.get_running_loop()
        self._monotonic_deadline = loop.time() + spec.duration_seconds
        connected = False
        cancellation_received = False
        try:
            await self._device.connect()
            connected = True
            record = await self._prepare(spec)
            self._store.create(record)
            progress = _ExecutionProgress(record)

            try:
                stop_reason = await self._execute(progress)
            except BaseException as operation_error:
                if progress.write_attempted:
                    try:
                        await self._restore_uninterruptibly(progress.record)
                    except BaseException as restore_error:
                        if isinstance(restore_error, asyncio.CancelledError):
                            cancellation_received = True
                        else:
                            raise
                else:
                    await self._clear_without_write(progress.record)
                if isinstance(operation_error, asyncio.CancelledError):
                    raise
                if isinstance(operation_error, DeviceVerificationError):
                    raise DeviceVerificationApplyError(operation_error.code) from operation_error
                raise DeviceVerificationApplyError(
                    DeviceVerificationErrorCode.DEVICE_IO_FAILED
                ) from operation_error

            if progress.write_attempted:
                await self._restore_uninterruptibly(progress.record)
            else:
                await self._clear_without_write(progress.record)
            if cancellation_received:
                raise asyncio.CancelledError
            return DeviceVerificationResult(
                operation_id=spec.operation_id,
                stop_reason=stop_reason,
                lower_power_applied=progress.lower_power_applied,
                completed_at=datetime.now(UTC),
            )
        finally:
            if connected or self._device.connected:
                await self._disconnect_uninterruptibly()
            self._guard_epoch = None
            self._active_operation_id = None
            self._stop_event = None
            self._monotonic_deadline = None

    async def _recover_owned(
        self,
        *,
        attended_authority: AttendedRestoreAuthority | None,
    ) -> bool:
        record = self._store.load()
        if record is None:
            return False
        if self._device.connected:
            raise DeviceVerificationPreflightError(
                DeviceVerificationErrorCode.FRESH_SESSION_REQUIRED
            )
        self._validate_binding(record)
        now = datetime.now(UTC)
        automatic_deadline = record.expires_at + timedelta(
            seconds=self._AUTOMATIC_RECOVERY_GRACE_SECONDS
        )
        attended_required = (
            record.recovery_reason is DeviceVerificationRecoveryReason.SAFETY_INTERLOCK
            or (
                record.write_started
                and (
                    now < record.created_at
                    or now < record.updated_at
                    or now > automatic_deadline
                )
            )
        )
        if attended_required:
            self._require_attended_authority(record, attended_authority)

        self._guard_epoch = self._global_guard.epoch
        self._active_operation_id = record.operation_id
        self._stop_event = asyncio.Event()
        connected = False
        try:
            await self._device.connect()
            connected = True
            if not record.write_started:
                # PREPARED is durable before any write. A fresh read proves the adapter is alive;
                # recovery must not turn this marker into a physical mutation.
                self._assert_snapshot(
                    record.snapshot,
                    await self._read_with_timeout(self._RESTORE_IO_TIMEOUT_SECONDS),
                    DeviceVerificationErrorCode.RESTORE_VERIFY_FAILED,
                )
                self._store.clear()
                return True
            await self._restore_uninterruptibly(
                record,
                attended_authority=attended_authority,
            )
            return True
        finally:
            if connected or self._device.connected:
                await self._disconnect_uninterruptibly()
            self._guard_epoch = None
            self._active_operation_id = None
            self._stop_event = None

    async def _prepare(self, spec: DeviceVerificationSpec) -> DeviceVerificationRecord:
        self._require_safety()
        capabilities = self._device.capabilities
        if capabilities.product_key != LOCAL_WAVEMAKER_PRO.product_key:
            raise DeviceVerificationPreflightError(DeviceVerificationErrorCode.UNSUPPORTED_DEVICE)
        if capabilities.model != LOCAL_WAVEMAKER_PRO.name:
            raise DeviceVerificationPreflightError(DeviceVerificationErrorCode.UNSUPPORTED_DEVICE)
        missing = self._REQUIRED_WRITABLE - capabilities.writable
        if missing or "constant" not in capabilities.native_modes:
            raise DeviceVerificationPreflightError(
                DeviceVerificationErrorCode.UNSUPPORTED_CAPABILITY
            )
        if LinkageRole.INDEPENDENT not in capabilities.linkage_roles:
            raise DeviceVerificationPreflightError(
                DeviceVerificationErrorCode.UNSUPPORTED_CAPABILITY
            )

        binding = self._device.physical_binding
        if binding is None or binding.product_key != capabilities.product_key:
            raise DeviceVerificationPreflightError(DeviceVerificationErrorCode.BINDING_MISMATCH)
        state = await self._device.get_state()
        snapshot = DeviceVerificationSnapshot.from_state(
            state,
            physical_binding=binding,
        )
        self._validate_power(
            capabilities.power_limits.min_power,
            capabilities.power_limits.max_power,
            capabilities.power_step,
            snapshot.power,
        )
        self._validate_power(
            capabilities.power_limits.min_power,
            capabilities.power_limits.max_power,
            capabilities.power_step,
            spec.target_power,
        )
        if not 1 <= snapshot.power - spec.target_power <= 5:
            raise DeviceVerificationPreflightError(DeviceVerificationErrorCode.INVALID_POWER)

        now = datetime.now(UTC)
        return DeviceVerificationRecord(
            operation_id=spec.operation_id,
            phase=DeviceVerificationPhase.PREPARED,
            spec=spec,
            snapshot=snapshot,
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=spec.duration_seconds),
        )

    @staticmethod
    def _validate_power(minimum: int, maximum: int, step: int, value: int) -> None:
        if value > 45 or not minimum <= value <= maximum or value % step:
            raise DeviceVerificationPreflightError(DeviceVerificationErrorCode.INVALID_POWER)

    async def _execute(
        self,
        progress: _ExecutionProgress,
    ) -> DeviceVerificationStopReason:
        abort = self._test_abort_reason(before_first_write=True)
        if abort is not None:
            return abort

        progress.record = self._transition(
            progress.record,
            DeviceVerificationPhase.SAME_VALUE_PENDING,
            write_started=True,
        )
        abort = self._test_abort_reason(before_first_write=True)
        if abort is not None:
            return abort

        progress.write_attempted = True
        await self._bounded_write(self._snapshot_target(progress.record.snapshot))
        self._assert_snapshot(
            progress.record.snapshot,
            await self._read_before_deadline(progress.record),
            DeviceVerificationErrorCode.SAME_VALUE_VERIFY_FAILED,
        )
        progress.record = self._transition(
            progress.record,
            DeviceVerificationPhase.SAME_VALUE_VERIFIED,
        )

        abort = self._test_abort_reason(before_first_write=False)
        if abort is not None:
            return abort
        progress.record = self._transition(
            progress.record,
            DeviceVerificationPhase.LOWER_POWER_PENDING,
        )
        abort = self._test_abort_reason(before_first_write=False)
        if abort is not None:
            return abort

        await self._bounded_write(
            DeviceTarget(enabled=True, power=progress.record.spec.target_power)
        )
        self._assert_lower_target(
            progress.record,
            await self._read_before_deadline(progress.record),
        )
        progress.lower_power_applied = True
        progress.record = self._transition(
            progress.record,
            DeviceVerificationPhase.LOWER_POWER_ACTIVE,
        )
        return await self._monitor(progress.record)

    async def _monitor(
        self,
        record: DeviceVerificationRecord,
    ) -> DeviceVerificationStopReason:
        if self._stop_event is None:
            raise AssertionError("stop event is not initialized")
        while True:
            self._require_safety()
            remaining = self._remaining_seconds(record)
            if remaining <= 0:
                return DeviceVerificationStopReason.TIMEOUT
            stop_waiter = asyncio.create_task(self._stop_event.wait())
            safety_waiter = asyncio.create_task(self._global_guard.wait_until_blocked())
            waiters = {stop_waiter, safety_waiter}
            try:
                done, _ = await asyncio.wait(
                    waiters,
                    timeout=min(record.spec.verification_interval_seconds, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for waiter in waiters:
                    if not waiter.done():
                        waiter.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)
            self._require_safety()
            if stop_waiter in done:
                return DeviceVerificationStopReason.MANUAL
            if self._remaining_seconds(record) <= 0:
                return DeviceVerificationStopReason.TIMEOUT
            self._assert_lower_target(record, await self._read_before_deadline(record))

    def _test_abort_reason(
        self,
        *,
        before_first_write: bool,
    ) -> DeviceVerificationStopReason | None:
        self._require_safety()
        if self._stop_event is not None and self._stop_event.is_set():
            return (
                DeviceVerificationStopReason.STOPPED_BEFORE_WRITE
                if before_first_write
                else DeviceVerificationStopReason.MANUAL
            )
        if self._remaining_seconds() <= 0:
            return (
                DeviceVerificationStopReason.EXPIRED_BEFORE_WRITE
                if before_first_write
                else DeviceVerificationStopReason.TIMEOUT
            )
        return None

    async def _bounded_write(self, target: DeviceTarget) -> None:
        self._require_safety()
        remaining = self._remaining_seconds()
        if remaining <= 0:
            raise DeviceVerificationApplyError(DeviceVerificationErrorCode.OPERATION_EXPIRED)
        try:
            async with asyncio.timeout(remaining):
                self._require_safety()
                await self._device.write_target(target, guard=self._test_mutation_allowed)
        except TimeoutError as error:
            raise DeviceVerificationApplyError(
                DeviceVerificationErrorCode.OPERATION_EXPIRED
            ) from error
        except Exception as error:
            if not self._safety_allows_operation():
                raise DeviceVerificationRecoveryDeferred(
                    DeviceVerificationErrorCode.SAFETY_INTERLOCK
                ) from error
            if self._remaining_seconds() <= 0:
                raise DeviceVerificationApplyError(
                    DeviceVerificationErrorCode.OPERATION_EXPIRED
                ) from error
            raise

    async def _read_before_deadline(
        self,
        record: DeviceVerificationRecord,
    ) -> DeviceState:
        remaining = self._remaining_seconds(record)
        if remaining <= 0:
            raise DeviceVerificationApplyError(DeviceVerificationErrorCode.OPERATION_EXPIRED)
        try:
            return await self._read_with_timeout(remaining)
        except TimeoutError as error:
            raise DeviceVerificationApplyError(
                DeviceVerificationErrorCode.OPERATION_EXPIRED
            ) from error

    async def _read_with_timeout(self, timeout_seconds: float) -> DeviceState:
        async with asyncio.timeout(timeout_seconds):
            return await self._device.get_state()

    async def _restore_uninterruptibly(
        self,
        record: DeviceVerificationRecord,
        *,
        attended_authority: AttendedRestoreAuthority | None = None,
    ) -> None:
        task = asyncio.create_task(self._restore(record, attended_authority=attended_authority))
        cancellation_received = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancellation_received = True
        task.result()
        if cancellation_received:
            raise asyncio.CancelledError

    async def _restore(
        self,
        record: DeviceVerificationRecord,
        *,
        attended_authority: AttendedRestoreAuthority | None,
    ) -> None:
        self._validate_binding(record)
        safety_recovery = (
            record.recovery_reason is DeviceVerificationRecoveryReason.SAFETY_INTERLOCK
        )
        if safety_recovery:
            self._require_attended_authority(record, attended_authority)
        if not self._safety_allows_operation():
            await self._persist_safety_recovery_and_stop(
                record,
                attended_authority=attended_authority,
            )

        if not safety_recovery:
            record = self._transition(record, DeviceVerificationPhase.RESTORE_PENDING)
        write_completed = False
        try:
            async with asyncio.timeout(self._RESTORE_IO_TIMEOUT_SECONDS):
                self._require_safety()
                await self._device.write_target(
                    self._snapshot_target(record.snapshot),
                    guard=self._safety_allows_operation,
                )
                write_completed = True

                # This is deliberately a new read after the exact restore frame. The journal is
                # never cleared based on an adapter ACK or a previously cached state.
                final_state = await self._device.get_state()
                self._assert_snapshot(
                    record.snapshot,
                    final_state,
                    DeviceVerificationErrorCode.RESTORE_VERIFY_FAILED,
                )
                # An emergency stop that races the final readback remains authoritative. Never
                # clear its recovery requirement merely because the last frame happened to match.
                self._require_safety()
        except DeviceVerificationRecoveryDeferred:
            await self._persist_safety_recovery_and_stop(
                record,
                attended_authority=attended_authority,
            )
        except DeviceVerificationError as error:
            code = (
                DeviceVerificationErrorCode.RESTORE_VERIFY_FAILED
                if write_completed
                else DeviceVerificationErrorCode.RESTORE_WRITE_FAILED
            )
            self._save_restore_failure(
                record,
                code,
                attended_authority=attended_authority,
            )
            raise DeviceVerificationRollbackError(code) from error
        except Exception as error:
            if not self._safety_allows_operation():
                try:
                    await self._persist_safety_recovery_and_stop(
                        record,
                        attended_authority=attended_authority,
                    )
                except DeviceVerificationRecoveryDeferred as deferred:
                    raise deferred from error
            code = (
                DeviceVerificationErrorCode.RESTORE_VERIFY_FAILED
                if write_completed
                else DeviceVerificationErrorCode.RESTORE_WRITE_FAILED
            )
            self._save_restore_failure(
                record,
                code,
                attended_authority=attended_authority,
            )
            raise DeviceVerificationRollbackError(code) from error
        self._store.clear()
        if not self._safety_allows_operation():
            await self._persist_safety_recovery_and_stop(
                record,
                attended_authority=attended_authority,
            )

    async def _persist_safety_recovery_and_stop(
        self,
        record: DeviceVerificationRecord,
        *,
        attended_authority: AttendedRestoreAuthority | None = None,
    ) -> None:
        recovery_record = self._save_recovery(
            record,
            reason=DeviceVerificationRecoveryReason.SAFETY_INTERLOCK,
            code=DeviceVerificationErrorCode.SAFETY_INTERLOCK,
            invalidate_authority=attended_authority,
        )
        try:
            async with asyncio.timeout(self._SAFETY_STOP_IO_TIMEOUT_SECONDS):
                await self._device.write_target(
                    DeviceTarget(
                        enabled=False,
                        power=0,
                        linkage=LinkageRole.INDEPENDENT,
                        timer_enabled=False,
                    )
                )
        except Exception:
            self._save_recovery(
                recovery_record,
                reason=DeviceVerificationRecoveryReason.SAFETY_INTERLOCK,
                code=DeviceVerificationErrorCode.SAFETY_STOP_FAILED,
                invalidate_authority=attended_authority,
            )
        raise DeviceVerificationRecoveryDeferred(DeviceVerificationErrorCode.SAFETY_INTERLOCK)

    async def _clear_without_write(self, record: DeviceVerificationRecord) -> None:
        # A fresh state read precedes every journal clear, even when local progress proves no
        # device mutation was attempted.
        state = await self._read_with_timeout(self._RESTORE_IO_TIMEOUT_SECONDS)
        self._assert_snapshot(
            record.snapshot,
            state,
            DeviceVerificationErrorCode.RESTORE_VERIFY_FAILED,
        )
        self._store.clear()

    def _save_restore_failure(
        self,
        record: DeviceVerificationRecord,
        code: DeviceVerificationErrorCode,
        *,
        attended_authority: AttendedRestoreAuthority | None = None,
    ) -> None:
        reason = (
            DeviceVerificationRecoveryReason.SAFETY_INTERLOCK
            if record.recovery_reason is DeviceVerificationRecoveryReason.SAFETY_INTERLOCK
            else DeviceVerificationRecoveryReason.RESTORE_FAILED
        )
        self._save_recovery(
            record,
            reason=reason,
            code=code,
            invalidate_authority=attended_authority,
        )

    def _save_recovery(
        self,
        record: DeviceVerificationRecord,
        *,
        reason: DeviceVerificationRecoveryReason,
        code: DeviceVerificationErrorCode,
        invalidate_authority: AttendedRestoreAuthority | None = None,
    ) -> DeviceVerificationRecord:
        updated_at = max(
            datetime.now(UTC),
            record.updated_at + timedelta(microseconds=1),
            (
                invalidate_authority.issued_at + timedelta(microseconds=1)
                if invalidate_authority is not None
                else record.updated_at
            ),
        )
        updated = record.model_copy(
            update={
                "phase": DeviceVerificationPhase.RECOVERY_REQUIRED,
                "write_started": True,
                "recovery_reason": reason,
                "error_code": code,
                "updated_at": updated_at,
            }
        )
        self._store.save(updated)
        return updated

    def _transition(
        self,
        record: DeviceVerificationRecord,
        phase: DeviceVerificationPhase,
        *,
        write_started: bool | None = None,
    ) -> DeviceVerificationRecord:
        if phase is DeviceVerificationPhase.RECOVERY_REQUIRED:
            raise ValueError("recovery transitions need an explicit typed reason")
        updated = record.model_copy(
            update={
                "phase": phase,
                "write_started": (record.write_started if write_started is None else write_started),
                "recovery_reason": None,
                "error_code": None,
                "updated_at": datetime.now(UTC),
            }
        )
        self._store.save(updated)
        return updated

    def _validate_binding(self, record: DeviceVerificationRecord) -> None:
        capabilities = self._device.capabilities
        if (
            capabilities.product_key != LOCAL_WAVEMAKER_PRO.product_key
            or capabilities.model != LOCAL_WAVEMAKER_PRO.name
        ):
            raise DeviceVerificationPreflightError(DeviceVerificationErrorCode.UNSUPPORTED_DEVICE)
        binding = self._device.physical_binding
        if binding is None or binding != record.snapshot.physical_binding:
            raise DeviceVerificationPreflightError(DeviceVerificationErrorCode.BINDING_MISMATCH)

    def _require_attended_authority(
        self,
        record: DeviceVerificationRecord,
        authority: AttendedRestoreAuthority | None,
    ) -> None:
        now = datetime.now(UTC)
        if (
            authority is None
            or authority.operation_id != record.operation_id
            or authority.physical_binding != record.snapshot.physical_binding
            or authority.issued_at < record.updated_at
            or not authority.issued_at <= now < authority.expires_at
            or authority.permit_enabled_restore is not True
        ):
            raise DeviceVerificationRecoveryDeferred(
                DeviceVerificationErrorCode.ATTENDED_AUTHORITY_REQUIRED
            )

    def _safety_allows_operation(self) -> bool:
        return (
            self._guard_epoch is not None
            and self._global_guard.permitted is True
            and self._global_guard.epoch == self._guard_epoch
        )

    def _test_mutation_allowed(self) -> bool:
        """Guard checked by the adapter under its I/O lock immediately before wire send."""

        return (
            self._safety_allows_operation()
            and (self._stop_event is None or not self._stop_event.is_set())
            and self._remaining_seconds() > 0
        )

    def _require_safety(self) -> None:
        if not self._safety_allows_operation():
            raise DeviceVerificationRecoveryDeferred(DeviceVerificationErrorCode.SAFETY_INTERLOCK)

    def _remaining_seconds(
        self,
        record: DeviceVerificationRecord | None = None,
    ) -> float:
        monotonic_remaining = (
            float("inf")
            if self._monotonic_deadline is None
            else self._monotonic_deadline - asyncio.get_running_loop().time()
        )
        wall_remaining = (
            float("inf")
            if record is None
            else (record.expires_at - datetime.now(UTC)).total_seconds()
        )
        return min(monotonic_remaining, wall_remaining)

    @staticmethod
    def _snapshot_target(snapshot: DeviceVerificationSnapshot) -> DeviceTarget:
        return DeviceTarget(
            enabled=True,
            power=snapshot.power,
            mode=snapshot.mode,
            frequency=snapshot.frequency,
            linkage=LinkageRole.INDEPENDENT,
            timer_enabled=False,
        )

    @staticmethod
    def _state_matches_snapshot(
        snapshot: DeviceVerificationSnapshot,
        state: DeviceState,
    ) -> bool:
        return (
            state.online is True
            and state.error is None
            and state.enabled is True
            and state.power == snapshot.power
            and state.mode == snapshot.mode
            and state.frequency == snapshot.frequency
            and state.linkage is LinkageRole.INDEPENDENT
            and state.timer_enabled is False
        )

    @classmethod
    def _assert_snapshot(
        cls,
        snapshot: DeviceVerificationSnapshot,
        state: DeviceState,
        code: DeviceVerificationErrorCode,
    ) -> None:
        if not cls._state_matches_snapshot(snapshot, state):
            raise DeviceVerificationError(code)

    @staticmethod
    def _assert_lower_target(
        record: DeviceVerificationRecord,
        state: DeviceState,
    ) -> None:
        snapshot = record.snapshot
        if (
            not state.online
            or state.error is not None
            or state.enabled is not True
            or state.power != record.spec.target_power
            or state.mode != snapshot.mode
            or state.frequency != snapshot.frequency
            or state.linkage is not LinkageRole.INDEPENDENT
            or state.timer_enabled is not False
        ):
            raise DeviceVerificationError(DeviceVerificationErrorCode.LOWER_POWER_VERIFY_FAILED)

    async def _disconnect_uninterruptibly(self) -> None:
        task = asyncio.create_task(self._device.disconnect())
        deadline = asyncio.get_running_loop().time() + self._RESTORE_IO_TIMEOUT_SECONDS
        while not task.done() and asyncio.get_running_loop().time() < deadline:
            try:
                remaining = deadline - asyncio.get_running_loop().time()
                async with asyncio.timeout(max(remaining, 0.001)):
                    await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except TimeoutError:
                break
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return
        try:
            task.result()
        except Exception:
            # Disconnection does not change controller state. A later operation still refuses a
            # non-fresh adapter via ``device.connected``.
            return


@contextmanager
def exclusive_file_lease(path: str | Path) -> Iterator[None]:
    """Small building block for a process-global hardware-operation guard."""

    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    if not hasattr(os, "O_NOFOLLOW"):
        raise DeviceVerificationBusyError(DeviceVerificationErrorCode.OPERATION_BUSY)
    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(lock_path, flags, 0o600)
        opened = os.fstat(descriptor)
        current = os.stat(lock_path, follow_symlinks=False)
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
            raise DeviceVerificationBusyError(DeviceVerificationErrorCode.OPERATION_BUSY)
    except DeviceVerificationBusyError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise DeviceVerificationBusyError(DeviceVerificationErrorCode.OPERATION_BUSY) from error
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DeviceVerificationBusyError(DeviceVerificationErrorCode.OPERATION_BUSY) from error
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


__all__ = [
    "AttendedRestoreAuthority",
    "DeviceVerificationApplyError",
    "DeviceVerificationBusyError",
    "DeviceVerificationError",
    "DeviceVerificationErrorCode",
    "DeviceVerificationJournalError",
    "DeviceVerificationJournalStore",
    "DeviceVerificationPhase",
    "DeviceVerificationPreflightError",
    "DeviceVerificationRecord",
    "DeviceVerificationRecoveryDeferred",
    "DeviceVerificationRecoveryReason",
    "DeviceVerificationResult",
    "DeviceVerificationRollbackError",
    "DeviceVerificationSnapshot",
    "DeviceVerificationSpec",
    "DeviceVerificationStopReason",
    "FirstPhysicalWriteVerifier",
    "GlobalHardwareSafetyGuard",
    "JsonDeviceVerificationJournalStore",
    "exclusive_file_lease",
]
