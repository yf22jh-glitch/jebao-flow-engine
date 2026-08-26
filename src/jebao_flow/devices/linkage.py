"""Crash-recoverable native master/slave linkage diagnostics.

The controller deliberately treats a temporary native-linkage test as a saga rather than a
normal group pattern.  Two physical controllers cannot be changed atomically, so every run is
journaled before its first write and always ends in compensating restore work.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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
from jebao_flow.protocol.models import (
    Capability,
    DeviceSchedule,
    DeviceState,
    DeviceTarget,
    LinkageRole,
)

DeviceIdentifier = Annotated[str, StringConstraints(min_length=1)]


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


class LinkageTransactionPhase(StrEnum):
    PREPARED = "prepared"
    APPLYING = "applying"
    ACTIVE = "active"
    ROLLING_BACK = "rolling_back"
    RECOVERY_REQUIRED = "recovery_required"


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

    @model_validator(mode="after")
    def validate_relationship(self) -> Self:
        if self.master_device_id == self.slave_device_id:
            raise ValueError("master and slave devices must be different")
        if self.slave_role not in {
            LinkageRole.SYNC_SLAVE,
            LinkageRole.ASYNC_SLAVE,
        }:
            raise ValueError("slave_role must be sync_slave or async_slave")
        return self


class DeviceControlSnapshot(BaseModel):
    """Control state needed to undo a temporary linkage test exactly."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: DeviceIdentifier
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

    @classmethod
    def from_state(cls, device_id: str, state: DeviceState) -> Self:
        if state.frequency is None:
            raise LinkagePreflightError(f"device {device_id!r} did not report frequency")
        if state.linkage is None:
            raise LinkagePreflightError(f"device {device_id!r} did not report linkage")
        if state.linkage is not LinkageRole.INDEPENDENT:
            raise LinkagePreflightError(
                f"device {device_id!r} must start in independent mode"
            )
        if state.timer_enabled is None:
            raise LinkagePreflightError(f"device {device_id!r} did not report TimerON")
        return cls(
            device_id=device_id,
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

    version: int = Field(default=1, ge=1, le=1)
    operation_id: str = Field(min_length=1)
    phase: LinkageTransactionPhase
    spec: LinkageTestSpec
    snapshots: tuple[DeviceControlSnapshot, ...] = Field(min_length=2, max_length=2)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    error: str | None = None
    failed_device_ids: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.operation_id != self.spec.operation_id:
            raise ValueError("record operation_id must match spec operation_id")
        snapshot_ids = {snapshot.device_id for snapshot in self.snapshots}
        expected_ids = {self.spec.master_device_id, self.spec.slave_device_id}
        if snapshot_ids != expected_ids:
            raise ValueError("record snapshots must cover exactly the master and slave")
        return self


class LinkageTestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    stop_reason: LinkageStopReason
    completed_at: datetime


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
                self._safety_epoch = None
                lease.__exit__(None, None, None)

    async def _run_owned(self, spec: LinkageTestSpec) -> LinkageTestResult:
        pending = self._store.load()
        if pending is not None:
            raise LinkageTransactionBusyError(
                f"linkage recovery {pending.operation_id!r} must complete first"
            )

        record = await self._prepare(spec)
        try:
            self._store.create(record)
        except LinkageJournalClaimError as error:
            raise LinkageTransactionBusyError(
                "another daemon claimed the linkage recovery journal"
            ) from error
        self._active_operation_id = spec.operation_id
        self._stop_event = asyncio.Event()
        operation_error: BaseException | None = None
        stop_reason: LinkageStopReason | None = None

        try:
            record = self._transition(record, LinkageTransactionPhase.APPLYING)
            await self._stage_devices(record)
            await self._activate_relationship(record)
            await self._verify_active_relationship(record)
            record = self._transition(record, LinkageTransactionPhase.ACTIVE)
            stop_reason = await self._monitor_until_stop(record)
        except BaseException as error:
            operation_error = error

        try:
            await self._rollback_uninterruptibly(record)
        except asyncio.CancelledError:
            self._active_operation_id = None
            self._stop_event = None
            raise
        except BaseException as rollback_error:
            self._active_operation_id = None
            self._stop_event = None
            if isinstance(rollback_error, LinkageRollbackError):
                raise
            raise LinkageRollbackError(
                f"linkage operation {spec.operation_id!r} could not be restored"
            ) from rollback_error

        self._active_operation_id = None
        self._stop_event = None
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
        )

    async def stop(self, operation_id: str | None = None) -> bool:
        """Request early restore of the active transaction."""

        if self._stop_event is None or self._active_operation_id is None:
            return False
        if operation_id is not None and operation_id != self._active_operation_id:
            return False
        self._stop_event.set()
        return True

    async def recover_pending(self) -> bool:
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
                return await self._recover_owned()
            finally:
                self._safety_epoch = None
                lease.__exit__(None, None, None)

    async def _recover_owned(self) -> bool:
        record = self._store.load()
        if record is None:
            return False
        self._active_operation_id = record.operation_id
        self._stop_event = asyncio.Event()
        try:
            if record.phase is LinkageTransactionPhase.PREPARED:
                # APPLYING is durably persisted before the first device write. A PREPARED record
                # proves no compensation is needed and must not disturb a schedule that
                # legitimately advanced while the daemon was offline.
                self._store.clear()
            elif (
                self._safety_allows_operation()
                and await self._snapshots_are_restored(record)
            ):
                self._store.clear()
            else:
                await self._rollback_uninterruptibly(record)
        finally:
            self._active_operation_id = None
            self._stop_event = None
        return True

    async def _prepare(self, spec: LinkageTestSpec) -> LinkageTransactionRecord:
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
            if not device.connected:
                raise LinkagePreflightError(f"device {device.device_id!r} is disconnected")
            state = await device.get_state()
            if not state.online:
                raise LinkagePreflightError(f"device {device.device_id!r} is offline")
            if state.error:
                raise LinkagePreflightError(
                    f"device {device.device_id!r} reports an error: {state.error}"
                )
            snapshot = DeviceControlSnapshot.from_state(device.device_id, state)
            self._validate_snapshot(device, snapshot)
            snapshots.append(snapshot)

        now = datetime.now(UTC)
        return LinkageTransactionRecord(
            operation_id=spec.operation_id,
            phase=LinkageTransactionPhase.PREPARED,
            spec=spec,
            snapshots=tuple(snapshots),
            created_at=now,
            updated_at=now,
            expires_at=now + timedelta(seconds=spec.duration_seconds),
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

    async def _stage_devices(self, record: LinkageTransactionRecord) -> None:
        for snapshot in record.snapshots:
            self._require_safety_interlock()
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
                guard=self._safety_allows_operation,
            )

    async def _activate_relationship(self, record: LinkageTransactionRecord) -> None:
        spec = record.spec
        master = self._get_device(spec.master_device_id)
        slave = self._get_device(spec.slave_device_id)
        self._require_safety_interlock()
        await master.write_target(
            DeviceTarget(
                enabled=True,
                power=spec.master_power,
                mode=spec.mode,
                frequency=spec.frequency,
                linkage=LinkageRole.MASTER,
                timer_enabled=False,
            ),
            guard=self._safety_allows_operation,
        )
        self._require_safety_interlock()
        await slave.write_target(
            DeviceTarget(
                enabled=True,
                power=spec.slave_power,
                mode=spec.mode,
                frequency=spec.frequency,
                linkage=spec.slave_role,
                timer_enabled=False,
            ),
            guard=self._safety_allows_operation,
        )

    async def _verify_active_relationship(self, record: LinkageTransactionRecord) -> None:
        self._require_safety_interlock()
        spec = record.spec
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
                power=spec.slave_power,
                mode=spec.mode,
                frequency=spec.frequency,
                linkage=spec.slave_role,
                timer_enabled=False,
            ),
        }
        for device_id, target in expected.items():
            state = await self._get_device(device_id).get_state()
            self._assert_target(device_id, state, target)

    async def _monitor_until_stop(
        self,
        record: LinkageTransactionRecord,
    ) -> LinkageStopReason:
        if self._stop_event is None:
            raise AssertionError("stop event is not initialized")
        loop = asyncio.get_running_loop()
        wall_remaining = (record.expires_at - datetime.now(UTC)).total_seconds()
        monotonic_deadline = loop.time() + max(0, wall_remaining)
        while True:
            self._require_safety_interlock()
            remaining = min(
                (record.expires_at - datetime.now(UTC)).total_seconds(),
                monotonic_deadline - loop.time(),
            )
            if remaining <= 0:
                return LinkageStopReason.TIMEOUT
            interval = min(record.spec.verification_interval_seconds, remaining)
            stop_waiter = asyncio.create_task(self._stop_event.wait())
            safety_waiter = asyncio.create_task(
                self._safety_interlock.wait_until_blocked()
            )
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
                return LinkageStopReason.MANUAL
            if min(
                (record.expires_at - datetime.now(UTC)).total_seconds(),
                monotonic_deadline - loop.time(),
            ) <= 0:
                return LinkageStopReason.TIMEOUT
            # Detect the exact behavior this diagnostic is intended to measure: a native master
            # broadcast must not silently replace the requested per-slave Flow.
            await self._verify_active_relationship(record)

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
        try:
            record = self._transition(record, LinkageTransactionPhase.ROLLING_BACK)
        except Exception:
            # A previously durable record is still usable. Restore first even if the phase update
            # cannot be written; never trade aquarium safety for nicer bookkeeping.
            pass

        if not self._safety_allows_operation():
            await self._defer_restore_for_safety(record)

        errors: dict[str, list[str]] = {
            snapshot.device_id: [] for snapshot in record.snapshots
        }
        detach_order = (record.spec.slave_device_id, record.spec.master_device_id)

        for device_id in detach_order:
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
            except Exception as error:
                errors[device_id].append(f"detach: {error}")

        restored_control: set[str] = set()
        for snapshot in record.snapshots:
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)
            device = self._get_device(snapshot.device_id)
            try:
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
                if (
                    schedule_structure_fingerprint(state.schedule)
                    != snapshot.schedule_fingerprint
                ):
                    raise LinkageRollbackError(
                        "device-local schedule changed during linkage test"
                    )
                restored_control.add(snapshot.device_id)
                errors[snapshot.device_id].clear()
            except Exception as error:
                errors[snapshot.device_id].append(f"restore: {error}")

        for snapshot in record.snapshots:
            if snapshot.device_id not in restored_control:
                continue
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)
            device = self._get_device(snapshot.device_id)
            try:
                # TimerON is restored in the final frame, but the guarded atomic target prevents
                # a safety latch from racing this frame and accidentally re-enabling the pump.
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
                errors[snapshot.device_id].clear()
            except Exception as error:
                errors[snapshot.device_id].append(f"timer restore: {error}")

        failed = {device_id: values for device_id, values in errors.items() if values}
        if failed:
            if not self._safety_allows_operation():
                await self._defer_restore_for_safety(record)
            for device_id in failed:
                await self._try_safe_fallback(self._get_device(device_id), record.spec.frequency)
            message = "; ".join(
                f"{device_id}: {', '.join(values)}"
                for device_id, values in sorted(failed.items())
            )
            recovery_record = record.model_copy(
                update={
                    "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
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

        stop_errors: dict[str, str] = {}
        for device_id in (record.spec.slave_device_id, record.spec.master_device_id):
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
            except Exception as error:
                stop_errors[device_id] = str(error)

        message = "exact restore deferred by safety interlock"
        if stop_errors:
            details = ", ".join(
                f"{device_id}: {error}" for device_id, error in sorted(stop_errors.items())
            )
            message = f"{message}; safe stop errors: {details}"
        recovery_record = record.model_copy(
            update={
                "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
                "updated_at": datetime.now(UTC),
                "error": message,
                # Even a successfully stopped device still needs its exact snapshot restored
                # after the operator explicitly clears the safety latch.
                "failed_device_ids": tuple(
                    sorted(snapshot.device_id for snapshot in record.snapshots)
                ),
            }
        )
        self._store.save(recovery_record)
        raise LinkageRollbackError(
            f"linkage operation {record.operation_id!r}: {message}"
        )

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

    async def _snapshots_are_restored(self, record: LinkageTransactionRecord) -> bool:
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
                return False
        return True

    def _transition(
        self,
        record: LinkageTransactionRecord,
        phase: LinkageTransactionPhase,
    ) -> LinkageTransactionRecord:
        updated = record.model_copy(
            update={
                "phase": phase,
                "updated_at": datetime.now(UTC),
                "error": None,
                "failed_device_ids": (),
            }
        )
        self._store.save(updated)
        return updated

    def _get_device(self, device_id: str) -> JebaoDevice:
        try:
            return self._devices[device_id]
        except KeyError as error:
            raise LinkagePreflightError(f"unknown device {device_id!r}") from error

    def _safety_allows_operation(self) -> bool:
        return (
            self._safety_epoch is not None
            and self._safety_interlock.permitted is True
            and self._safety_interlock.epoch == self._safety_epoch
        )

    def _require_safety_interlock(self) -> None:
        if not self._safety_allows_operation():
            raise LinkageTransactionError("linkage test was stopped by the safety interlock")

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
    def _assert_timer_and_schedule(
        snapshot: DeviceControlSnapshot,
        state: DeviceState,
    ) -> None:
        if state.timer_enabled is not snapshot.timer_enabled:
            raise LinkageRollbackError(
                f"device {snapshot.device_id!r} TimerON was not restored"
            )
        if not state.online or state.error:
            raise LinkageRollbackError(
                f"device {snapshot.device_id!r} is not healthy after TimerON restore"
            )
        if schedule_structure_fingerprint(state.schedule) != snapshot.schedule_fingerprint:
            raise LinkageRollbackError(
                f"device {snapshot.device_id!r} schedule structure changed"
            )
