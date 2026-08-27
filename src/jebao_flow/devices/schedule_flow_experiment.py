"""Composed attended experiment for per-slot power on a native async slave.

The outer native-linkage transaction owns the original control state and first establishes a
safe, independent TimerOFF baseline.  Inside that baseline, the byte-exact schedule transaction
qualifies one unused slot, stages a two-segment day, and invokes the existing role-only schedule
boundary controller.  Every normal and exceptional exit unwinds in the inverse order:
roles -> TimerOFF -> original 48 slots -> original controls.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from jebao_flow.devices.base import JebaoDevice
from jebao_flow.devices.linkage import (
    LinkageJournalStore,
    LinkageRecoveryAuthority,
    LinkageRollbackError,
    LinkageSafetyInterlock,
    LinkageStopReason,
    LinkageTestSpec,
    LinkageTransactionBusyError,
    LinkageTransactionError,
    LinkageTransactionRecord,
    TemporaryLinkageController,
    schedule_structure_fingerprint,
)
from jebao_flow.devices.schedule_linkage import (
    PrerequisiteAuthorizer,
    ScheduleActiveLinkageController,
    ScheduleLinkageJournalStore,
    ScheduleLinkageRecord,
    ScheduleLinkageResult,
    ScheduleLinkageSample,
    ScheduleLinkageSpec,
)
from jebao_flow.devices.schedule_transaction import (
    DeviceSchedulePatch,
    ObservationCompletion,
    ScheduleSlotPatch,
    SnapshotAuthorizer,
    TemporaryScheduleController,
    TemporaryScheduleErrorCode,
    TemporaryScheduleJournalStore,
    TemporaryScheduleKind,
    TemporaryScheduleObserverUnstoppableError,
    TemporaryScheduleRecord,
    TemporaryScheduleResult,
    TemporaryScheduleSpec,
    behavior_neutral_unused_slot_patch,
)
from jebao_flow.protocol.models import DeviceTarget, LinkageRole, ScheduleEntry
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    encode_local_wavemaker_pro_schedule_entry,
)

WallTime = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"),
]


class ScheduleFlowExperimentSpec(BaseModel):
    """One deliberate Constant -> Sine boundary with distinguishable slave power."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    qualification_operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    master_device_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    slave_device_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    boundary_time: WallTime
    master_before_flow: int = Field(default=31, ge=0, le=100)
    slave_before_flow: int = Field(default=32, ge=0, le=100)
    master_after_flow: int = Field(default=35, ge=0, le=100)
    slave_after_flow: int = Field(default=40, ge=0, le=100)
    sine_frequency: int = Field(default=30, ge=0, le=100)
    safe_frequency: int = Field(default=20, ge=0, le=100)
    observation_window_seconds: float = Field(default=600, gt=0, le=600)
    post_boundary_stability_seconds: float = Field(default=300, ge=0, le=300)
    verification_interval_seconds: float = Field(default=2, gt=0, le=10)
    minimum_lead_seconds: float = Field(default=60, ge=10, le=180)
    ambiguous_band_seconds: float = Field(default=1, ge=0.1, le=5)
    maximum_clock_skew_seconds: float = Field(default=2, ge=0.1, le=10)
    clock_advance_tolerance_seconds: float = Field(default=2, ge=0.1, le=10)
    sentinel_qualification: bool = True

    @model_validator(mode="after")
    def validate_experiment(self) -> Self:
        if self.master_device_id == self.slave_device_id:
            raise ValueError("master and slave devices must differ")
        if self.boundary_time == "00:00":
            raise ValueError("the experiment boundary cannot be midnight")
        if self.slave_before_flow == self.slave_after_flow:
            raise ValueError("the slave schedule must request a different A and B flow")
        if self.slave_before_flow == self.master_after_flow:
            raise ValueError("the prior slave flow must differ from the next master flow")
        if self.master_after_flow == self.slave_after_flow:
            raise ValueError("the post-boundary slave flow must differ from the master")
        required_after = (
            self.post_boundary_stability_seconds
            + 2 * self.ambiguous_band_seconds
            + 4 * self.verification_interval_seconds
        )
        if self.observation_window_seconds <= self.minimum_lead_seconds + required_after:
            raise ValueError("the observation window cannot contain setup and stable evidence")
        return self

    def outer_linkage_spec(self) -> LinkageTestSpec:
        """Build the journal owner that pauses and ultimately restores original controls."""

        duration = min(900.0, self.observation_window_seconds + 240.0)
        return LinkageTestSpec(
            operation_id=self.operation_id,
            master_device_id=self.master_device_id,
            slave_device_id=self.slave_device_id,
            slave_role=LinkageRole.ASYNC_SLAVE,
            mode="constant",
            master_power=self.master_before_flow,
            slave_power=self.slave_before_flow,
            frequency=self.safe_frequency,
            duration_seconds=duration,
            verification_interval_seconds=self.verification_interval_seconds,
            bootstrap_active_schedule=True,
        )

    def role_observation_spec(self) -> ScheduleLinkageSpec:
        return ScheduleLinkageSpec(
            operation_id=f"{self.operation_id}_roles",
            qualification_operation_id=self.qualification_operation_id,
            master_device_id=self.master_device_id,
            slave_device_id=self.slave_device_id,
            observation_window_seconds=self.observation_window_seconds,
            verification_interval_seconds=self.verification_interval_seconds,
            minimum_lead_seconds=self.minimum_lead_seconds,
            ambiguous_band_seconds=self.ambiguous_band_seconds,
            post_boundary_stability_seconds=self.post_boundary_stability_seconds,
            observe_slave_after_tuple_variance=True,
            maximum_clock_skew_seconds=self.maximum_clock_skew_seconds,
            clock_advance_tolerance_seconds=self.clock_advance_tolerance_seconds,
        )

    def temporary_schedule_spec(self) -> TemporaryScheduleSpec:
        return TemporaryScheduleSpec(
            operation_id=f"{self.operation_id}_schedule",
            device_patches=(
                _two_segment_patch(
                    self.master_device_id,
                    boundary_time=self.boundary_time,
                    before_flow=self.master_before_flow,
                    after_flow=self.master_after_flow,
                    sine_frequency=self.sine_frequency,
                ),
                _two_segment_patch(
                    self.slave_device_id,
                    boundary_time=self.boundary_time,
                    before_flow=self.slave_before_flow,
                    after_flow=self.slave_after_flow,
                    sine_frequency=self.sine_frequency,
                ),
            ),
            forward_timeout_seconds=90,
            observation_timeout_seconds=min(900, self.observation_window_seconds + 120),
            recovery_authority_seconds=2100,
        )


class ScheduleFlowOutcome(StrEnum):
    """Classification of the slave's effective post-boundary Auto tuple."""

    PER_SLOT_POWER_VERIFIED = "per_slot_power_verified"
    SLAVE_FLOW_FIXED_AT_PREVIOUS = "slave_flow_fixed_at_previous"
    SLAVE_FLOW_FOLLOWED_MASTER = "slave_flow_followed_master"
    UNEXPECTED_EFFECTIVE_STATE = "unexpected_effective_state"


class ScheduleFlowExperimentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    sentinel_qualified: bool
    outcome: ScheduleFlowOutcome
    last_after_sample: ScheduleLinkageSample
    schedule_transition_verified: bool
    stable_slave_tuple_observed: Literal[True] = True
    stable_observation_seconds: float = Field(ge=0, le=300)
    temporary_schedule_restored: Literal[True] = True
    original_controls_restored: Literal[True] = True
    completed_at: datetime


class ScheduleFlowExperimentController(TemporaryLinkageController):
    """Compose three existing recovery domains under one deployment-wide caller lease."""

    def __init__(
        self,
        devices: Mapping[str, JebaoDevice],
        outer_store: LinkageJournalStore,
        schedule_store: TemporaryScheduleJournalStore,
        role_store: ScheduleLinkageJournalStore,
        *,
        safety_interlock: LinkageSafetyInterlock,
        prerequisite_authorizer: PrerequisiteAuthorizer,
        role_sample_observer: Callable[[ScheduleLinkageSample], None] | None = None,
        schedule_snapshot_authorizer: SnapshotAuthorizer | None = None,
    ) -> None:
        super().__init__(devices, outer_store, safety_interlock=safety_interlock)
        self._experiment_devices = dict(devices)
        self._schedule_store = schedule_store
        self._role_store = role_store
        self._schedule_controller = TemporaryScheduleController(
            devices,
            schedule_store,
            safety_interlock=safety_interlock,
            snapshot_authorizer=schedule_snapshot_authorizer,
        )
        self._role_controller = ScheduleActiveLinkageController(
            devices,
            role_store,
            prerequisite_authorizer=prerequisite_authorizer,
            safety_interlock=safety_interlock,
            sample_observer=self._observe_role_sample,
        )
        self._external_role_sample_observer = role_sample_observer
        self._experiment_spec: ScheduleFlowExperimentSpec | None = None
        self._sentinel_result: TemporaryScheduleResult | None = None
        self._temporary_result: TemporaryScheduleResult | None = None
        self._role_result: ScheduleLinkageResult | None = None
        self._role_error: BaseException | None = None
        self._last_role_sample: ScheduleLinkageSample | None = None
        self._experiment_entry_lock = asyncio.Lock()
        self._schedule_restore_blocked = False

    async def run_experiment(
        self,
        spec: ScheduleFlowExperimentSpec,
    ) -> ScheduleFlowExperimentResult:
        # The parent rejects concurrent runs only after this method would otherwise overwrite
        # per-run composition state. Own a separate entry lock before touching that state.
        if self._experiment_entry_lock.locked():
            raise LinkageTransactionBusyError(
                "another schedule-flow experiment is already running"
            )
        async with self._experiment_entry_lock:
            if self._schedule_store.load() is not None or self._role_store.load() is not None:
                raise LinkageTransactionBusyError(
                    "unfinished nested schedule recovery blocks a new experiment"
                )
            self._experiment_spec = spec
            self._sentinel_result = None
            self._temporary_result = None
            self._role_result = None
            self._role_error = None
            self._last_role_sample = None
            self._schedule_restore_blocked = False
            try:
                outer_result = await super().run(spec.outer_linkage_spec())
                if self._temporary_result is None or self._role_result is None:
                    raise LinkageTransactionError(
                        "schedule-flow experiment produced no verified result"
                    )
                if not self._role_result.schedule_transition_verified:
                    raise LinkageTransactionError("schedule-flow transition was not verified")
                if spec.sentinel_qualification and self._sentinel_result is None:
                    raise LinkageTransactionError("schedule wire qualification did not complete")
                sample = self._last_role_sample
                if sample is None or sample.phase != "after":
                    raise LinkageTransactionError("schedule-flow experiment has no after sample")
                outcome = classify_schedule_flow_sample(spec, sample)
                return ScheduleFlowExperimentResult(
                    operation_id=spec.operation_id,
                    sentinel_qualified=self._sentinel_result is not None,
                    outcome=outcome,
                    last_after_sample=sample,
                    schedule_transition_verified=(
                        outcome is ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED
                    ),
                    stable_observation_seconds=spec.post_boundary_stability_seconds,
                    completed_at=outer_result.completed_at,
                )
            finally:
                self._experiment_spec = None

    @property
    def last_role_sample(self) -> ScheduleLinkageSample | None:
        return self._last_role_sample

    @property
    def last_role_result(self) -> ScheduleLinkageResult | None:
        """Return completed stable-boundary evidence for durable attended CLI handoff."""

        return self._role_result

    def _observe_role_sample(self, sample: ScheduleLinkageSample) -> None:
        self._last_role_sample = sample
        if self._external_role_sample_observer is not None:
            self._external_role_sample_observer(sample)

    async def _activate_relationship(self, record: LinkageTransactionRecord) -> None:
        """Run the nested experiment while the outer transaction remains safely TimerOFF."""

        spec = self._require_experiment(record)
        if spec.sentinel_qualification:
            self._sentinel_result = await self._schedule_controller.run(
                await self._sentinel_spec(spec)
            )

        async def observe(_record: TemporaryScheduleRecord) -> ObservationCompletion:
            role_error: BaseException | None = None
            try:
                await self._arm_temporary_schedule(record)
                preflight = await self._role_controller.preflight(
                    spec.role_observation_spec()
                )
                self._role_result = await self._role_controller.run(preflight)
            except BaseException as error:
                role_error = error
            finally:
                await self._disarm_temporary_schedule_uninterruptibly(record)
                await self._clear_role_journal_before_schedule_restore(record)
            self._role_error = role_error
            return ObservationCompletion.DISARM_VERIFIED

        try:
            self._temporary_result = await self._schedule_controller.run(
                spec.temporary_schedule_spec(),
                observe=observe,
            )
        except BaseException:
            # If the exact schedule journal survives, the controller could not prove that the
            # original 48 slots are back. Refuse the parent's TimerON control restore until an
            # attended recovery first disarms both devices and restores that journal.
            try:
                self._schedule_restore_blocked = self._schedule_store.load() is not None
            except BaseException:
                self._schedule_restore_blocked = True
            raise
        if self._role_error is not None:
            raise self._role_error

    async def _rollback_uninterruptibly(
        self,
        record: LinkageTransactionRecord,
        *,
        schedule_change_ids: set[str] | None = None,
        read_failure_ids: set[str] | None = None,
    ) -> None:
        task = asyncio.create_task(
            self._rollback_composed(
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
                cancellation_received = True
        task.result()
        if cancellation_received:
            raise asyncio.CancelledError

    async def _rollback_composed(
        self,
        record: LinkageTransactionRecord,
        *,
        schedule_change_ids: set[str] | None,
        read_failure_ids: set[str] | None,
    ) -> None:
        try:
            pending_schedule = self._schedule_store.load()
        except BaseException:
            pending_schedule = None
            self._schedule_restore_blocked = True
        if self._schedule_restore_blocked or pending_schedule is not None:
            try:
                await self._recover_nested_before_outer(record)
            except BaseException:
                # Do not merely skip the parent's rollback: that would discard its final chance
                # to detach a controller still running the staged TimerON schedule. Latch this
                # run locally and invoke the audited two-device OFF compensation, which leaves
                # the outer journal recovery-required and never restores saved TimerON state.
                self._safety_interlock.trip()
                await self._defer_restore_for_safety(record)
        await super()._rollback_uninterruptibly(
            record,
            schedule_change_ids=schedule_change_ids,
            read_failure_ids=read_failure_ids,
        )

    async def recover_experiment(self) -> bool:
        """Recover nested roles, safe TimerOFF, exact slots, then original controls.

        The caller must hold the deployment-wide attended hardware lease. This method never
        resumes the experiment and deliberately gives the schedule journal precedence over the
        outer TimerON snapshot.
        """

        if self._experiment_entry_lock.locked():
            raise LinkageTransactionBusyError(
                "another schedule-flow experiment is already running"
            )
        async with self._experiment_entry_lock:
            schedule_record = self._schedule_store.load()
            outer_record = self._store.load()
            role_record = self._role_store.load()
            if (schedule_record is not None or role_record is not None) and outer_record is None:
                raise LinkageRollbackError(
                    "nested schedule recovery has no owning control snapshot"
                )
            recovered = False
            if outer_record is not None and (
                schedule_record is not None or role_record is not None
            ):
                self._validate_nested_recovery_ownership(
                    outer_record,
                    schedule_record,
                    role_record,
                )
                self._validate_recovery_bindings(outer_record)
                self._active_operation_id = outer_record.operation_id
                try:
                    recovered = await self._recover_nested_before_outer(outer_record)
                finally:
                    self._active_operation_id = None
            if self._schedule_store.load() is not None:
                raise LinkageRollbackError("temporary schedule recovery remains incomplete")
            self._schedule_restore_blocked = False
            recovered = (
                await super().recover_pending(authority=LinkageRecoveryAuthority.ATTENDED)
                or recovered
            )
            return recovered

    async def _recover_nested_before_outer(
        self,
        outer_record: LinkageTransactionRecord,
    ) -> bool:
        schedule_record = self._schedule_store.load()
        role_record = self._role_store.load()
        self._validate_nested_recovery_ownership(
            outer_record,
            schedule_record,
            role_record,
        )
        recovered = False
        controls_disarmed = False
        if role_record is not None:
            await self._disarm_temporary_schedule_uninterruptibly(outer_record)
            controls_disarmed = True
            recovered = (
                await self._role_controller.finalize_externally_disarmed(
                    role_record.operation_id
                )
                or recovered
            )
            if self._role_store.load() is not None:
                raise LinkageRollbackError(
                    "role-only recovery remains incomplete before schedule restore"
                )
        if schedule_record is not None:
            if not controls_disarmed:
                await self._disarm_temporary_schedule_uninterruptibly(outer_record)
            recovered = (
                await self._schedule_controller.manual_recover(
                    disarm_verified=True,
                    observer_stopped=True,
                )
                or recovered
            )
        # Never attempt role recovery after the original schedule has been put back: that journal
        # is bound to the temporary schedule fingerprint and must have closed before restoration.
        if self._role_store.load() is not None:
            raise LinkageRollbackError(
                "role-only recovery appeared after schedule restoration"
            )
        if self._schedule_store.load() is not None or self._role_store.load() is not None:
            raise LinkageRollbackError("nested schedule recovery remains incomplete")
        self._schedule_restore_blocked = False
        return recovered

    async def _clear_role_journal_before_schedule_restore(
        self,
        outer_record: LinkageTransactionRecord,
    ) -> None:
        """Prove the nested role saga terminal while its temporary schedule still exists."""

        try:
            role_record = self._role_store.load()
            if role_record is None:
                return
            schedule_record = self._schedule_store.load()
            self._validate_nested_recovery_ownership(
                outer_record,
                schedule_record,
                role_record,
            )
            await self._role_controller.finalize_externally_disarmed(
                role_record.operation_id
            )
            if self._role_store.load() is not None:
                raise LinkageRollbackError("role-only recovery remains incomplete")
        except BaseException:
            # The temporary image must remain installed while role recovery still refers to its
            # fingerprint.  This typed error prevents the schedule transaction from clearing its
            # journal or rewriting the original 48 slots prematurely.
            raise TemporaryScheduleObserverUnstoppableError(
                TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED
            ) from None

    @staticmethod
    def _validate_nested_recovery_ownership(
        outer: LinkageTransactionRecord,
        schedule: TemporaryScheduleRecord | None,
        role: ScheduleLinkageRecord | None,
    ) -> None:
        expected_ids = (outer.spec.master_device_id, outer.spec.slave_device_id)
        if (
            not outer.spec.bootstrap_active_schedule
            or outer.spec.slave_role is not LinkageRole.ASYNC_SLAVE
            or tuple(snapshot.device_id for snapshot in outer.snapshots) != expected_ids
        ):
            raise LinkageRollbackError("outer recovery record is not a schedule-flow owner")
        outer_bindings = {
            snapshot.device_id: snapshot.physical_binding for snapshot in outer.snapshots
        }

        if schedule is not None:
            suffix = (
                "_sentinel"
                if schedule.spec.kind is TemporaryScheduleKind.SENTINEL_QUALIFICATION
                else "_schedule"
            )
            patch_ids = tuple(patch.device_id for patch in schedule.spec.device_patches)
            if (
                schedule.operation_id != f"{outer.operation_id}{suffix}"
                or patch_ids != expected_ids
                or tuple(snapshot.device_id for snapshot in schedule.snapshots) != expected_ids
                or any(
                    snapshot.physical_binding != outer_bindings[snapshot.device_id]
                    for snapshot in schedule.snapshots
                )
            ):
                raise LinkageRollbackError("temporary schedule journal owner mismatch")

        if role is not None:
            if (
                schedule is None
                or schedule.spec.kind is not TemporaryScheduleKind.FIELD_OBSERVATION
            ):
                raise LinkageRollbackError("role recovery has no owning field schedule")
            if (
                role.operation_id != f"{outer.operation_id}_roles"
                or (role.spec.master_device_id, role.spec.slave_device_id) != expected_ids
                or tuple(snapshot.device_id for snapshot in role.snapshots) != expected_ids
                or any(
                    snapshot.physical_binding != outer_bindings[snapshot.device_id]
                    for snapshot in role.snapshots
                )
            ):
                raise LinkageRollbackError("schedule-linkage journal owner mismatch")

    async def _verify_active_relationship(
        self,
        record: LinkageTransactionRecord,
        *,
        slave_power: int | None = None,
        live_slave_power_change: bool = False,
    ) -> None:
        """Verify the nested workflows left the outer safe baseline and original schedule."""

        if slave_power is not None or live_slave_power_change:
            raise LinkageTransactionError("unexpected live-power verification path")
        snapshots = {snapshot.device_id: snapshot for snapshot in record.snapshots}
        for device_id, expected_power in (
            (record.spec.master_device_id, record.spec.master_power),
            (record.spec.slave_device_id, record.spec.slave_power),
        ):
            state = await self._get_device(device_id).get_state()
            if (
                not state.online
                or state.error is not None
                or not state.enabled
                or state.power != expected_power
                or state.mode != record.spec.mode
                or state.frequency != record.spec.frequency
                or state.linkage is not LinkageRole.INDEPENDENT
                or state.timer_enabled is not False
                or schedule_structure_fingerprint(state.schedule)
                != snapshots[device_id].schedule_fingerprint
            ):
                raise LinkageTransactionError(
                    "schedule-flow experiment did not return to its safe outer baseline"
                )

    async def _monitor_until_stop(
        self,
        record: LinkageTransactionRecord,
    ) -> tuple[LinkageStopReason, bool]:
        del record
        return LinkageStopReason.TIMEOUT, False

    async def _sentinel_spec(
        self,
        spec: ScheduleFlowExperimentSpec,
    ) -> TemporaryScheduleSpec:
        patches: list[DeviceSchedulePatch] = []
        for device_id in (spec.master_device_id, spec.slave_device_id):
            image = await self._get_device(device_id).read_schedule_image()
            patches.append(
                DeviceSchedulePatch(
                    device_id=device_id,
                    slots=(behavior_neutral_unused_slot_patch(image),),
                )
            )
        return TemporaryScheduleSpec(
            operation_id=f"{spec.operation_id}_sentinel",
            kind=TemporaryScheduleKind.SENTINEL_QUALIFICATION,
            device_patches=tuple(patches),
            forward_timeout_seconds=90,
            recovery_authority_seconds=1800,
        )

    async def _arm_temporary_schedule(self, record: LinkageTransactionRecord) -> None:
        for device_id, power in (
            (record.spec.master_device_id, record.spec.master_power),
            (record.spec.slave_device_id, record.spec.slave_power),
        ):
            self._require_forward_write(record)
            await self._run_forward_operation(
                record,
                self._get_device(device_id).write_target(
                    DeviceTarget(
                        enabled=True,
                        power=power,
                        mode=record.spec.mode,
                        frequency=record.spec.frequency,
                        linkage=LinkageRole.INDEPENDENT,
                        timer_enabled=True,
                    ),
                    guard=lambda current=record: self._forward_write_allowed(current),
                ),
            )

    async def _disarm_temporary_schedule_uninterruptibly(
        self,
        record: LinkageTransactionRecord,
    ) -> None:
        task = asyncio.create_task(self._disarm_temporary_schedule(record))
        cancellation_received = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancellation_received = True
        task.result()
        if cancellation_received:
            raise asyncio.CancelledError

    async def _disarm_temporary_schedule(self, record: LinkageTransactionRecord) -> None:
        # Slave first prevents a still-attached slave from following a master while TimerON is
        # being removed. This is a compensating safety write and remains authorized after the
        # forward observation deadline, but only for the still-owned operation.
        targets = (
            (record.spec.slave_device_id, record.spec.slave_power),
            (record.spec.master_device_id, record.spec.master_power),
        )
        cancellation_received = False
        for device_id, power in targets:
            try:
                await self._get_device(device_id).write_target(
                    DeviceTarget(
                        enabled=True,
                        power=power,
                        mode=record.spec.mode,
                        frequency=record.spec.frequency,
                        linkage=LinkageRole.INDEPENDENT,
                        timer_enabled=False,
                    ),
                    guard=lambda operation_id=record.operation_id: (
                        self.active_operation_id == operation_id
                    ),
                )
            except asyncio.CancelledError:
                cancellation_received = True
            except BaseException:
                # A lost acknowledgement can still mean the command applied. Always attempt the
                # other controller, then make the schedule-restore decision from fresh state.
                pass

        safe = True
        for device_id, power in targets:
            device = self._get_device(device_id)
            try:
                # A successful write can leave a paired 0x03 reply queued after a 0x04 report.
                # Prove TimerOFF from a new authenticated stream, never the write stream.
                await device.disconnect()
                await device.connect()
                state = await device.get_state()
            except asyncio.CancelledError:
                cancellation_received = True
                safe = False
                continue
            except BaseException:
                safe = False
                continue
            safe = safe and (
                state.online
                and state.error is None
                and state.enabled
                and state.power == power
                and state.mode == record.spec.mode
                and state.frequency == record.spec.frequency
                and state.linkage is LinkageRole.INDEPENDENT
                and state.timer_enabled is False
            )

        if not safe:
            # This exact error tells the schedule transaction to retain its staged image and
            # recovery journal. Rewriting 48 slots while either TimerON is unproven is unsafe.
            raise TemporaryScheduleObserverUnstoppableError(
                TemporaryScheduleErrorCode.OBSERVER_NOT_STOPPED
            )
        if cancellation_received:
            raise asyncio.CancelledError

    def _require_experiment(
        self,
        record: LinkageTransactionRecord,
    ) -> ScheduleFlowExperimentSpec:
        spec = self._experiment_spec
        if spec is None or spec.operation_id != record.operation_id:
            raise LinkageTransactionError("schedule-flow experiment identity changed")
        return spec


def _two_segment_patch(
    device_id: str,
    *,
    boundary_time: str,
    before_flow: int,
    after_flow: int,
    sine_frequency: int,
) -> DeviceSchedulePatch:
    before = ScheduleEntry(
        slot=0,
        start="00:00",
        end=boundary_time,
        mode="constant",
        mode_code=2,
        parameters={
            "flow": before_flow,
            "frequency": 0,
            "feed_time": 0,
            "custom_frequency": 0,
        },
    )
    after = ScheduleEntry(
        slot=1,
        start=boundary_time,
        end="24:00",
        mode="sine",
        mode_code=1,
        parameters={
            "flow": after_flow,
            "frequency": sine_frequency,
            "feed_time": 0,
            "custom_frequency": 0,
        },
    )
    wires = (
        encode_local_wavemaker_pro_schedule_entry(before),
        encode_local_wavemaker_pro_schedule_entry(after),
    )
    slots = tuple(
        ScheduleSlotPatch.from_wire(
            slot,
            wires[slot] if slot < len(wires) else LOCAL_WAVEMAKER_PRO_UNUSED_EE,
        )
        for slot in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT)
    )
    return DeviceSchedulePatch(device_id=device_id, slots=slots)


def classify_schedule_flow_sample(
    spec: ScheduleFlowExperimentSpec,
    sample: ScheduleLinkageSample,
) -> ScheduleFlowOutcome:
    """Classify effective slave evidence without relying on the app's fixed Flow control."""

    if (
        sample.phase != "after"
        or sample.master_manual_power != spec.master_before_flow
        or sample.slave_manual_power != spec.slave_before_flow
        or (
            sample.master.mode,
            sample.master.flow,
            sample.master.frequency,
        )
        != ("sine", spec.master_after_flow, spec.sine_frequency)
    ):
        return ScheduleFlowOutcome.UNEXPECTED_EFFECTIVE_STATE
    if (
        sample.slave.mode,
        sample.slave.flow,
        sample.slave.frequency,
    ) == ("sine", spec.slave_after_flow, spec.sine_frequency):
        return ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED
    if sample.slave.flow == spec.slave_before_flow:
        return ScheduleFlowOutcome.SLAVE_FLOW_FIXED_AT_PREVIOUS
    if sample.slave.flow == sample.master.flow:
        return ScheduleFlowOutcome.SLAVE_FLOW_FOLLOWED_MASTER
    return ScheduleFlowOutcome.UNEXPECTED_EFFECTIVE_STATE


__all__ = [
    "ScheduleFlowExperimentController",
    "ScheduleFlowExperimentResult",
    "ScheduleFlowExperimentSpec",
    "ScheduleFlowOutcome",
    "classify_schedule_flow_sample",
]
