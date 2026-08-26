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

from jebao_flow.devices.base import JebaoDevice
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
_LOGGER = logging.getLogger(__name__)


class LinkageTransactionError(RuntimeError):
    """Base error for a temporary native-linkage transaction."""


class LinkagePreflightError(LinkageTransactionError):
    """The requested transaction is unsafe or unsupported before any write."""


class LinkageTransactionBusyError(LinkageTransactionError):
    """Another transaction or unfinished recovery owns the devices."""


class LinkageApplyError(LinkageTransactionError):
    """Applying or verifying the temporary relationship failed, but restore succeeded."""


class LinkageRollbackError(LinkageTransactionError):
    """One or more devices could not be restored exactly."""


class LinkageJournalClaimError(LinkageTransactionError):
    """A durable journal already belongs to another daemon or recovery."""


class _ForwardStopRequested(LinkageTransactionError):
    """A normal stop won before the next temporary forward-control frame."""


class _ForwardDeadlineExpired(LinkageTransactionError):
    """The bounded experiment expired before the next forward-control frame."""


class LinkageTransactionPhase(StrEnum):
    PREPARED = "prepared"
    APPLYING = "applying"
    ACTIVE = "active"
    ROLLING_BACK = "rolling_back"
    RECOVERY_REQUIRED = "recovery_required"


class LinkageRecoveryReason(StrEnum):
    """Typed reason why an unfinished transaction remains recovery-latched."""

    SAFETY_INTERLOCK = "safety_interlock"
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
    ) -> None:
        self._devices = dict(devices)
        self._store = store
        self._run_lock = asyncio.Lock()
        self._safety_interlock = safety_interlock
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
                record = await self._reconcile_exactly_restored_devices(record)
                if len(record.restored_device_ids) == len(record.snapshots):
                    self._store.clear()
                else:
                    await self._rollback_uninterruptibly(record)
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
                power_change_sent = True
                _LOGGER.info(
                    "native-linkage requested live slave power change power=%s",
                    expected_slave_power,
                )
            # Detect the exact behavior this diagnostic is intended to measure: a native master
            # broadcast must not silently replace the requested per-slave Flow.
            await self._verify_active_relationship(record, slave_power=expected_slave_power)
            if power_change_sent:
                power_changed = True

    async def _rollback_uninterruptibly(self, record: LinkageTransactionRecord) -> None:
        task = asyncio.create_task(self._rollback(record))
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

    async def _rollback(self, record: LinkageTransactionRecord) -> None:
        # A tripped interlock must become durable before a bookkeeping transition can erase its
        # typed reason, and before any physical safe-stop frame is attempted.
        if not self._safety_allows_operation():
            await self._defer_restore_for_safety(record)

        try:
            record = self._transition(record, LinkageTransactionPhase.ROLLING_BACK)
        except Exception:
            # A previously durable record is still usable. Restore first even if the phase update
            # cannot be written; never trade aquarium safety for nicer bookkeeping.
            pass

        if not self._safety_allows_operation():
            await self._defer_restore_for_safety(record)

        record = await self._reconcile_exactly_restored_devices(record)
        already_restored = set(record.restored_device_ids)
        pending_snapshots = tuple(
            snapshot for snapshot in record.snapshots if snapshot.device_id not in already_restored
        )
        if not pending_snapshots:
            self._store.clear()
            return

        errors: dict[str, list[str]] = {snapshot.device_id: [] for snapshot in pending_snapshots}
        detach_order = (record.spec.slave_device_id, record.spec.master_device_id)

        for device_id in detach_order:
            if device_id in already_restored:
                continue
            device = self._get_device(device_id)
            try:
                await device.write_target(
                    DeviceTarget(
                        enabled=True,
                        power=self._safe_power(device),
                        mode="constant",
                        frequency=record.spec.frequency,
                        linkage=LinkageRole.INDEPENDENT,
                        timer_enabled=False,
                    ),
                    guard=self._safety_allows_operation,
                )
            except Exception:
                errors[device_id].append("detach_failed")

        restored_control: set[str] = set()
        for snapshot in pending_snapshots:
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)
            device = self._get_device(snapshot.device_id)
            try:
                if snapshot.timer_enabled and snapshot.enabled:
                    # Keep the device at the already-verified safe detach target until the final
                    # atomic manual-fallback + TimerON frame. Writing a saved high fallback with
                    # TimerOFF would briefly expose that power before the schedule resumes.
                    state = await device.get_state()
                    if not state.online or state.error or state.timer_enabled is not False:
                        raise LinkageRollbackError(
                            "device is not safely paused before scheduled restore"
                        )
                    self._assert_schedule_unchanged(snapshot, state)
                else:
                    await device.write_target(
                        DeviceTarget(
                            enabled=True,
                            power=snapshot.power,
                            mode=snapshot.mode,
                            frequency=snapshot.frequency,
                            linkage=snapshot.linkage,
                            timer_enabled=False,
                        ),
                        guard=self._safety_allows_operation,
                    )
                    if not snapshot.enabled:
                        await device.set_enabled(False)
                    state = await device.get_state()
                    self._assert_snapshot_control(snapshot, state, expected_timer=False)
                    self._assert_schedule_unchanged(snapshot, state)
                restored_control.add(snapshot.device_id)
                errors[snapshot.device_id].clear()
            except Exception:
                errors[snapshot.device_id].append("control_restore_failed")

        for snapshot in pending_snapshots:
            if snapshot.device_id not in restored_control:
                continue
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)
            device = self._get_device(snapshot.device_id)
            try:
                # Restore the saved manual fallback and TimerON in one guarded frame. For a
                # scheduled device this moves directly from safe-low TimerOFF to schedule
                # authority without exposing a saved high manual fallback between frames.
                await device.write_target(
                    DeviceTarget(
                        enabled=snapshot.enabled,
                        power=snapshot.power,
                        mode=snapshot.mode,
                        frequency=snapshot.frequency,
                        linkage=snapshot.linkage,
                        timer_enabled=snapshot.timer_enabled,
                    ),
                    guard=self._safety_allows_operation,
                )
                state = await device.get_state()
                self._assert_timer_and_schedule(snapshot, state)
                record = self._mark_device_restored(record, snapshot.device_id)
                errors[snapshot.device_id].clear()
            except Exception:
                errors[snapshot.device_id].append("timer_restore_failed")

        record = await self._reconcile_exactly_restored_devices(record)
        exactly_restored = set(record.restored_device_ids)
        for snapshot in record.snapshots:
            if snapshot.device_id not in exactly_restored:
                errors.setdefault(snapshot.device_id, []).append("final_verification_failed")

        failed = {device_id: values for device_id, values in errors.items() if values}
        if failed:
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)
            for device_id in failed:
                await self._try_safe_fallback(self._get_device(device_id), record.spec.frequency)
            message = "; ".join(
                f"{device_id}: {','.join(values)}" for device_id, values in sorted(failed.items())
            )
            recovery_record = record.model_copy(
                update={
                    "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                    "recovery_reason": LinkageRecoveryReason.RESTORE_FAILED,
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

    async def _try_safe_fallback(self, device: JebaoDevice, frequency: int) -> None:
        try:
            await device.write_target(
                DeviceTarget(
                    enabled=True,
                    power=self._safe_power(device),
                    mode="constant",
                    frequency=frequency,
                    linkage=LinkageRole.INDEPENDENT,
                    timer_enabled=False,
                ),
                guard=self._safety_allows_operation,
            )
        except Exception:
            # One bounded attempt only. Recovery remains latched in the journal and a later
            # reconnect can call recover_pending() without causing a command storm.
            return

    async def _reconcile_exactly_restored_devices(
        self,
        record: LinkageTransactionRecord,
    ) -> LinkageTransactionRecord:
        """Freshly reconcile durable progress before skipping writes or clearing the journal."""

        exactly_restored: list[str] = []
        for snapshot in record.snapshots:
            try:
                state = await self._get_device(snapshot.device_id).get_state()
                self._assert_snapshot_control(
                    snapshot,
                    state,
                    expected_timer=snapshot.timer_enabled,
                )
                self._assert_timer_and_schedule(snapshot, state)
            except Exception:
                continue
            exactly_restored.append(snapshot.device_id)

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
            raise LinkageRollbackError(f"device {snapshot.device_id!r} schedule structure changed")
