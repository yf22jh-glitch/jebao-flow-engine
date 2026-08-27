"""Crash-recoverable native master/slave linkage diagnostics.

The controller deliberately treats a temporary native-linkage test as a saga rather than a
normal group pattern.  Two physical controllers cannot be changed atomically, so every run is
journaled before its first write and always ends in compensating restore work.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
from collections.abc import Mapping
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from jebao_flow.devices.base import JebaoDevice, PowerStateVerificationError
from jebao_flow.devices.identity import PhysicalDeviceBinding
from jebao_flow.protocol.models import (
    Capability,
    DeviceSchedule,
    DeviceState,
    DeviceTarget,
    LinkageRole,
)

DeviceIdentifier = Annotated[str, StringConstraints(min_length=1)]
_AUDITED_SNAPSHOT_MODES = frozenset({"constant", "pulse", "sine"})
_SCHEDULE_BOOTSTRAP_SNAPSHOT_MODES = _AUDITED_SNAPSHOT_MODES | {"random"}
_SAFETY_STOP_TIMEOUT_SECONDS = 30.0
_RESTORE_TIMING_UNSET = object()
_LOGGER = logging.getLogger(__name__)


class LinkageTransactionError(RuntimeError):
    """Base error for a temporary native-linkage transaction."""


class LinkagePreflightError(LinkageTransactionError):
    """The requested transaction is unsafe or unsupported before any write."""


class LinkageTransactionBusyError(LinkageTransactionError):
    """Another transaction or unfinished recovery owns the devices."""


class LinkageApplyError(LinkageTransactionError):
    """Applying or verifying the temporary relationship failed, but restore succeeded."""


class LinkageLiveSlavePowerVerificationError(LinkageTransactionError):
    """A completed live slave-power write did not match its subsequent read-back."""


class LinkageRollbackError(LinkageTransactionError):
    """One or more devices could not be restored exactly."""


class LinkageJournalClaimError(LinkageTransactionError):
    """A durable journal already belongs to another daemon or recovery."""


class _ForwardStopRequested(LinkageTransactionError):
    """A normal stop won before the next temporary forward-control frame."""


class _ForwardDeadlineExpired(LinkageTransactionError):
    """The bounded experiment expired before the next forward-control frame."""


class _ScheduleStructureChangedDuringRestore(LinkageRollbackError):
    """A saved schedule fingerprint changed while exact restore was in progress."""


class _RestoreStateReadFailed(LinkageRollbackError):
    """A restore read failed before producing one complete decoded DeviceState."""


class LinkageTransactionPhase(StrEnum):
    PREPARED = "prepared"
    APPLYING = "applying"
    ACTIVE = "active"
    ROLLING_BACK = "rolling_back"
    RECOVERY_REQUIRED = "recovery_required"


class LinkageRecoveryReason(StrEnum):
    """Typed reason why an unfinished transaction remains recovery-latched."""

    SAFETY_INTERLOCK = "safety_interlock"
    SCHEDULE_CHANGED = "schedule_changed"
    RESTORE_FAILED = "restore_failed"


class LinkageRecoveryAuthority(StrEnum):
    """Authority level for compensation that may restore a saved ON state."""

    AUTOMATIC = "automatic"
    ATTENDED = "attended"


class LinkageStopReason(StrEnum):
    MANUAL = "manual"
    TIMEOUT = "timeout"


class LinkageSafetyInterlock:
    """Explicit, latched gate shared with emergency-stop and maintenance control."""

    def __init__(self, *, initially_permitted: bool = False) -> None:
        self._permitted = initially_permitted
        self._epoch = 0
        self._blocked = asyncio.Event()
        if not initially_permitted:
            self._blocked.set()

    @property
    def permitted(self) -> bool:
        return self._permitted

    @property
    def epoch(self) -> int:
        return self._epoch

    def trip(self) -> None:
        self._permitted = False
        self._epoch += 1
        self._blocked.set()

    def clear(self) -> None:
        self._permitted = True
        self._blocked.clear()

    async def wait_until_blocked(self) -> None:
        await self._blocked.wait()


class LinkageTestSpec(BaseModel):
    """One bounded native Sync/Async experiment between two compatible controllers."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(
        default_factory=lambda: uuid4().hex,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    master_device_id: DeviceIdentifier
    slave_device_id: DeviceIdentifier
    slave_role: LinkageRole
    mode: Literal["constant", "pulse", "sine"]
    master_power: int = Field(ge=0, le=100)
    slave_power: int = Field(ge=0, le=100)
    frequency: int = Field(ge=0, le=100)
    duration_seconds: float = Field(default=30, gt=0, le=900)
    verification_interval_seconds: float = Field(default=1, gt=0, le=30)
    bootstrap_active_schedule: bool = False
    slave_power_after: int | None = Field(default=None, ge=0, le=100)
    power_change_after_seconds: float | None = Field(default=None, gt=0, le=900)

    @model_validator(mode="after")
    def validate_relationship(self) -> Self:
        if self.master_device_id == self.slave_device_id:
            raise ValueError("master and slave devices must be different")
        if self.slave_role not in {
            LinkageRole.SYNC_SLAVE,
            LinkageRole.ASYNC_SLAVE,
        }:
            raise ValueError("slave_role must be sync_slave or async_slave")
        if (self.slave_power_after is None) != (self.power_change_after_seconds is None):
            raise ValueError(
                "slave_power_after and power_change_after_seconds must be provided together"
            )
        if self.slave_power_after is not None:
            if self.slave_role is not LinkageRole.ASYNC_SLAVE:
                raise ValueError("a live slave power change requires async_slave")
            if self.slave_power_after == self.slave_power:
                raise ValueError("the live slave power change must request a different value")
            if self.power_change_after_seconds is None:
                raise AssertionError("validated power change has no change time")
            if self.power_change_after_seconds >= self.duration_seconds:
                raise ValueError("the live slave power change must occur before test expiry")
        return self


class DeviceControlSnapshot(BaseModel):
    """Control state needed to undo a temporary linkage test exactly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: DeviceIdentifier
    physical_binding: PhysicalDeviceBinding
    enabled: bool
    power: int = Field(ge=0, le=100)
    mode: str = Field(min_length=1)
    frequency: int = Field(ge=0, le=100)
    linkage: LinkageRole
    timer_enabled: bool
    schedule_fingerprint: str | None = None

    @field_validator("linkage")
    @classmethod
    def require_independent_snapshot(cls, value: LinkageRole) -> LinkageRole:
        if value is not LinkageRole.INDEPENDENT:
            raise ValueError("temporary linkage snapshots must start independent")
        return value

    @field_validator("mode")
    @classmethod
    def require_audited_restore_mode(cls, value: str) -> str:
        if value not in _SCHEDULE_BOOTSTRAP_SNAPSHOT_MODES:
            audited = ", ".join(sorted(_SCHEDULE_BOOTSTRAP_SNAPSHOT_MODES))
            raise ValueError(f"snapshot mode must be one of the restorable modes: {audited}")
        return value

    @classmethod
    def from_state(
        cls,
        device_id: str,
        state: DeviceState,
        *,
        physical_binding: PhysicalDeviceBinding,
    ) -> Self:
        if state.frequency is None:
            raise LinkagePreflightError(f"device {device_id!r} did not report frequency")
        if state.linkage is None:
            raise LinkagePreflightError(f"device {device_id!r} did not report linkage")
        if state.linkage is not LinkageRole.INDEPENDENT:
            raise LinkagePreflightError(f"device {device_id!r} must start in independent mode")
        if state.timer_enabled is None:
            raise LinkagePreflightError(f"device {device_id!r} did not report TimerON")
        if state.mode not in _SCHEDULE_BOOTSTRAP_SNAPSHOT_MODES:
            audited = ", ".join(sorted(_SCHEDULE_BOOTSTRAP_SNAPSHOT_MODES))
            raise LinkagePreflightError(
                f"device {device_id!r} current mode {state.mode!r} is outside "
                f"the restorable modes: {audited}"
            )
        if state.timer_enabled:
            schedule = state.schedule
            if schedule is None:
                raise LinkagePreflightError(
                    f"device {device_id!r} has TimerON without a decoded schedule"
                )
            if not schedule.enabled:
                raise LinkagePreflightError(
                    f"device {device_id!r} TimerON disagrees with its decoded schedule"
                )
            if schedule.invalid_slots:
                raise LinkagePreflightError(f"device {device_id!r} schedule contains invalid slots")
        return cls(
            device_id=device_id,
            physical_binding=physical_binding,
            enabled=state.enabled,
            power=state.power,
            mode=state.mode,
            frequency=state.frequency,
            linkage=state.linkage,
            timer_enabled=state.timer_enabled,
            schedule_fingerprint=schedule_structure_fingerprint(state.schedule),
        )


class LinkageTransactionRecord(BaseModel):
    """Durable recovery record; absence is the only terminal/success state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Version 2 adds privacy-preserving physical bindings and per-device restore progress. A
    # version-1 record cannot be migrated safely because it contains neither stable identity nor
    # enough information to prove which physical controller should receive compensation.
    version: Literal[2] = 2
    operation_id: str = Field(min_length=1)
    phase: LinkageTransactionPhase
    recovery_reason: LinkageRecoveryReason | None = None
    spec: LinkageTestSpec
    snapshots: tuple[DeviceControlSnapshot, ...] = Field(min_length=2, max_length=2)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    error: str | None = None
    failed_device_ids: tuple[str, ...] = ()
    restored_device_ids: tuple[str, ...] = ()
    bootstrap_qualified_device_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.operation_id != self.spec.operation_id:
            raise ValueError("record operation_id must match spec operation_id")
        if self.phase is LinkageTransactionPhase.RECOVERY_REQUIRED:
            if self.recovery_reason is None:
                raise ValueError("recovery_reason is required when recovery is required")
        elif self.recovery_reason is not None:
            raise ValueError("recovery_reason must be None outside recovery_required")
        snapshot_ids = {snapshot.device_id for snapshot in self.snapshots}
        expected_ids = {self.spec.master_device_id, self.spec.slave_device_id}
        if snapshot_ids != expected_ids:
            raise ValueError("record snapshots must cover exactly the master and slave")
        restored_ids = set(self.restored_device_ids)
        failed_ids = set(self.failed_device_ids)
        bootstrap_qualified_ids = set(self.bootstrap_qualified_device_ids)
        if len(restored_ids) != len(self.restored_device_ids):
            raise ValueError("restored_device_ids must not contain duplicates")
        if len(failed_ids) != len(self.failed_device_ids):
            raise ValueError("failed_device_ids must not contain duplicates")
        if len(bootstrap_qualified_ids) != len(self.bootstrap_qualified_device_ids):
            raise ValueError("bootstrap-qualified device IDs must not contain duplicates")
        if not restored_ids <= snapshot_ids:
            raise ValueError("restored_device_ids must reference transaction snapshots")
        if not failed_ids <= snapshot_ids:
            raise ValueError("failed_device_ids must reference transaction snapshots")
        if not bootstrap_qualified_ids <= snapshot_ids:
            raise ValueError("bootstrap-qualified IDs must reference transaction snapshots")
        if bootstrap_qualified_ids and not self.spec.bootstrap_active_schedule:
            raise ValueError("only schedule-bootstrap records may qualify devices")
        if restored_ids & failed_ids:
            raise ValueError("restored and failed device IDs must not overlap")
        return self


class LinkageTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    stop_reason: LinkageStopReason
    completed_at: datetime
    bootstrap_qualified_device_ids: tuple[str, ...] = ()
    slave_power_change_verified: bool = False


class LinkageJournalStore(Protocol):
    """Synchronous, durable store used at low-frequency transaction boundaries."""

    def load(self) -> LinkageTransactionRecord | None: ...

    def lease(self) -> AbstractContextManager[None]: ...

    def create(self, record: LinkageTransactionRecord) -> None: ...

    def save(self, record: LinkageTransactionRecord) -> None: ...

    def clear(self) -> None: ...


def schedule_structure_fingerprint(schedule: DeviceSchedule | None) -> str | None:
    """Hash schedule structure while excluding volatile clock and TimerON state."""

    if schedule is None:
        return None
    canonical = {
        "slot_capacity": schedule.slot_capacity,
        "entries": [
            entry.model_dump(mode="json")
            for entry in sorted(schedule.entries, key=lambda value: value.slot)
        ],
        "invalid_slots": sorted(schedule.invalid_slots),
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class TemporaryLinkageController:
    """Apply one bounded relationship and guarantee journaled compensating restore."""

    _AUTOMATIC_RECOVERY_GRACE_SECONDS = 30.0
    _RESTORE_VERIFICATION_DECODED_READ_ATTEMPTS = 4
    _DEFAULT_RESTORE_VERIFICATION_BACKOFF_SECONDS = 0.25
    _DEFAULT_RESTORE_VERIFICATION_READ_TIMEOUT_SECONDS = 5.5
    # The LAN driver can legitimately spend up to 30 seconds in a complete guarded write and
    # up to 15 seconds establishing and authenticating a new Gizwits session. Keep those bounds
    # separate from the readback convergence window so a slow connect cannot consume the time
    # reserved for proving the final TimerON state.
    _DEFAULT_RESTORE_WRITE_TIMEOUT_SECONDS = 31.0
    _DEFAULT_RESTORE_CONNECTION_TIMEOUT_SECONDS = 16.0
    # A connected hard-read failure can require 5.5 seconds for the failed read, 16 seconds each
    # for disconnect and fresh authentication, four 5.5-second decoded reads, and bounded
    # mismatch backoff. Keep the whole 61.25-second path inside one monotonic convergence window.
    _DEFAULT_RESTORE_VERIFICATION_CONVERGENCE_TIMEOUT_SECONDS = 64.0
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
        devices: Mapping[str, JebaoDevice],
        store: LinkageJournalStore,
        *,
        safety_interlock: LinkageSafetyInterlock,
        restore_verification_backoff_seconds: float = (
            _DEFAULT_RESTORE_VERIFICATION_BACKOFF_SECONDS
        ),
        restore_verification_read_timeout_seconds: float = (
            _DEFAULT_RESTORE_VERIFICATION_READ_TIMEOUT_SECONDS
        ),
        restore_verification_total_timeout_seconds: float | object = _RESTORE_TIMING_UNSET,
        restore_write_timeout_seconds: float | object = _RESTORE_TIMING_UNSET,
        restore_connection_timeout_seconds: float | object = _RESTORE_TIMING_UNSET,
        restore_verification_convergence_timeout_seconds: float | object = (
            _RESTORE_TIMING_UNSET
        ),
    ) -> None:
        timing_values = {
            "restore_verification_backoff_seconds": restore_verification_backoff_seconds,
            "restore_verification_read_timeout_seconds": (
                restore_verification_read_timeout_seconds
            ),
            "restore_verification_total_timeout_seconds": (
                restore_verification_total_timeout_seconds
            ),
            "restore_write_timeout_seconds": restore_write_timeout_seconds,
            "restore_connection_timeout_seconds": restore_connection_timeout_seconds,
            "restore_verification_convergence_timeout_seconds": (
                restore_verification_convergence_timeout_seconds
            ),
        }
        for name, value in timing_values.items():
            if value is _RESTORE_TIMING_UNSET:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be a finite number")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                raise ValueError(f"{name} must be finite")
        if restore_verification_backoff_seconds < 0:
            raise ValueError("restore_verification_backoff_seconds must be non-negative")
        if restore_verification_read_timeout_seconds <= 0:
            raise ValueError("restore_verification_read_timeout_seconds must be positive")
        for name, value in timing_values.items():
            if name in {
                "restore_verification_backoff_seconds",
                "restore_verification_read_timeout_seconds",
            } or value is _RESTORE_TIMING_UNSET:
                continue
            if value <= 0:
                raise ValueError(f"{name} must be positive")

        # Backward compatibility for tests and callers that supplied the former shared bound:
        # an explicit legacy value fans out to the three new long-running phases. A dedicated
        # value always wins, while omission selects the audited production defaults.
        legacy_total = (
            None
            if restore_verification_total_timeout_seconds is _RESTORE_TIMING_UNSET
            else float(restore_verification_total_timeout_seconds)
        )

        def resolve_timeout(dedicated: float | object, default: float) -> float:
            if dedicated is not _RESTORE_TIMING_UNSET:
                return float(dedicated)
            if legacy_total is not None:
                return legacy_total
            return default

        self._devices = dict(devices)
        self._store = store
        self._run_lock = asyncio.Lock()
        self._safety_interlock = safety_interlock
        self._restore_verification_backoff_seconds = float(
            restore_verification_backoff_seconds
        )
        self._restore_verification_read_timeout_seconds = float(
            restore_verification_read_timeout_seconds
        )
        self._restore_write_timeout_seconds = resolve_timeout(
            restore_write_timeout_seconds,
            self._DEFAULT_RESTORE_WRITE_TIMEOUT_SECONDS,
        )
        self._restore_connection_timeout_seconds = resolve_timeout(
            restore_connection_timeout_seconds,
            self._DEFAULT_RESTORE_CONNECTION_TIMEOUT_SECONDS,
        )
        self._restore_verification_convergence_timeout_seconds = resolve_timeout(
            restore_verification_convergence_timeout_seconds,
            self._DEFAULT_RESTORE_VERIFICATION_CONVERGENCE_TIMEOUT_SECONDS,
        )
        self._stop_event: asyncio.Event | None = None
        self._active_operation_id: str | None = None
        self._safety_epoch: int | None = None
        self._operation_monotonic_deadline: float | None = None
        self._last_prepared_record: LinkageTransactionRecord | None = None

    @property
    def active_operation_id(self) -> str | None:
        return self._active_operation_id

    @property
    def blocked_device_ids(self) -> frozenset[str]:
        record = self._store.load()
        if record is None:
            return frozenset()
        return frozenset(snapshot.device_id for snapshot in record.snapshots)

    async def run(self, spec: LinkageTestSpec) -> LinkageTestResult:
        """Run until manual stop or timeout, then restore before returning."""

        if self._run_lock.locked():
            raise LinkageTransactionBusyError("another linkage transaction is already running")

        async with self._run_lock:
            try:
                lease = self._store.lease()
                lease.__enter__()
            except LinkageJournalClaimError as error:
                raise LinkageTransactionBusyError(
                    "another daemon owns the linkage journal lease"
                ) from error
            self._safety_epoch = self._safety_interlock.epoch
            try:
                return await self._run_owned(spec)
            finally:
                self._active_operation_id = None
                self._stop_event = None
                self._operation_monotonic_deadline = None
                self._safety_epoch = None
                lease.__exit__(None, None, None)

    async def _run_owned(self, spec: LinkageTestSpec) -> LinkageTestResult:
        pending = self._store.load()
        if pending is not None:
            raise LinkageTransactionBusyError(
                f"linkage recovery {pending.operation_id!r} must complete first"
            )

        started_at = datetime.now(UTC)
        self._operation_monotonic_deadline = (
            asyncio.get_running_loop().time() + spec.duration_seconds
        )
        self._active_operation_id = spec.operation_id
        self._stop_event = asyncio.Event()
        record = await self._prepare(
            spec,
            created_at=started_at,
            expires_at=started_at + timedelta(seconds=spec.duration_seconds),
        )
        self._last_prepared_record = record

        if self._stop_requested() and self._safety_allows_operation():
            return self._stopped_result(spec)

        try:
            self._store.create(record)
        except LinkageJournalClaimError as error:
            raise LinkageTransactionBusyError(
                "another daemon claimed the linkage recovery journal"
            ) from error

        if not self._safety_allows_operation():
            await self._rollback_uninterruptibly(record)
        if self._stop_requested():
            self._store.clear()
            return self._stopped_result(spec)
        if self._forward_deadline_expired(record):
            self._store.clear()
            raise LinkageApplyError(
                f"linkage operation {spec.operation_id!r} expired before its first control frame"
            )

        operation_error: BaseException | None = None
        stop_reason: LinkageStopReason | None = None
        slave_power_change_verified = False

        try:
            record = self._transition(record, LinkageTransactionPhase.APPLYING)
            record = await self._stage_devices(record)
            await self._activate_relationship(record)
            await self._verify_active_relationship(record)
            record = self._transition(record, LinkageTransactionPhase.ACTIVE)
            stop_reason, slave_power_change_verified = await self._monitor_until_stop(record)
        except BaseException as error:
            if self._stop_requested() and self._safety_allows_operation():
                stop_reason = LinkageStopReason.MANUAL
            else:
                operation_error = error

        try:
            await self._rollback_uninterruptibly(record)
        except asyncio.CancelledError:
            raise
        except BaseException as rollback_error:
            if isinstance(rollback_error, LinkageRollbackError):
                raise
            raise LinkageRollbackError(
                f"linkage operation {spec.operation_id!r} could not be restored"
            ) from rollback_error

        if operation_error is not None:
            if isinstance(operation_error, asyncio.CancelledError):
                raise operation_error
            raise LinkageApplyError(
                f"linkage operation {spec.operation_id!r} failed and was restored"
            ) from operation_error

        if stop_reason is None:
            raise AssertionError("successful linkage operation has no stop reason")
        return LinkageTestResult(
            operation_id=spec.operation_id,
            stop_reason=stop_reason,
            completed_at=datetime.now(UTC),
            bootstrap_qualified_device_ids=record.bootstrap_qualified_device_ids,
            slave_power_change_verified=slave_power_change_verified,
        )

    async def enforce_safety_stop(
        self,
        fallback_record: LinkageTransactionRecord | None = None,
    ) -> None:
        """Durably defer exact restore, then stop both devices after a late e-stop race."""

        if self._run_lock.locked():
            raise LinkageTransactionBusyError("another linkage transaction is still running")
        async with self._run_lock:
            try:
                lease = self._store.lease()
                lease.__enter__()
            except LinkageJournalClaimError as error:
                raise LinkageTransactionBusyError(
                    "another daemon owns the linkage journal lease"
                ) from error
            try:
                record = self._store.load() or self._last_prepared_record or fallback_record
                if record is None:
                    raise LinkageRollbackError(
                        "cannot persist a late safety stop without a recovery snapshot"
                    )
                self._validate_recovery_bindings(record)
                await self._defer_restore_for_safety(record)
            finally:
                lease.__exit__(None, None, None)

    async def stop(self, operation_id: str | None = None) -> bool:
        """Request early restore of the active transaction."""

        if self._stop_event is None or self._active_operation_id is None:
            return False
        if operation_id is not None and operation_id != self._active_operation_id:
            return False
        self._stop_event.set()
        return True

    async def recover_pending(
        self,
        *,
        authority: LinkageRecoveryAuthority = LinkageRecoveryAuthority.AUTOMATIC,
    ) -> bool:
        """Restore, never resume, a transaction found after process restart."""

        if self._run_lock.locked():
            raise LinkageTransactionBusyError("another linkage transaction is already running")
        async with self._run_lock:
            try:
                lease = self._store.lease()
                lease.__enter__()
            except LinkageJournalClaimError as error:
                raise LinkageTransactionBusyError(
                    "another daemon owns the linkage journal lease"
                ) from error
            self._safety_epoch = self._safety_interlock.epoch
            try:
                return await self._recover_owned(authority)
            finally:
                self._safety_epoch = None
                lease.__exit__(None, None, None)

    async def _recover_owned(self, authority: LinkageRecoveryAuthority) -> bool:
        record = self._store.load()
        if record is None:
            return False
        self._validate_recovery_bindings(record)
        if (
            record.recovery_reason is LinkageRecoveryReason.SCHEDULE_CHANGED
            and authority is not LinkageRecoveryAuthority.ATTENDED
        ):
            raise LinkagePreflightError(
                "schedule-changed recovery requires explicit attended authority"
            )
        if (
            authority is LinkageRecoveryAuthority.AUTOMATIC
            and record.phase is not LinkageTransactionPhase.PREPARED
        ):
            if any(snapshot.timer_enabled for snapshot in record.snapshots):
                raise LinkagePreflightError(
                    "TimerON linkage recovery requires explicit attended authority"
                )
            now = datetime.now(UTC)
            automatic_deadline = record.expires_at + timedelta(
                seconds=self._AUTOMATIC_RECOVERY_GRACE_SECONDS
            )
            if now < record.created_at or now < record.updated_at or now > automatic_deadline:
                raise LinkagePreflightError(
                    "stale linkage recovery requires explicit attended authority"
                )
        if (
            record.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK
            and authority is not LinkageRecoveryAuthority.ATTENDED
        ):
            raise LinkagePreflightError(
                "safety-interlock recovery requires explicit attended authority"
            )
        self._active_operation_id = record.operation_id
        self._stop_event = asyncio.Event()
        try:
            if record.phase is LinkageTransactionPhase.PREPARED:
                # APPLYING is durably persisted before the first device write. A PREPARED record
                # proves no compensation is needed and must not disturb a schedule that
                # legitimately advanced while the daemon was offline.
                self._store.clear()
            elif self._safety_allows_operation():
                schedule_change_ids: set[str] = set()
                read_failure_ids: set[str] = set()
                record = await self._reconcile_exactly_restored_devices(
                    record,
                    schedule_change_ids=schedule_change_ids,
                    read_failure_ids=read_failure_ids,
                )
                if len(record.restored_device_ids) == len(record.snapshots):
                    self._store.clear()
                else:
                    await self._rollback_uninterruptibly(
                        record,
                        schedule_change_ids=schedule_change_ids,
                        read_failure_ids=read_failure_ids,
                    )
            else:
                await self._rollback_uninterruptibly(record)
        finally:
            self._active_operation_id = None
            self._stop_event = None
        return True

    async def _prepare(
        self,
        spec: LinkageTestSpec,
        *,
        created_at: datetime,
        expires_at: datetime,
    ) -> LinkageTransactionRecord:
        if not self._safety_allows_operation():
            raise LinkagePreflightError("linkage test is blocked by the safety interlock")
        master = self._get_device(spec.master_device_id)
        slave = self._get_device(spec.slave_device_id)
        self._validate_capabilities(master, spec, LinkageRole.MASTER, spec.master_power)
        self._validate_capabilities(slave, spec, spec.slave_role, spec.slave_power)

        master_key = master.capabilities.product_key
        slave_key = slave.capabilities.product_key
        if master_key is None or slave_key is None:
            raise LinkagePreflightError(
                "native linkage requires known product keys for both devices"
            )
        if master_key != slave_key:
            raise LinkagePreflightError(
                "native linkage requires matching product families until cross-model behavior "
                "is verified"
            )

        snapshots: list[DeviceControlSnapshot] = []
        for device in (master, slave):
            physical_binding = device.physical_binding
            if physical_binding is None:
                raise LinkagePreflightError(
                    f"device {device.device_id!r} has no exact stable physical binding"
                )
            if physical_binding.product_key != device.capabilities.product_key:
                raise LinkagePreflightError(
                    f"device {device.device_id!r} physical binding does not match its product"
                )
            if not device.connected:
                raise LinkagePreflightError(f"device {device.device_id!r} is disconnected")
            state = await device.get_state()
            if not state.online:
                raise LinkagePreflightError(f"device {device.device_id!r} is offline")
            if state.error:
                raise LinkagePreflightError(
                    f"device {device.device_id!r} reports an error: {state.error}"
                )
            snapshot = DeviceControlSnapshot.from_state(
                device.device_id,
                state,
                physical_binding=physical_binding,
            )
            if (
                not spec.bootstrap_active_schedule
                and snapshot.mode not in _AUDITED_SNAPSHOT_MODES
            ):
                audited = ", ".join(sorted(_AUDITED_SNAPSHOT_MODES))
                raise LinkagePreflightError(
                    f"device {device.device_id!r} current mode {snapshot.mode!r} is outside "
                    f"the audited restore modes: {audited}"
                )
            if spec.bootstrap_active_schedule and snapshot.timer_enabled is not True:
                raise LinkagePreflightError(
                    f"device {device.device_id!r} must have an active decoded schedule for "
                    "schedule-bootstrap testing"
                )
            self._validate_snapshot(device, snapshot)
            snapshots.append(snapshot)

        return LinkageTransactionRecord(
            operation_id=spec.operation_id,
            phase=LinkageTransactionPhase.PREPARED,
            spec=spec,
            snapshots=tuple(snapshots),
            created_at=created_at,
            updated_at=datetime.now(UTC),
            expires_at=expires_at,
        )

    def _validate_capabilities(
        self,
        device: JebaoDevice,
        spec: LinkageTestSpec,
        role: LinkageRole,
        power: int,
    ) -> None:
        capabilities = device.capabilities
        missing = self._REQUIRED_WRITABLE - capabilities.writable
        if missing:
            names = ", ".join(sorted(capability.value for capability in missing))
            raise LinkagePreflightError(
                f"device {device.device_id!r} cannot write required capabilities: {names}"
            )
        if role not in capabilities.linkage_roles:
            raise LinkagePreflightError(
                f"device {device.device_id!r} does not support linkage {role.value!r}"
            )
        for mode in {"constant", spec.mode}:
            if mode not in capabilities.native_modes:
                raise LinkagePreflightError(
                    f"device {device.device_id!r} does not support native mode {mode!r}"
                )
        limits = capabilities.power_limits
        if not limits.min_power <= power <= limits.max_power:
            raise LinkagePreflightError(
                f"device {device.device_id!r} power {power} is outside "
                f"{limits.min_power}..{limits.max_power}"
            )
        if power % capabilities.power_step:
            raise LinkagePreflightError(
                f"device {device.device_id!r} power {power} does not match step "
                f"{capabilities.power_step}"
            )
        if spec.slave_power_after is not None and device.device_id == spec.slave_device_id:
            if not limits.min_power <= spec.slave_power_after <= limits.max_power:
                raise LinkagePreflightError(
                    f"device {device.device_id!r} power {spec.slave_power_after} is outside "
                    f"{limits.min_power}..{limits.max_power}"
                )
            if spec.slave_power_after % capabilities.power_step:
                raise LinkagePreflightError(
                    f"device {device.device_id!r} power {spec.slave_power_after} does not "
                    f"match step {capabilities.power_step}"
                )

    @staticmethod
    def _validate_snapshot(
        device: JebaoDevice,
        snapshot: DeviceControlSnapshot,
    ) -> None:
        capabilities = device.capabilities
        if not snapshot.enabled:
            raise LinkagePreflightError(
                f"device {device.device_id!r} must already be running; temporary linkage "
                "will not start an offline pump"
            )
        if snapshot.mode not in capabilities.native_modes:
            raise LinkagePreflightError(
                f"device {device.device_id!r} current mode {snapshot.mode!r} cannot be restored"
            )
        limits = capabilities.power_limits
        if not limits.min_power <= snapshot.power <= limits.max_power:
            raise LinkagePreflightError(
                f"device {device.device_id!r} current power {snapshot.power} cannot be restored "
                f"within {limits.min_power}..{limits.max_power}"
            )
        if snapshot.power % capabilities.power_step:
            raise LinkagePreflightError(
                f"device {device.device_id!r} current power {snapshot.power} does not match "
                f"step {capabilities.power_step}"
            )

    async def _stage_devices(
        self,
        record: LinkageTransactionRecord,
    ) -> LinkageTransactionRecord:
        if record.spec.bootstrap_active_schedule:
            return await self._bootstrap_scheduled_devices(record)
        for snapshot in record.snapshots:
            self._require_forward_write(record)
            device = self._get_device(snapshot.device_id)
            await device.write_target(
                DeviceTarget(
                    enabled=True,
                    power=self._safe_power(device),
                    mode="constant",
                    frequency=record.spec.frequency,
                    linkage=LinkageRole.INDEPENDENT,
                    timer_enabled=False,
                ),
                guard=lambda: self._forward_write_allowed(record),
            )
        return record

    async def _bootstrap_scheduled_devices(
        self,
        record: LinkageTransactionRecord,
    ) -> LinkageTransactionRecord:
        """Qualify first writes while pausing, but never rewriting, an active schedule."""

        # Re-read both snapshots before the first write. A schedule boundary may advance its
        # separate Auto* fields, but the saved manual fallback control and schedule structure
        # must still be identical to the armed preview.
        for snapshot in record.snapshots:
            state = await self._get_device(snapshot.device_id).get_state()
            self._assert_snapshot_control(
                snapshot,
                state,
                expected_timer=snapshot.timer_enabled,
            )
            self._assert_schedule_unchanged(snapshot, state)

        for snapshot in record.snapshots:
            device = self._get_device(snapshot.device_id)
            qualification_power, step_power = self._bootstrap_qualification_levels(device)
            baseline = DeviceTarget(
                enabled=True,
                power=qualification_power,
                mode="constant",
                frequency=record.spec.frequency,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=False,
            )
            for target in (
                baseline,
                baseline.model_copy(update={"power": step_power}),
                baseline,
            ):
                self._require_forward_write(record)
                await device.write_target(
                    target,
                    guard=lambda current_record=record: self._forward_write_allowed(current_record),
                )
                qualification_state = await device.get_state()
                self._assert_target(device.device_id, qualification_state, target)
                self._assert_schedule_unchanged(snapshot, qualification_state)

            _LOGGER.info(
                "schedule-bootstrap qualified device=%s baseline_power=%s step_power=%s",
                device.device_id,
                qualification_power,
                step_power,
            )
            qualified = tuple(
                sorted({*record.bootstrap_qualified_device_ids, snapshot.device_id})
            )
            record = record.model_copy(
                update={
                    "bootstrap_qualified_device_ids": qualified,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._store.save(record)
        return record

    async def _activate_relationship(self, record: LinkageTransactionRecord) -> None:
        spec = record.spec
        master = self._get_device(spec.master_device_id)
        slave = self._get_device(spec.slave_device_id)
        self._require_forward_write(record)
        await master.write_target(
            DeviceTarget(
                enabled=True,
                power=spec.master_power,
                mode=spec.mode,
                frequency=spec.frequency,
                linkage=LinkageRole.MASTER,
                timer_enabled=False,
            ),
            guard=lambda: self._forward_write_allowed(record),
        )
        self._require_forward_write(record)
        await slave.write_target(
            DeviceTarget(
                enabled=True,
                power=spec.slave_power,
                mode=spec.mode,
                frequency=spec.frequency,
                linkage=spec.slave_role,
                timer_enabled=False,
            ),
            guard=lambda: self._forward_write_allowed(record),
        )

    async def _verify_active_relationship(
        self,
        record: LinkageTransactionRecord,
        *,
        slave_power: int | None = None,
        live_slave_power_change: bool = False,
    ) -> None:
        self._require_safety_interlock()
        spec = record.spec
        expected_slave_power = spec.slave_power if slave_power is None else slave_power
        expected = {
            spec.master_device_id: DeviceTarget(
                enabled=True,
                power=spec.master_power,
                mode=spec.mode,
                frequency=spec.frequency,
                linkage=LinkageRole.MASTER,
                timer_enabled=False,
            ),
            spec.slave_device_id: DeviceTarget(
                enabled=True,
                power=expected_slave_power,
                mode=spec.mode,
                frequency=spec.frequency,
                linkage=spec.slave_role,
                timer_enabled=False,
            ),
        }
        snapshots = {snapshot.device_id: snapshot for snapshot in record.snapshots}
        observed: dict[str, DeviceState] = {}
        for device_id, target in expected.items():
            state = await self._get_device(device_id).get_state()
            if (
                live_slave_power_change
                and device_id == spec.slave_device_id
                and state.power != target.power
            ):
                # Classify only the exact diagnostic result: the state frame is otherwise the
                # requested slave target, but its Flow did not survive the live write.  Offline,
                # error, linkage, mode, frequency, timer, and schedule failures remain generic
                # transaction failures and must not be persisted as this primary diagnosis.
                self._assert_target(
                    device_id,
                    state,
                    target.model_copy(update={"power": state.power}),
                )
                if spec.bootstrap_active_schedule:
                    self._assert_schedule_unchanged(snapshots[device_id], state)
                raise LinkageLiveSlavePowerVerificationError(
                    "live slave power read-back did not match the requested target"
                )
            self._assert_target(device_id, state, target)
            if spec.bootstrap_active_schedule:
                self._assert_schedule_unchanged(snapshots[device_id], state)
            observed[device_id] = state
        _LOGGER.info(
            "native-linkage readback master_power=%s slave_power=%s slave_role=%s",
            observed[spec.master_device_id].power,
            observed[spec.slave_device_id].power,
            observed[spec.slave_device_id].linkage.value
            if observed[spec.slave_device_id].linkage is not None
            else "none",
        )

    async def _monitor_until_stop(
        self,
        record: LinkageTransactionRecord,
    ) -> tuple[LinkageStopReason, bool]:
        if self._stop_event is None:
            raise AssertionError("stop event is not initialized")
        loop = asyncio.get_running_loop()
        monitor_started = loop.time()
        expected_slave_power = record.spec.slave_power
        power_changed = False
        wall_remaining = (record.expires_at - datetime.now(UTC)).total_seconds()
        monotonic_deadline = self._operation_monotonic_deadline
        if monotonic_deadline is None:
            monotonic_deadline = loop.time() + max(0, wall_remaining)
        while True:
            self._require_safety_interlock()
            remaining = min(
                (record.expires_at - datetime.now(UTC)).total_seconds(),
                monotonic_deadline - loop.time(),
            )
            if remaining <= 0:
                if record.spec.slave_power_after is not None and not power_changed:
                    raise LinkageTransactionError(
                        "linkage test expired before the live slave power change"
                    )
                return LinkageStopReason.TIMEOUT, power_changed
            interval = min(record.spec.verification_interval_seconds, remaining)
            change_after = record.spec.power_change_after_seconds
            if not power_changed and change_after is not None:
                until_change = change_after - (loop.time() - monitor_started)
                interval = min(interval, max(0, until_change))
            stop_waiter = asyncio.create_task(self._stop_event.wait())
            safety_waiter = asyncio.create_task(self._safety_interlock.wait_until_blocked())
            waiters = {stop_waiter, safety_waiter}
            try:
                done, _ = await asyncio.wait(
                    waiters,
                    timeout=interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for waiter in waiters:
                    if not waiter.done():
                        waiter.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)
            # A simultaneous emergency stop always wins over a manual normal restore.
            self._require_safety_interlock()
            if stop_waiter in done:
                return LinkageStopReason.MANUAL, power_changed
            if (
                min(
                    (record.expires_at - datetime.now(UTC)).total_seconds(),
                    monotonic_deadline - loop.time(),
                )
                <= 0
            ):
                if record.spec.slave_power_after is not None and not power_changed:
                    raise LinkageTransactionError(
                        "linkage test expired before the live slave power change"
                    )
                return LinkageStopReason.TIMEOUT, power_changed
            power_change_sent = False
            if (
                not power_changed
                and record.spec.slave_power_after is not None
                and record.spec.power_change_after_seconds is not None
                and loop.time() - monitor_started >= record.spec.power_change_after_seconds
            ):
                self._require_forward_write(record)
                expected_slave_power = record.spec.slave_power_after
                slave = self._get_device(record.spec.slave_device_id)
                try:
                    await slave.write_target(
                        DeviceTarget(
                            enabled=True,
                            power=expected_slave_power,
                            mode=record.spec.mode,
                            frequency=record.spec.frequency,
                            linkage=record.spec.slave_role,
                            timer_enabled=False,
                        ),
                        guard=lambda: self._forward_write_allowed(record),
                    )
                except PowerStateVerificationError:
                    # The LAN adapter's decoded mismatch covers only fields in its control frame.
                    # Classify it through a fresh full DeviceState read so master health, device
                    # errors, and the saved schedule fingerprint must also be valid.  If Flow has
                    # since converged this returns and the original driver error remains generic.
                    await self._verify_active_relationship(
                        record,
                        slave_power=expected_slave_power,
                        live_slave_power_change=True,
                    )
                    raise
                power_change_sent = True
                _LOGGER.info(
                    "native-linkage requested live slave power change power=%s",
                    expected_slave_power,
                )
            # Detect the exact behavior this diagnostic is intended to measure: a native master
            # broadcast must not silently replace the requested per-slave Flow.
            await self._verify_active_relationship(
                record,
                slave_power=expected_slave_power,
                live_slave_power_change=power_changed or power_change_sent,
            )
            if power_change_sent:
                power_changed = True

    async def _rollback_uninterruptibly(
        self,
        record: LinkageTransactionRecord,
        *,
        schedule_change_ids: set[str] | None = None,
        read_failure_ids: set[str] | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._rollback(
                record,
                schedule_change_ids=schedule_change_ids,
                read_failure_ids=read_failure_ids,
            )
        )
        cancellation_received = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                # Every await remains shielded. Repeated Task.cancel() calls must never reach the
                # physical rollback child, even after the first cancellation was caught.
                cancellation_received = True
        task.result()
        if cancellation_received:
            raise asyncio.CancelledError

    async def _rollback(
        self,
        record: LinkageTransactionRecord,
        *,
        schedule_change_ids: set[str] | None = None,
        read_failure_ids: set[str] | None = None,
    ) -> None:
        # A tripped interlock must become durable before a bookkeeping transition can erase its
        # typed reason, and before any physical safe-stop frame is attempted.
        if not self._safety_allows_operation():
            await self._defer_restore_for_safety(record)

        if record.recovery_reason is not LinkageRecoveryReason.SCHEDULE_CHANGED:
            try:
                record = self._transition(record, LinkageTransactionPhase.ROLLING_BACK)
            except Exception:
                # A previously durable record is still usable. Restore first even if the phase
                # update cannot be written; never trade aquarium safety for nicer bookkeeping.
                pass
        # Once schedule drift has been observed, keep that typed reason durable throughout an
        # attended retry. Only terminal journal removal may erase it; otherwise a crash here could
        # let recovery-first treat the next process as an ordinary transient restore failure.

        if not self._safety_allows_operation():
            await self._defer_restore_for_safety(record)

        schedule_change_ids = set() if schedule_change_ids is None else set(schedule_change_ids)
        read_failure_ids = set() if read_failure_ids is None else set(read_failure_ids)
        record = await self._reconcile_exactly_restored_devices(
            record,
            excluded_device_ids=frozenset(schedule_change_ids | read_failure_ids),
            schedule_change_ids=schedule_change_ids,
            read_failure_ids=read_failure_ids,
        )
        already_restored = set(record.restored_device_ids)
        pending_snapshots = tuple(
            snapshot for snapshot in record.snapshots if snapshot.device_id not in already_restored
        )
        if not pending_snapshots:
            self._store.clear()
            return

        errors: dict[str, list[str]] = {snapshot.device_id: [] for snapshot in pending_snapshots}
        detach_order = (record.spec.slave_device_id, record.spec.master_device_id)
        restore_blocked_ids: set[str] = set()
        safe_fallback_attempted_ids: set[str] = set()
        detach_targets: dict[str, DeviceTarget] = {}

        async def block_after_slave_detach_failure(
            *,
            master_error: str = "slave_detach_unconfirmed",
        ) -> None:
            nonlocal record
            master_id = record.spec.master_device_id
            restore_blocked_ids.update({master_id, record.spec.slave_device_id})
            # Consult current durable progress rather than the snapshot taken before this rollback.
            # A master can become exact during the final restore loop, after ``already_restored``
            # was computed, and must still be paused if the slave's following safe fallback is
            # unconfirmed.
            if master_id not in record.restored_device_ids:
                return
            # Durable progress must be invalidated before disturbing a master that was already
            # exact. Then pause it immediately: the former slave may still obey native linkage.
            record = self._mark_devices_unrestored(record, {master_id})
            errors.setdefault(master_id, []).append(master_error)
            safe_fallback_attempted_ids.add(master_id)
            master_paused = await self._try_safe_fallback(
                self._get_device(master_id),
                record.spec.frequency,
            )
            if not master_paused:
                errors[master_id].append("safe_fallback_failed")
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)

        for device_id in detach_order:
            if device_id in already_restored:
                continue
            if device_id in read_failure_ids:
                errors[device_id].append("state_read_failed")
                safe_fallback_attempted_ids.add(device_id)
                detached = await self._try_safe_fallback(
                    self._get_device(device_id),
                    record.spec.frequency,
                    force_reconnect=True,
                )
                if not detached:
                    errors[device_id].append("detach_failed")
                    if device_id == record.spec.slave_device_id:
                        await block_after_slave_detach_failure()
                if not self._safety_allows_operation():
                    await self._defer_restore_for_safety(record)
                continue
            device = self._get_device(device_id)
            detach_target = DeviceTarget(
                enabled=True,
                power=self._safe_power(device),
                mode="constant",
                frequency=record.spec.frequency,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=False,
            )
            detach_targets[device_id] = detach_target
            try:
                await self._write_restore_target(
                    device,
                    detach_target,
                )
            except Exception:
                if not self._safety_allows_operation():
                    await self._defer_restore_for_safety(record)
                errors[device_id].append("detach_failed")
                restore_blocked_ids.add(device_id)
                if device_id == record.spec.slave_device_id:
                    await block_after_slave_detach_failure()
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)

        restored_control: set[str] = set()
        verified_detach_ids: set[str] = set()
        for snapshot in pending_snapshots:
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)
            if snapshot.device_id in (
                schedule_change_ids | read_failure_ids | restore_blocked_ids
            ):
                errors[snapshot.device_id].append("control_restore_failed")
                continue
            device = self._get_device(snapshot.device_id)
            try:
                if snapshot.timer_enabled and snapshot.enabled:
                    # Keep the device at the already-verified safe detach target until the final
                    # atomic manual-fallback + TimerON frame. Writing a saved high fallback with
                    # TimerOFF would briefly expose that power before the schedule resumes.
                    state = await self._read_restore_state(
                        device,
                        timeout_seconds=self._restore_verification_read_timeout_seconds,
                    )
                    detach_error: Exception | None = None
                    try:
                        self._assert_target(
                            snapshot.device_id,
                            state,
                            detach_targets[snapshot.device_id],
                        )
                    except Exception as error:
                        detach_error = error
                    if detach_error is None:
                        verified_detach_ids.add(snapshot.device_id)
                    # Observe schedule drift even when the same frame does not prove detach. A
                    # later matching schedule must never erase that hard failure.
                    self._assert_restore_schedule_unchanged(snapshot, state)
                    if detach_error is not None:
                        raise detach_error
                else:
                    await self._write_restore_target(
                        device,
                        DeviceTarget(
                            enabled=True,
                            power=snapshot.power,
                            mode=snapshot.mode,
                            frequency=snapshot.frequency,
                            linkage=snapshot.linkage,
                            timer_enabled=False,
                        ),
                    )
                    if not snapshot.enabled:
                        await device.set_enabled(False)
                    state = await self._read_restore_state(
                        device,
                        timeout_seconds=self._restore_verification_read_timeout_seconds,
                    )
                    self._assert_restore_schedule_unchanged(snapshot, state)
                    self._assert_snapshot_control(snapshot, state, expected_timer=False)
                    verified_detach_ids.add(snapshot.device_id)
                restored_control.add(snapshot.device_id)
                errors[snapshot.device_id].clear()
            except _ScheduleStructureChangedDuringRestore:
                schedule_change_ids.add(snapshot.device_id)
                record = self._latch_schedule_change(record, snapshot.device_id)
                if snapshot.device_id not in verified_detach_ids:
                    restore_blocked_ids.add(snapshot.device_id)
                    if snapshot.device_id == record.spec.slave_device_id:
                        await block_after_slave_detach_failure()
                errors[snapshot.device_id].append("control_restore_failed")
            except _RestoreStateReadFailed:
                read_failure_ids.add(snapshot.device_id)
                restore_blocked_ids.add(snapshot.device_id)
                if snapshot.device_id == record.spec.slave_device_id:
                    await block_after_slave_detach_failure()
                errors[snapshot.device_id].append("state_read_failed")
            except Exception:
                if not self._safety_allows_operation():
                    await self._defer_restore_for_safety(record)
                restore_blocked_ids.add(snapshot.device_id)
                if snapshot.device_id == record.spec.slave_device_id:
                    await block_after_slave_detach_failure()
                errors[snapshot.device_id].append("control_restore_failed")

        restore_verification_failed_ids: set[str] = set()
        for snapshot in pending_snapshots:
            if (
                snapshot.device_id not in restored_control
                or snapshot.device_id in restore_blocked_ids
            ):
                continue
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)
            device = self._get_device(snapshot.device_id)
            # Restore the saved manual fallback and TimerON in one guarded frame. For a scheduled
            # device this moves directly from safe-low TimerOFF to schedule authority without
            # exposing a saved high manual fallback between frames. The frame is never resent:
            # an exception may mean the controller applied it but its ACK/readback was lost.
            write_uncertain = False
            try:
                await self._write_restore_target(
                    device,
                    DeviceTarget(
                        enabled=snapshot.enabled,
                        power=snapshot.power,
                        mode=snapshot.mode,
                        frequency=snapshot.frequency,
                        linkage=snapshot.linkage,
                        timer_enabled=snapshot.timer_enabled,
                    ),
                )
            except Exception:
                if not self._safety_allows_operation():
                    await self._defer_restore_for_safety(record)
                write_uncertain = True

            try:
                await self._verify_exact_restore(
                    snapshot,
                    device,
                    force_fresh_session=write_uncertain,
                )
                record = self._mark_device_restored(record, snapshot.device_id)
                errors[snapshot.device_id].clear()
            except _ScheduleStructureChangedDuringRestore:
                if not self._safety_allows_operation():
                    await self._defer_restore_for_safety(record)
                schedule_change_ids.add(snapshot.device_id)
                record = self._latch_schedule_change(record, snapshot.device_id)
                restore_verification_failed_ids.add(snapshot.device_id)
                errors[snapshot.device_id].append("timer_restore_failed")
            except _RestoreStateReadFailed:
                if not self._safety_allows_operation():
                    await self._defer_restore_for_safety(record)
                read_failure_ids.add(snapshot.device_id)
                restore_verification_failed_ids.add(snapshot.device_id)
                errors[snapshot.device_id].append("state_read_failed")
            except Exception:
                if not self._safety_allows_operation():
                    await self._defer_restore_for_safety(record)
                restore_verification_failed_ids.add(snapshot.device_id)
                errors[snapshot.device_id].append("timer_restore_failed")

        record = await self._reconcile_exactly_restored_devices(
            record,
            excluded_device_ids=frozenset(
                schedule_change_ids
                | read_failure_ids
                | restore_blocked_ids
                | restore_verification_failed_ids
            ),
            schedule_change_ids=schedule_change_ids,
            read_failure_ids=read_failure_ids,
        )
        exactly_restored = set(record.restored_device_ids)
        # A transient write/read failure can leave a local diagnostic behind even though the
        # final fresh read proves that the device is now exactly restored.  Durable reconciliation
        # is authoritative: retaining that stale error would put the same device in both the
        # restored and failed sets, violating the journal model and masking the primary operation
        # error with a secondary rollback exception.
        for device_id in exactly_restored:
            errors.pop(device_id, None)
        for snapshot in record.snapshots:
            if snapshot.device_id not in exactly_restored:
                errors.setdefault(snapshot.device_id, []).append("final_verification_failed")

        failed = {device_id: values for device_id, values in errors.items() if values}
        if failed:
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)
            for device_id in tuple(failed):
                if device_id not in safe_fallback_attempted_ids:
                    fallback_confirmed = await self._try_safe_fallback(
                        self._get_device(device_id),
                        record.spec.frequency,
                        force_reconnect=device_id in read_failure_ids,
                    )
                    if not fallback_confirmed:
                        errors[device_id].append("safe_fallback_failed")
                        if device_id == record.spec.slave_device_id:
                            await block_after_slave_detach_failure(
                                master_error="slave_safe_fallback_unconfirmed"
                            )
                if not self._safety_allows_operation():
                    await self._defer_restore_for_safety(record)
            # A failed slave fallback can reopen a newly-restored master after ``failed`` was
            # first calculated. Rebuild the set from the updated error lists so both devices and
            # the master's pause result become durable in the recovery record.
            failed = {device_id: values for device_id, values in errors.items() if values}
            message = "; ".join(
                f"{device_id}: {','.join(values)}" for device_id, values in sorted(failed.items())
            )
            recovery_record = record.model_copy(
                update={
                    "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                    "recovery_reason": (
                        LinkageRecoveryReason.SCHEDULE_CHANGED
                        if schedule_change_ids
                        or record.recovery_reason is LinkageRecoveryReason.SCHEDULE_CHANGED
                        else LinkageRecoveryReason.RESTORE_FAILED
                    ),
                    "updated_at": datetime.now(UTC),
                    "error": message,
                    "failed_device_ids": tuple(sorted(failed)),
                }
            )
            self._store.save(recovery_record)
            raise LinkageRollbackError(
                f"linkage operation {record.operation_id!r} requires recovery: {message}"
            )

        if not self._safety_allows_operation():
            await self._defer_restore_for_safety(record)
        self._store.clear()

    async def _verify_exact_restore(
        self,
        snapshot: DeviceControlSnapshot,
        device: JebaoDevice,
        *,
        force_fresh_session: bool = False,
    ) -> None:
        """Allow bounded fresh reads for delayed TimerON/schedule convergence.

        The restore frame is sent exactly once.  Some controllers acknowledge its raw fields
        before a following full state+schedule read reflects the resumed TimerON authority, so
        retry only readback and never retransmit the saved ON target.
        """

        loop = asyncio.get_running_loop()
        # Initial session establishment has its own audited budget. Start the convergence clock
        # only afterwards so authentication latency cannot starve the evidence-gathering reads.
        if force_fresh_session and device.connected:
            await self._disconnect_restore_session(
                device,
                timeout_seconds=self._restore_connection_timeout_seconds,
            )
        if not device.connected:
            await self._ensure_restore_connection(
                device,
                deadline=loop.time() + self._restore_connection_timeout_seconds,
            )

        deadline = (
            loop.time() + self._restore_verification_convergence_timeout_seconds
        )
        last_error: Exception | None = None
        decoded_reads = 0
        exact_streak = 0
        transport_recovery_used = False

        while decoded_reads < self._RESTORE_VERIFICATION_DECODED_READ_ATTEMPTS:
            self._require_restore_safety()
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                state = await self._read_restore_state(
                    device,
                    timeout_seconds=min(
                        self._restore_verification_read_timeout_seconds,
                        remaining,
                    ),
                )
            except _RestoreStateReadFailed as error:
                self._require_restore_safety()
                exact_streak = 0
                last_error = error
                if transport_recovery_used:
                    raise _RestoreStateReadFailed(
                        f"device {snapshot.device_id!r} exact restore state read failed "
                        "after one fresh-session recovery"
                    ) from error
                transport_recovery_used = True
                # A timed-out/cancelled/invalid read makes the frame boundary untrustworthy. At
                # most once, force a new authenticated session and continue with readback only;
                # the final TimerON target above is deliberately never replayed.
                await self._recover_restore_verification_session(device, deadline=deadline)
                continue
            else:
                decoded_reads += 1
                self._require_restore_safety()
                # A schedule edit is not eventual controller convergence. Once observed, it is a
                # hard invariant failure and cannot be hidden by a later matching read.
                self._assert_restore_schedule_unchanged(snapshot, state)
                try:
                    self._assert_snapshot_control(
                        snapshot,
                        state,
                        expected_timer=snapshot.timer_enabled,
                    )
                    self._assert_timer_and_schedule(snapshot, state)
                except _ScheduleStructureChangedDuringRestore:
                    raise
                except Exception as error:
                    last_error = error
                    exact_streak = 0
                else:
                    exact_streak += 1
                    self._require_restore_safety()
                    # Two consecutive complete decoded observations prevent one late/stale exact
                    # frame on either the original or a recovered session from clearing recovery.
                    if exact_streak >= 2:
                        return
                    # A first exact frame is the riskiest observation to leave uncorroborated.
                    # Confirm it immediately rather than sleeping while schedule authority runs.
                    continue

            if decoded_reads < self._RESTORE_VERIFICATION_DECODED_READ_ATTEMPTS:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                delay = min(
                    self._restore_verification_backoff_seconds
                    * (2 ** (decoded_reads - 1)),
                    remaining,
                )
                await self._wait_for_restore_retry(delay)

        raise LinkageRollbackError(
            f"device {snapshot.device_id!r} exact restore was not confirmed by bounded reads"
        ) from last_error

    async def _recover_restore_verification_session(
        self,
        device: JebaoDevice,
        *,
        deadline: float,
    ) -> None:
        """Replace one poisoned read session within the convergence deadline."""

        self._require_restore_safety()
        if device.connected:
            await self._disconnect_restore_session(
                device,
                timeout_seconds=self._remaining_restore_timeout(
                    deadline,
                    ceiling=self._restore_connection_timeout_seconds,
                ),
            )
        await self._ensure_restore_connection(
            device,
            deadline=min(
                deadline,
                asyncio.get_running_loop().time() + self._restore_connection_timeout_seconds,
            ),
        )

    @staticmethod
    def _remaining_restore_timeout(deadline: float, *, ceiling: float) -> float:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise _RestoreStateReadFailed(
                "exact restore convergence window expired during session recovery"
            )
        return min(ceiling, remaining)

    async def _read_restore_state(
        self,
        device: JebaoDevice,
        *,
        timeout_seconds: float,
    ) -> DeviceState:
        """Read once with both a wall-clock bound and an interruptible safety race."""

        self._require_restore_safety()
        read_task = asyncio.create_task(device.get_state())
        safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        tasks = {read_task, safety_task}
        try:
            done, _ = await asyncio.wait(
                tasks,
                timeout=timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if safety_task in done or not self._safety_allows_operation():
                raise LinkageRollbackError(
                    "safety interlock changed during exact restore verification"
                )
            if read_task not in done:
                raise _RestoreStateReadFailed("exact restore state read timed out")
            try:
                state = read_task.result()
            except asyncio.CancelledError as error:
                raise _RestoreStateReadFailed("exact restore state read was cancelled") from error
            except Exception as error:
                raise _RestoreStateReadFailed("exact restore state read failed") from error
            self._require_restore_safety()
            return state
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _write_restore_target(
        self,
        device: JebaoDevice,
        target: DeviceTarget,
    ) -> None:
        """Write once with a wall-clock bound and let a safety trip win immediately.

        Cancellation makes the request outcome uncertain, so this helper never retransmits the
        target. Cancellation inside a protocol exchange quarantines that stream, while a cancel
        during command pacing/readback delay can still look connected; callers therefore force a
        fresh session before read-only verification or use the independently safe fallback path.
        """

        self._require_restore_safety()
        write_task = asyncio.create_task(
            device.write_target(
                target,
                guard=self._safety_allows_operation,
            )
        )
        safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        tasks = {write_task, safety_task}
        try:
            done, _ = await asyncio.wait(
                tasks,
                timeout=self._restore_write_timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            # A simultaneous completed write and safety trip is still a safety event. The caller
            # will durably latch recovery and issue OFF compensation after cancellation cleanup.
            if safety_task in done or not self._safety_allows_operation():
                raise LinkageRollbackError(
                    "safety interlock changed during exact restore write"
                )
            if write_task not in done:
                raise LinkageRollbackError("exact restore target write timed out")
            try:
                write_task.result()
            except asyncio.CancelledError as error:
                raise LinkageRollbackError(
                    "exact restore target write was cancelled"
                ) from error
            self._require_restore_safety()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            # JebaoDevice.write_target() requires prompt cancellation propagation so a LAN
            # stream is quarantined and its I/O lock is released before any OFF compensation.
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _ensure_restore_connection(
        self,
        device: JebaoDevice,
        *,
        deadline: float,
    ) -> None:
        """Reconnect without replaying a target inside one caller-owned deadline.

        A failed connect can leave a TCP stream open but unauthenticated. Cleanup therefore uses
        only the time remaining in the same deadline and is intentionally allowed after a safety
        trip: closing local transport cannot re-enable a pump, and the following durable safety
        path needs ``connected=False`` so it can establish fresh authentication before OFF.
        """

        self._require_restore_safety()
        if device.connected:
            return
        loop = asyncio.get_running_loop()
        try:
            connect_task = asyncio.create_task(device.connect())
            safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
            tasks = {connect_task, safety_task}
            try:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise _RestoreStateReadFailed(
                        "restore verification reconnect deadline expired"
                    )
                done, _ = await asyncio.wait(
                    tasks,
                    timeout=remaining,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if safety_task in done or not self._safety_allows_operation():
                    raise LinkageRollbackError(
                        "safety interlock changed during exact restore verification"
                    )
                if connect_task not in done:
                    raise _RestoreStateReadFailed("restore verification reconnect timed out")
                try:
                    connect_task.result()
                except asyncio.CancelledError as error:
                    raise _RestoreStateReadFailed(
                        "restore verification reconnect was cancelled"
                    ) from error
                except Exception as error:
                    raise _RestoreStateReadFailed(
                        "restore verification reconnect failed"
                    ) from error
                self._require_restore_safety()
                if not device.connected:
                    raise _RestoreStateReadFailed(
                        "restore verification reconnect did not establish a session"
                    )
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            await self._close_failed_restore_connection(device, deadline=deadline)
            raise
        except Exception as error:
            try:
                await self._close_failed_restore_connection(device, deadline=deadline)
            except _RestoreStateReadFailed as cleanup_error:
                raise cleanup_error from error
            raise

    async def _close_failed_restore_connection(
        self,
        device: JebaoDevice,
        *,
        deadline: float,
    ) -> None:
        """Quarantine a failed/half-authenticated session within its caller's deadline."""

        if not device.connected:
            return
        try:
            async with asyncio.timeout_at(deadline):
                await device.disconnect()
        except TimeoutError as error:
            # The production LAN session drops reader/writer/authentication synchronously before
            # waiting for socket close. Treat that postcondition as successful quarantine even if
            # the bounded wait_closed tail timed out.
            if device.connected:
                raise _RestoreStateReadFailed(
                    "failed restore connection cleanup timed out"
                ) from error
        except Exception as error:
            if device.connected:
                raise _RestoreStateReadFailed(
                    "failed restore connection cleanup did not close the session"
                ) from error
        if device.connected:
            raise _RestoreStateReadFailed(
                "failed restore connection cleanup left the session connected"
            )

    async def _disconnect_restore_session(
        self,
        device: JebaoDevice,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        """Force a fresh restore session without delaying a concurrent safety stop."""

        self._require_restore_safety()
        timeout = (
            self._restore_connection_timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        disconnect_task = asyncio.create_task(device.disconnect())
        safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        tasks = {disconnect_task, safety_task}
        try:
            done, _ = await asyncio.wait(
                tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if safety_task in done or not self._safety_allows_operation():
                raise LinkageRollbackError(
                    "safety interlock changed during restore session disconnect"
                )
            if disconnect_task not in done:
                raise _RestoreStateReadFailed("restore session disconnect timed out")
            try:
                disconnect_task.result()
            except asyncio.CancelledError as error:
                raise _RestoreStateReadFailed(
                    "restore session disconnect was cancelled"
                ) from error
            except Exception as error:
                raise _RestoreStateReadFailed("restore session disconnect failed") from error
            self._require_restore_safety()
            if device.connected:
                raise _RestoreStateReadFailed(
                    "restore session disconnect did not close the session"
                )
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _wait_for_restore_retry(self, delay_seconds: float) -> None:
        """Wait between fresh reads while allowing a safety latch to win immediately."""

        self._require_restore_safety()
        if delay_seconds <= 0:
            await asyncio.sleep(0)
            self._require_restore_safety()
            return

        safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        try:
            await asyncio.wait({safety_task}, timeout=delay_seconds)
        finally:
            if not safety_task.done():
                safety_task.cancel()
            await asyncio.gather(safety_task, return_exceptions=True)
        self._require_restore_safety()

    def _require_restore_safety(self) -> None:
        if not self._safety_allows_operation():
            raise LinkageRollbackError(
                "safety interlock changed during exact restore verification"
            )

    async def _defer_restore_for_safety(self, record: LinkageTransactionRecord) -> None:
        """Keep an emergency/maintenance latch authoritative over saved enabled state."""

        message = "exact restore deferred by safety interlock"
        recovery_record = record.model_copy(
            update={
                "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                "recovery_reason": LinkageRecoveryReason.SAFETY_INTERLOCK,
                "updated_at": datetime.now(UTC),
                "error": message,
                # Safe-stop intentionally disturbs every saved state, so no prior exact-restore
                # progress remains valid after this durable transition.
                "failed_device_ids": tuple(
                    sorted(snapshot.device_id for snapshot in record.snapshots)
                ),
                "restored_device_ids": (),
            }
        )
        # This fsynced record is the authority used by recovery-first when the external latch
        # cannot be created. It must precede every physical safe-stop attempt.
        self._store.save(recovery_record)

        async def stop_device(device_id: str) -> tuple[str, str | None]:
            device = self._get_device(device_id)
            try:
                # A cancelled/timed-out Gizwits read quarantines its TCP session because the
                # frame boundary is no longer trustworthy. Reconnect before the OFF frame rather
                # than attempting a safety command on a poisoned stream.
                if not device.connected:
                    await device.connect()
                await device.write_target(
                    DeviceTarget(
                        enabled=False,
                        power=0,
                        linkage=LinkageRole.INDEPENDENT,
                        timer_enabled=False,
                    )
                )
            except Exception:
                return device_id, "safe_stop_failed"
            return device_id, None

        async with asyncio.timeout(_SAFETY_STOP_TIMEOUT_SECONDS):
            results = await asyncio.gather(
                *(
                    stop_device(device_id)
                    for device_id in (
                        record.spec.slave_device_id,
                        record.spec.master_device_id,
                    )
                )
            )
        stop_errors = {device_id: error for device_id, error in results if error is not None}

        if stop_errors:
            details = ", ".join(
                f"{device_id}: {error}" for device_id, error in sorted(stop_errors.items())
            )
            message = f"{message}; safe stop errors: {details}"
            self._store.save(
                recovery_record.model_copy(
                    update={
                        "updated_at": datetime.now(UTC),
                        "error": message,
                    }
                )
            )
        raise LinkageRollbackError(f"linkage operation {record.operation_id!r}: {message}")

    async def _try_safe_fallback(
        self,
        device: JebaoDevice,
        frequency: int,
        *,
        force_reconnect: bool = False,
    ) -> bool:
        try:
            if force_reconnect and device.connected:
                await self._disconnect_restore_session(device)
            await self._ensure_restore_connection(
                device,
                deadline=(
                    asyncio.get_running_loop().time()
                    + self._restore_connection_timeout_seconds
                ),
            )
            await self._write_restore_target(
                device,
                DeviceTarget(
                    enabled=True,
                    power=self._safe_power(device),
                    mode="constant",
                    frequency=frequency,
                    linkage=LinkageRole.INDEPENDENT,
                    timer_enabled=False,
                ),
            )
        except Exception:
            # One bounded attempt only. Recovery remains latched in the journal and a later
            # reconnect can call recover_pending() without causing a command storm.
            return False
        return True

    async def _reconcile_exactly_restored_devices(
        self,
        record: LinkageTransactionRecord,
        *,
        excluded_device_ids: frozenset[str] = frozenset(),
        schedule_change_ids: set[str] | None = None,
        read_failure_ids: set[str] | None = None,
    ) -> LinkageTransactionRecord:
        """Freshly reconcile durable progress before skipping writes or clearing the journal."""

        if not self._safety_allows_operation():
            await self._defer_restore_for_safety(record)
        exactly_restored: list[str] = []
        for snapshot in record.snapshots:
            if snapshot.device_id in excluded_device_ids:
                continue
            device = self._get_device(snapshot.device_id)
            exact_observations = 0
            while exact_observations < 2:
                try:
                    state = await self._read_restore_state(
                        device,
                        timeout_seconds=self._restore_verification_read_timeout_seconds,
                    )
                except _RestoreStateReadFailed:
                    if read_failure_ids is not None:
                        read_failure_ids.add(snapshot.device_id)
                    if not self._safety_allows_operation():
                        await self._defer_restore_for_safety(record)
                    break
                except Exception:
                    if not self._safety_allows_operation():
                        await self._defer_restore_for_safety(record)
                    break
                try:
                    self._assert_restore_schedule_unchanged(snapshot, state)
                    self._assert_snapshot_control(
                        snapshot,
                        state,
                        expected_timer=snapshot.timer_enabled,
                    )
                    self._assert_timer_and_schedule(snapshot, state)
                except _ScheduleStructureChangedDuringRestore:
                    if schedule_change_ids is not None:
                        schedule_change_ids.add(snapshot.device_id)
                    record = self._latch_schedule_change(record, snapshot.device_id)
                    if not self._safety_allows_operation():
                        await self._defer_restore_for_safety(record)
                    break
                except Exception:
                    if not self._safety_allows_operation():
                        await self._defer_restore_for_safety(record)
                    break
                exact_observations += 1
            if exact_observations == 2:
                if not self._safety_allows_operation():
                    await self._defer_restore_for_safety(record)
                exactly_restored.append(snapshot.device_id)

        if not self._safety_allows_operation():
            await self._defer_restore_for_safety(record)
        restored = tuple(sorted(exactly_restored))
        failed = tuple(
            value for value in record.failed_device_ids if value not in exactly_restored
        )
        if restored == record.restored_device_ids and failed == record.failed_device_ids:
            return record
        updated = record.model_copy(
            update={
                "updated_at": datetime.now(UTC),
                "failed_device_ids": failed,
                "restored_device_ids": restored,
            }
        )
        self._store.save(updated)
        return updated

    def _transition(
        self,
        record: LinkageTransactionRecord,
        phase: LinkageTransactionPhase,
    ) -> LinkageTransactionRecord:
        if phase is LinkageTransactionPhase.RECOVERY_REQUIRED:
            raise ValueError("recovery_required transitions need an explicit recovery reason")
        updated = record.model_copy(
            update={
                "phase": phase,
                "recovery_reason": None,
                "updated_at": datetime.now(UTC),
                "error": None,
                "failed_device_ids": (),
            }
        )
        self._store.save(updated)
        return updated

    def _mark_device_restored(
        self,
        record: LinkageTransactionRecord,
        device_id: str,
    ) -> LinkageTransactionRecord:
        restored = tuple(sorted({*record.restored_device_ids, device_id}))
        updated = record.model_copy(
            update={
                "updated_at": datetime.now(UTC),
                "failed_device_ids": tuple(
                    value for value in record.failed_device_ids if value != device_id
                ),
                "restored_device_ids": restored,
            }
        )
        self._store.save(updated)
        return updated

    def _latch_schedule_change(
        self,
        record: LinkageTransactionRecord,
        device_id: str,
    ) -> LinkageTransactionRecord:
        """Persist schedule drift before any later read or compensating write can obscure it."""

        failed = tuple(sorted({*record.failed_device_ids, device_id}))
        restored = tuple(
            restored_id
            for restored_id in record.restored_device_ids
            if restored_id != device_id
        )
        if (
            record.recovery_reason is LinkageRecoveryReason.SCHEDULE_CHANGED
            and failed == record.failed_device_ids
            and restored == record.restored_device_ids
        ):
            return record
        schedule_message = f"{device_id}: schedule_changed"
        message = record.error or schedule_message
        if schedule_message not in message:
            message = f"{message}; {schedule_message}"
        updated = record.model_copy(
            update={
                "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                "recovery_reason": LinkageRecoveryReason.SCHEDULE_CHANGED,
                "updated_at": datetime.now(UTC),
                "error": message,
                "failed_device_ids": failed,
                "restored_device_ids": restored,
            }
        )
        self._store.save(updated)
        return updated

    def _mark_devices_unrestored(
        self,
        record: LinkageTransactionRecord,
        device_ids: set[str],
    ) -> LinkageTransactionRecord:
        restored = tuple(
            device_id
            for device_id in record.restored_device_ids
            if device_id not in device_ids
        )
        if restored == record.restored_device_ids:
            return record
        updated = record.model_copy(
            update={
                "updated_at": datetime.now(UTC),
                "restored_device_ids": restored,
            }
        )
        self._store.save(updated)
        return updated

    def _get_device(self, device_id: str) -> JebaoDevice:
        try:
            return self._devices[device_id]
        except KeyError as error:
            raise LinkagePreflightError(f"unknown device {device_id!r}") from error

    def _validate_recovery_bindings(self, record: LinkageTransactionRecord) -> None:
        """Reject config/device remapping before recovery can emit a physical write."""

        for snapshot in record.snapshots:
            device = self._get_device(snapshot.device_id)
            binding = device.physical_binding
            if binding is None:
                raise LinkagePreflightError(
                    f"device {snapshot.device_id!r} has no exact stable physical binding"
                )
            if binding != snapshot.physical_binding:
                raise LinkagePreflightError(
                    f"device {snapshot.device_id!r} physical binding does not match "
                    "the recovery journal"
                )

    def _safety_allows_operation(self) -> bool:
        return (
            self._safety_epoch is not None
            and self._safety_interlock.permitted is True
            and self._safety_interlock.epoch == self._safety_epoch
        )

    def _stop_requested(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    def _forward_deadline_expired(self, record: LinkageTransactionRecord) -> bool:
        monotonic_deadline = self._operation_monotonic_deadline
        return datetime.now(UTC) >= record.expires_at or (
            monotonic_deadline is not None
            and asyncio.get_running_loop().time() >= monotonic_deadline
        )

    def _forward_write_allowed(self, record: LinkageTransactionRecord) -> bool:
        return (
            self._safety_allows_operation()
            and not self._stop_requested()
            and not self._forward_deadline_expired(record)
        )

    def _require_forward_write(self, record: LinkageTransactionRecord) -> None:
        self._require_safety_interlock()
        if self._stop_requested():
            raise _ForwardStopRequested("linkage test was stopped before the next control frame")
        if self._forward_deadline_expired(record):
            raise _ForwardDeadlineExpired("linkage test expired before the next control frame")

    def _require_safety_interlock(self) -> None:
        if not self._safety_allows_operation():
            raise LinkageTransactionError("linkage test was stopped by the safety interlock")

    @staticmethod
    def _stopped_result(spec: LinkageTestSpec) -> LinkageTestResult:
        return LinkageTestResult(
            operation_id=spec.operation_id,
            stop_reason=LinkageStopReason.MANUAL,
            completed_at=datetime.now(UTC),
        )

    @staticmethod
    def _safe_power(device: JebaoDevice) -> int:
        capabilities = device.capabilities
        minimum = capabilities.power_limits.min_power
        step = capabilities.power_step
        safe = ((minimum + step - 1) // step) * step
        if safe > capabilities.power_limits.max_power:
            raise LinkagePreflightError(
                f"device {device.device_id!r} has no valid power at configured step"
            )
        return safe

    @staticmethod
    def _bootstrap_qualification_levels(device: JebaoDevice) -> tuple[int, int]:
        capabilities = device.capabilities
        limits = capabilities.power_limits
        step = capabilities.power_step
        minimum = ((limits.min_power + step - 1) // step) * step
        qualification = minimum + step
        stepped = minimum
        if (
            qualification > min(limits.max_power, 45)
            or stepped < limits.min_power
            or qualification <= stepped
        ):
            raise LinkagePreflightError(
                f"device {device.device_id!r} has no safe bootstrap qualification step"
            )
        return qualification, stepped

    @staticmethod
    def _assert_target(device_id: str, state: DeviceState, target: DeviceTarget) -> None:
        expected = {
            "online": True,
            "enabled": target.enabled,
            "power": target.power,
            "mode": target.mode,
            "frequency": target.frequency,
            "linkage": target.linkage,
            "timer_enabled": target.timer_enabled,
            "error": None,
        }
        actual = {
            "online": state.online,
            "enabled": state.enabled,
            "power": state.power,
            "mode": state.mode,
            "frequency": state.frequency,
            "linkage": state.linkage,
            "timer_enabled": state.timer_enabled,
            "error": state.error,
        }
        mismatches = {
            name: {"expected": value, "actual": actual[name]}
            for name, value in expected.items()
            if actual[name] != value
        }
        if mismatches:
            raise LinkageTransactionError(
                f"device {device_id!r} did not apply linkage target: {mismatches}"
            )

    @staticmethod
    def _assert_snapshot_control(
        snapshot: DeviceControlSnapshot,
        state: DeviceState,
        *,
        expected_timer: bool,
    ) -> None:
        target = DeviceTarget(
            enabled=snapshot.enabled,
            power=snapshot.power,
            mode=snapshot.mode,
            frequency=snapshot.frequency,
            linkage=snapshot.linkage,
            timer_enabled=expected_timer,
        )
        TemporaryLinkageController._assert_target(snapshot.device_id, state, target)

    @staticmethod
    def _assert_schedule_unchanged(
        snapshot: DeviceControlSnapshot,
        state: DeviceState,
    ) -> None:
        if schedule_structure_fingerprint(state.schedule) != snapshot.schedule_fingerprint:
            raise LinkageTransactionError(
                f"device {snapshot.device_id!r} schedule structure changed during testing"
            )

    @staticmethod
    def _assert_restore_schedule_unchanged(
        snapshot: DeviceControlSnapshot,
        state: DeviceState,
    ) -> None:
        if schedule_structure_fingerprint(state.schedule) != snapshot.schedule_fingerprint:
            raise _ScheduleStructureChangedDuringRestore(
                f"device {snapshot.device_id!r} schedule structure changed during restore"
            )

    @staticmethod
    def _assert_timer_and_schedule(
        snapshot: DeviceControlSnapshot,
        state: DeviceState,
    ) -> None:
        if state.timer_enabled is not snapshot.timer_enabled:
            raise LinkageRollbackError(f"device {snapshot.device_id!r} TimerON was not restored")
        if not state.online or state.error:
            raise LinkageRollbackError(
                f"device {snapshot.device_id!r} is not healthy after TimerON restore"
            )
        if state.enabled is not snapshot.enabled:
            raise LinkageRollbackError(
                f"device {snapshot.device_id!r} enabled state was not restored"
            )
        if state.linkage is not snapshot.linkage:
            raise LinkageRollbackError(
                f"device {snapshot.device_id!r} linkage role was not restored"
            )
        if schedule_structure_fingerprint(state.schedule) != snapshot.schedule_fingerprint:
            raise _ScheduleStructureChangedDuringRestore(
                f"device {snapshot.device_id!r} schedule structure changed"
            )
