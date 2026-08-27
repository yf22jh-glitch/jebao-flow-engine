"""Composed attended experiment for per-slot power on a native async slave.

The outer native-linkage transaction owns the original control state and first establishes a
safe, independent TimerOFF baseline.  Inside that baseline, the byte-exact schedule transaction
qualifies one unused slot, stages a two-segment day, and invokes the existing role-only schedule
boundary controller.  Every normal and exceptional exit unwinds in the inverse order:
roles -> TimerOFF -> original 48 slots -> original controls.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from jebao_flow.devices.base import JebaoDevice
from jebao_flow.devices.linkage import (
    DeviceControlSnapshot,
    LinkageDiagnosticEvent,
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
    ScheduleLinkageBusyError,
    ScheduleLinkageJournalStore,
    ScheduleLinkagePreflightError,
    ScheduleLinkageRecord,
    ScheduleLinkageResult,
    ScheduleLinkageRunFailure,
    ScheduleLinkageRunProgressEvent,
    ScheduleLinkageRunProgressKind,
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
    TemporaryScheduleProgressEvent,
    TemporaryScheduleProgressKind,
    TemporaryScheduleRecord,
    TemporaryScheduleResult,
    TemporaryScheduleSpec,
    behavior_neutral_unused_slot_patch,
)
from jebao_flow.protocol.models import (
    DeviceState,
    DeviceTarget,
    LinkageRole,
    ScheduleEntry,
)
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    decode_local_wavemaker_pro_slot_wire,
    encode_local_wavemaker_pro_schedule_entry,
)

WallTime = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"),
]

_FIELD_SCHEDULE_END = "23:59"
_FIELD_SCHEDULE_END_SECONDS = 23 * 60 * 60 + 59 * 60
_ROLE_PREFLIGHT_SETTLE_SECONDS = 1.0
_TIMER_ARM_MINIMUM_LEAD_SECONDS = 180.0
_STAGED_A_CONVERGENCE_TIMEOUT_SECONDS = 30.0
_STAGED_A_CONVERGENCE_RETRY_SECONDS = 1.0
_STAGED_CURRENT_AUTO_MODES = frozenset({"constant", "pulse", "sine", "feed"})
_SCHEDULE_FLOW_TEST_MAX_POWER = 45

SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT = 64
SCHEDULE_FLOW_STAGE_EVENT_LIMIT = SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT + 1


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
    observation_window_seconds: float = Field(default=600, gt=0, le=630)
    post_boundary_stability_seconds: float = Field(default=300, ge=0, le=300)
    verification_interval_seconds: float = Field(default=2, gt=0, le=10)
    minimum_lead_seconds: float = Field(default=60, ge=10, le=180)
    ambiguous_band_seconds: float = Field(default=1, ge=0.1, le=5)
    maximum_clock_skew_seconds: float = Field(default=2, ge=0.1, le=10)
    clock_advance_tolerance_seconds: float = Field(default=2, ge=0.1, le=10)
    sentinel_qualification: bool = True
    sentinel_only: bool = False

    @model_validator(mode="after")
    def validate_experiment(self) -> Self:
        if self.master_device_id == self.slave_device_id:
            raise ValueError("master and slave devices must differ")
        if self.sentinel_only and not self.sentinel_qualification:
            raise ValueError("sentinel-only qualification requires the sentinel transaction")
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
        boundary_hour, boundary_minute = (
            int(part) for part in self.boundary_time.split(":", maxsplit=1)
        )
        boundary_seconds = boundary_hour * 60 * 60 + boundary_minute * 60
        if boundary_seconds + required_after >= _FIELD_SCHEDULE_END_SECONDS:
            raise ValueError(
                "stable schedule-flow evidence must complete before the 23:59 field end"
            )
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


class ScheduleFlowStage(StrEnum):
    """Totally ordered, identity-free milestones for one composed field run."""

    # Keep the original persisted values readable across an in-place upgrade.  The first v3
    # diagnostic build called this interval a bootstrap even though it was already the outer
    # control transaction.  The implementation now performs a prequalified pause, but changing
    # these wire values would make an unfinished v3 intent impossible to load for recovery.
    OUTER_PAUSE_STARTED = "outer_bootstrap_started"
    OUTER_PAUSE_COMPLETED = "outer_bootstrap_completed"
    SENTINEL_SNAPSHOT_STARTED = "sentinel_snapshot_started"
    SENTINEL_SNAPSHOT_COMPLETED = "sentinel_snapshot_completed"
    SENTINEL_WRITE_STARTED = "sentinel_write_started"
    SENTINEL_VERIFIED = "sentinel_verified"
    SENTINEL_RESTORE_STARTED = "sentinel_restore_started"
    SENTINEL_RESTORED = "sentinel_restored"
    FIELD_SNAPSHOT_STARTED = "field_snapshot_started"
    FIELD_SNAPSHOT_COMPLETED = "field_snapshot_completed"
    FIELD_WRITE_STARTED = "field_write_started"
    FIELD_VERIFIED = "field_verified"
    TIMER_ON_ARM_STARTED = "timer_on_arm_started"
    TIMER_ON_ARMED = "timer_on_armed"
    ROLE_PREFLIGHT_STARTED = "role_preflight_started"
    ROLE_PREFLIGHT_COMPLETED = "role_preflight_completed"
    ROLE_OBSERVATION_STARTED = "role_observation_started"
    ROLE_OBSERVATION_COMPLETED = "role_observation_completed"
    ROLE_DISARM_STARTED = "role_disarm_started"
    ROLE_DISARMED = "role_disarmed"
    FIELD_RESTORE_STARTED = "field_restore_started"
    FIELD_RESTORED = "field_restored"
    OUTER_RESTORE_STARTED = "outer_restore_started"
    OUTER_RESTORED = "outer_restored"


_SCHEDULE_FLOW_STAGE_ORDER = {stage: index for index, stage in enumerate(ScheduleFlowStage)}


def schedule_flow_stage_rank(stage: ScheduleFlowStage) -> int:
    """Return the stable monotonic rank used by durable intent validation."""

    return _SCHEDULE_FLOW_STAGE_ORDER[stage]


class ScheduleFlowFailureCategory(StrEnum):
    """Allow-listed non-schedule failure intervals safe for durable operator output."""

    # Legacy v3 wire value retained for crash-recovery compatibility; see ScheduleFlowStage.
    OUTER_PAUSE = "outer_bootstrap"
    TIMER_ON_ARM = "timer_on_arm"
    ROLE_PREFLIGHT = "role_preflight"
    ROLE_OBSERVATION = "role_observation"
    ROLE_DISARM = "role_disarm"
    OUTER_RESTORE = "outer_restore"
    CANCELLED = "cancelled"
    UNEXPECTED = "unexpected"


class _StagedAutoAwaitingConvergence(RuntimeError):
    """A well-formed Auto tuple has not yet adopted the owned current Constant entry."""


class ScheduleFlowStageEvent(BaseModel):
    """One durable stage event with no raw transport or physical identity fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: ScheduleFlowStage
    occurred_at: datetime
    completed_participants: int | None = Field(default=None, ge=0, le=2)
    temporary_error_code: TemporaryScheduleErrorCode | None = None
    failure_category: ScheduleFlowFailureCategory | None = None
    role_progress: ScheduleLinkageRunProgressEvent | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("schedule-flow stage timestamps must be timezone-aware")
        if self.temporary_error_code is not None and self.failure_category is not None:
            raise ValueError("a schedule-flow stage may contain only one failure classification")
        if self.role_progress is not None:
            if self.stage is not ScheduleFlowStage.ROLE_OBSERVATION_STARTED:
                raise ValueError("role progress is restricted to the role observation stage")
            if self.role_progress.occurred_at != self.occurred_at:
                raise ValueError("role progress and outer stage timestamps must match")
        return self


_TEMPORARY_PROGRESS_STAGE = {
    (
        TemporaryScheduleKind.SENTINEL_QUALIFICATION,
        TemporaryScheduleProgressKind.SNAPSHOT_STARTED,
    ): ScheduleFlowStage.SENTINEL_SNAPSHOT_STARTED,
    (
        TemporaryScheduleKind.SENTINEL_QUALIFICATION,
        TemporaryScheduleProgressKind.SNAPSHOT_COMPLETED,
    ): ScheduleFlowStage.SENTINEL_SNAPSHOT_COMPLETED,
    (
        TemporaryScheduleKind.SENTINEL_QUALIFICATION,
        TemporaryScheduleProgressKind.STAGE_WRITE_STARTED,
    ): ScheduleFlowStage.SENTINEL_WRITE_STARTED,
    (
        TemporaryScheduleKind.SENTINEL_QUALIFICATION,
        TemporaryScheduleProgressKind.STAGE_VERIFIED,
    ): ScheduleFlowStage.SENTINEL_VERIFIED,
    (
        TemporaryScheduleKind.SENTINEL_QUALIFICATION,
        TemporaryScheduleProgressKind.RESTORE_STARTED,
    ): ScheduleFlowStage.SENTINEL_RESTORE_STARTED,
    (
        TemporaryScheduleKind.SENTINEL_QUALIFICATION,
        TemporaryScheduleProgressKind.RESTORE_COMPLETED,
    ): ScheduleFlowStage.SENTINEL_RESTORED,
    (
        TemporaryScheduleKind.FIELD_OBSERVATION,
        TemporaryScheduleProgressKind.SNAPSHOT_STARTED,
    ): ScheduleFlowStage.FIELD_SNAPSHOT_STARTED,
    (
        TemporaryScheduleKind.FIELD_OBSERVATION,
        TemporaryScheduleProgressKind.SNAPSHOT_COMPLETED,
    ): ScheduleFlowStage.FIELD_SNAPSHOT_COMPLETED,
    (
        TemporaryScheduleKind.FIELD_OBSERVATION,
        TemporaryScheduleProgressKind.STAGE_WRITE_STARTED,
    ): ScheduleFlowStage.FIELD_WRITE_STARTED,
    (
        TemporaryScheduleKind.FIELD_OBSERVATION,
        TemporaryScheduleProgressKind.STAGE_VERIFIED,
    ): ScheduleFlowStage.FIELD_VERIFIED,
    (
        TemporaryScheduleKind.FIELD_OBSERVATION,
        TemporaryScheduleProgressKind.RESTORE_STARTED,
    ): ScheduleFlowStage.FIELD_RESTORE_STARTED,
    (
        TemporaryScheduleKind.FIELD_OBSERVATION,
        TemporaryScheduleProgressKind.RESTORE_COMPLETED,
    ): ScheduleFlowStage.FIELD_RESTORED,
}


class ScheduleFlowExperimentResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    sentinel_qualified: bool
    outcome: ScheduleFlowOutcome | Literal["wire_qualified"]
    last_after_sample: ScheduleLinkageSample | None
    schedule_transition_verified: bool
    stable_slave_tuple_observed: bool = True
    stable_observation_seconds: float = Field(ge=0, le=300)
    temporary_schedule_restored: Literal[True] = True
    original_controls_restored: Literal[True] = True
    completed_at: datetime

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        if self.outcome == "wire_qualified":
            if (
                not self.sentinel_qualified
                or self.last_after_sample is not None
                or self.schedule_transition_verified
                or self.stable_slave_tuple_observed
                or self.stable_observation_seconds != 0
            ):
                raise ValueError("wire qualification cannot contain field observation evidence")
        elif self.last_after_sample is None or not self.stable_slave_tuple_observed:
            raise ValueError("schedule-flow outcomes require stable field observation evidence")
        return self


PauseAuthorizer = Callable[
    [ScheduleFlowExperimentSpec, tuple[DeviceControlSnapshot, ...]],
    None,
]


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
        pause_authorizer: PauseAuthorizer,
        prerequisite_authorizer: PrerequisiteAuthorizer,
        role_sample_observer: Callable[[ScheduleLinkageSample], None] | None = None,
        diagnostic_event_observer: Callable[[LinkageDiagnosticEvent], None] | None = None,
        stage_event_observer: Callable[[ScheduleFlowStageEvent], None] | None = None,
        schedule_snapshot_authorizer: SnapshotAuthorizer | None = None,
        role_preflight_settle_seconds: float = _ROLE_PREFLIGHT_SETTLE_SECONDS,
    ) -> None:
        if role_preflight_settle_seconds < 0:
            raise ValueError("role preflight settle interval cannot be negative")
        super().__init__(devices, outer_store, safety_interlock=safety_interlock)
        self._experiment_devices = dict(devices)
        self._schedule_store = schedule_store
        self._role_store = role_store
        self._schedule_controller = TemporaryScheduleController(
            devices,
            schedule_store,
            safety_interlock=safety_interlock,
            snapshot_authorizer=schedule_snapshot_authorizer,
            progress_observer=self._observe_temporary_schedule_progress,
        )
        self._role_controller = ScheduleActiveLinkageController(
            devices,
            role_store,
            prerequisite_authorizer=prerequisite_authorizer,
            safety_interlock=safety_interlock,
            sample_observer=self._observe_role_sample,
            progress_observer=self._observe_role_progress,
            refresh_sessions_before_critical_reads=True,
            owned_staged_auto_transition_observation=True,
        )
        self._external_role_sample_observer = role_sample_observer
        self._external_diagnostic_event_observer = diagnostic_event_observer
        self._external_stage_event_observer = stage_event_observer
        self._authorize_pause = pause_authorizer
        self._role_preflight_settle_seconds = role_preflight_settle_seconds
        self._experiment_spec: ScheduleFlowExperimentSpec | None = None
        self._sentinel_result: TemporaryScheduleResult | None = None
        self._temporary_result: TemporaryScheduleResult | None = None
        self._role_result: ScheduleLinkageResult | None = None
        self._role_error: BaseException | None = None
        self._last_role_sample: ScheduleLinkageSample | None = None
        self._last_role_failure: ScheduleLinkageRunProgressEvent | None = None
        self._experiment_entry_lock = asyncio.Lock()
        self._schedule_restore_blocked = False
        self._last_schedule_stage: ScheduleFlowStage | None = None
        self._schedule_failure_recorded = False
        self._outer_pause_completed = 0
        self._wire_qualification_verified = False
        self._defer_external_evidence_delivery = False
        self._deferred_stage_events: list[ScheduleFlowStageEvent] = []
        self._deferred_diagnostic_events: list[LinkageDiagnosticEvent] = []
        self._deferred_role_samples: dict[str, ScheduleLinkageSample] = {}

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
            self._last_role_failure = None
            self._schedule_restore_blocked = False
            self._last_schedule_stage = None
            self._schedule_failure_recorded = False
            self._outer_pause_completed = 0
            self._wire_qualification_verified = False
            self._defer_external_evidence_delivery = False
            self._deferred_stage_events.clear()
            self._deferred_diagnostic_events.clear()
            self._deferred_role_samples.clear()
            try:
                outer_result = await super().run(spec.outer_linkage_spec())
                if spec.sentinel_only:
                    if not self._wire_qualification_verified:
                        raise LinkageTransactionError(
                            "schedule wire qualification was not fully verified"
                        )
                    return ScheduleFlowExperimentResult(
                        operation_id=spec.operation_id,
                        sentinel_qualified=True,
                        outcome="wire_qualified",
                        last_after_sample=None,
                        schedule_transition_verified=False,
                        stable_slave_tuple_observed=False,
                        stable_observation_seconds=0,
                        completed_at=outer_result.completed_at,
                    )
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

    async def _prepare(
        self,
        spec: LinkageTestSpec,
        *,
        created_at: datetime,
        expires_at: datetime,
    ) -> LinkageTransactionRecord:
        """Authorize the prequalified pause before creating any recovery journal."""

        record = await super()._prepare(
            spec,
            created_at=created_at,
            expires_at=expires_at,
        )
        experiment = self._require_experiment(record)
        # This runs after a read-only outer capture but before journal creation or the first
        # TimerOFF pause write. Keep the private Python API inside the same attended envelope as
        # the fixed CLI, while retaining broad persisted schema compatibility for recovery.
        self._assert_experiment_power_guard(experiment)
        self._authorize_pause(experiment, record.snapshots)
        return record

    @property
    def last_role_sample(self) -> ScheduleLinkageSample | None:
        return self._last_role_sample

    @property
    def last_role_result(self) -> ScheduleLinkageResult | None:
        """Return completed stable-boundary evidence for durable attended CLI handoff."""

        return self._role_result

    @property
    def last_role_failure(self) -> ScheduleLinkageRunProgressEvent | None:
        """Return the redacted inner failure retained across composed rollback."""

        return self._last_role_failure

    @property
    def wire_qualification_verified(self) -> bool:
        """Whether sentinel proof and the safe outer baseline both verified this run."""

        return self._wire_qualification_verified

    def _observe_role_sample(self, sample: ScheduleLinkageSample) -> None:
        self._last_role_sample = sample
        observer = self._external_role_sample_observer
        if observer is None:
            return
        if self._defer_external_evidence_delivery:
            # The monitor may sample many times.  Only the latest immutable evidence for each
            # phase is needed by the durable CLI intent, keeping this safety queue bounded by two.
            self._deferred_role_samples[sample.phase] = sample
            return
        observer(sample)

    def _observe_role_progress(self, event: ScheduleLinkageRunProgressEvent) -> None:
        """Embed the inner controller's already-redacted milestone in outer evidence."""

        if (
            event.kind is ScheduleLinkageRunProgressKind.FAILED
            and self._last_role_failure is None
        ):
            # This assignment is deliberately the only special handling inside the armed
            # window. Durable observers remain gated until exact disarm; the existing terminal
            # intent save checkpoints this already-redacted event after compensation.
            self._last_role_failure = event
        self._emit_stage(
            ScheduleFlowStage.ROLE_OBSERVATION_STARTED,
            role_progress=event,
        )

    def _remember_role_preflight_failure(
        self,
        error: BaseException,
        *,
        settled: bool,
    ) -> None:
        """Retain one allow-listed reason without persisting while TimerON may be active."""

        if self._last_role_failure is not None:
            return
        if settled:
            failure = ScheduleLinkageRunFailure.PREFLIGHT_SETTLE
        elif isinstance(error, ScheduleLinkagePreflightError):
            failure = error.failure
        elif isinstance(error, ScheduleLinkageBusyError):
            failure = ScheduleLinkageRunFailure.PREFLIGHT_BUSY
        else:
            failure = ScheduleLinkageRunFailure.PREFLIGHT_UNEXPECTED
        self._last_role_failure = ScheduleLinkageRunProgressEvent(
            kind=ScheduleLinkageRunProgressKind.FAILED,
            occurred_at=datetime.now(UTC),
            failure=failure,
        )

    def _on_diagnostic_event(self, event: LinkageDiagnosticEvent) -> None:
        """Forward only the parent's already-redacted diagnostic event to the harness."""

        observer = self._external_diagnostic_event_observer
        if observer is None:
            return
        if self._defer_external_evidence_delivery:
            self._deferred_diagnostic_events.append(event)
            return
        observer(event)

    def _emit_stage(
        self,
        stage: ScheduleFlowStage,
        *,
        completed_participants: int | None = None,
        temporary_error_code: TemporaryScheduleErrorCode | None = None,
        failure_category: ScheduleFlowFailureCategory | None = None,
        role_progress: ScheduleLinkageRunProgressEvent | None = None,
        best_effort: bool = False,
    ) -> None:
        """Emit one typed milestone; compensating paths never depend on its observer."""

        previous = self._last_schedule_stage
        if previous is not None and schedule_flow_stage_rank(stage) < schedule_flow_stage_rank(
            previous
        ):
            # A recovery can skip forward milestones, but it must never report backwards.
            return
        self._last_schedule_stage = stage
        if temporary_error_code is not None or failure_category is not None:
            self._schedule_failure_recorded = True
        observer = self._external_stage_event_observer
        if observer is None:
            return
        event = ScheduleFlowStageEvent(
            stage=stage,
            occurred_at=(
                role_progress.occurred_at
                if role_progress is not None
                else datetime.now(UTC)
            ),
            completed_participants=completed_participants,
            temporary_error_code=temporary_error_code,
            failure_category=failure_category,
            role_progress=role_progress,
        )
        if self._defer_external_evidence_delivery:
            # Persisted CLI observers fsync the outer intent. Never run one after TimerON can be
            # armed and before the composed controller proves TimerOFF+independent and closes
            # the role journal. The typed state machines bound this in-memory event sequence.
            self._deferred_stage_events.append(event)
            return
        if not best_effort:
            observer(event)
            return
        try:
            observer(event)
        except Exception:
            # TimerOFF, exact schedule restoration and outer control restoration must continue.
            pass

    def _flush_deferred_external_evidence(self) -> None:
        """Deliver evidence only after exact composed disarm and role closure are proven."""

        stage_events = tuple(self._deferred_stage_events)
        diagnostic_events = tuple(self._deferred_diagnostic_events)
        role_samples = tuple(self._deferred_role_samples.values())
        self._deferred_stage_events.clear()
        self._deferred_diagnostic_events.clear()
        self._deferred_role_samples.clear()
        self._defer_external_evidence_delivery = False

        stage_observer = self._external_stage_event_observer
        if stage_observer is not None:
            for event in stage_events:
                try:
                    stage_observer(event)
                except BaseException:
                    # Evidence durability cannot hold up exact schedule restoration.
                    pass
        diagnostic_observer = self._external_diagnostic_event_observer
        if diagnostic_observer is not None:
            for event in diagnostic_events:
                try:
                    diagnostic_observer(event)
                except BaseException:
                    pass
        sample_observer = self._external_role_sample_observer
        if sample_observer is not None:
            for sample in role_samples:
                try:
                    sample_observer(sample)
                except BaseException:
                    pass

    def _observe_temporary_schedule_progress(
        self,
        event: TemporaryScheduleProgressEvent,
    ) -> None:
        if event.kind is TemporaryScheduleProgressKind.FAILED:
            stage = self._last_schedule_stage
            if stage is None:
                stage = (
                    ScheduleFlowStage.SENTINEL_SNAPSHOT_STARTED
                    if event.schedule_kind is TemporaryScheduleKind.SENTINEL_QUALIFICATION
                    else ScheduleFlowStage.FIELD_SNAPSHOT_STARTED
                )
            self._emit_stage(
                stage,
                completed_participants=event.completed_participants,
                temporary_error_code=event.error_code,
                best_effort=True,
            )
            return
        stage = _TEMPORARY_PROGRESS_STAGE[(event.schedule_kind, event.kind)]
        if (
            event.kind is TemporaryScheduleProgressKind.STAGE_VERIFIED
            and event.completed_participants < 2
        ):
            stage = (
                ScheduleFlowStage.SENTINEL_WRITE_STARTED
                if event.schedule_kind is TemporaryScheduleKind.SENTINEL_QUALIFICATION
                else ScheduleFlowStage.FIELD_WRITE_STARTED
            )
        elif (
            event.kind is TemporaryScheduleProgressKind.RESTORE_COMPLETED
            and 0 < event.completed_participants < 2
        ):
            stage = (
                ScheduleFlowStage.SENTINEL_RESTORE_STARTED
                if event.schedule_kind is TemporaryScheduleKind.SENTINEL_QUALIFICATION
                else ScheduleFlowStage.FIELD_RESTORE_STARTED
            )
        self._emit_stage(
            stage,
            completed_participants=event.completed_participants,
            best_effort=event.kind
            in {
                TemporaryScheduleProgressKind.RESTORE_STARTED,
                TemporaryScheduleProgressKind.RESTORE_COMPLETED,
            },
        )

    async def _stage_devices(
        self,
        record: LinkageTransactionRecord,
    ) -> LinkageTransactionRecord:
        self._emit_stage(
            ScheduleFlowStage.OUTER_PAUSE_STARTED,
            completed_participants=0,
        )
        try:
            experiment = self._require_experiment(record)
            for snapshot in record.snapshots:
                state = await self._run_forward_operation(
                    record,
                    self._get_device(snapshot.device_id).get_state(),
                )
                self._assert_snapshot_control(
                    snapshot,
                    state,
                    expected_timer=True,
                )
                self._assert_schedule_unchanged(snapshot, state)

            # Recheck receipt validity against the same fresh snapshots immediately before the
            # first control write. There is deliberately no fallback bootstrap/requalification.
            self._authorize_pause(experiment, record.snapshots)
            target_powers = {
                record.spec.master_device_id: experiment.master_before_flow,
                record.spec.slave_device_id: experiment.slave_before_flow,
            }
            for snapshot in record.snapshots:
                device = self._get_device(snapshot.device_id)
                self._authorize_pause(experiment, record.snapshots)
                target = DeviceTarget(
                    enabled=True,
                    power=target_powers[snapshot.device_id],
                    mode="constant",
                    frequency=experiment.safe_frequency,
                    linkage=LinkageRole.INDEPENDENT,
                    timer_enabled=False,
                )
                self._require_forward_write(record)
                await self._run_forward_operation(
                    record,
                    device.write_target(
                        target,
                        # The LAN implementation can wait for its device lock and command-rate
                        # interval after entering write_target().  Revalidate the receipt in the
                        # transport's last-moment guard as well as immediately before the call,
                        # so an expired qualification cannot cross that queue into a frame send.
                        guard=lambda current_record=record, current_experiment=experiment: (
                            self._pause_write_allowed(current_experiment, current_record)
                        ),
                    ),
                )
                # A successful write can leave an older 0x03/0x04 frame queued on the write
                # stream. Prove the complete TimerOFF control and unchanged schedule only after
                # a new authenticated session.
                await self._run_forward_operation(record, device.disconnect())
                await self._run_forward_operation(record, device.connect())
                paused = await self._run_forward_operation(record, device.get_state())
                self._assert_target(device.device_id, paused, target)
                self._assert_schedule_unchanged(snapshot, paused)
                self._outer_pause_completed += 1
                if self._outer_pause_completed < len(record.snapshots):
                    self._emit_stage(
                        ScheduleFlowStage.OUTER_PAUSE_STARTED,
                        completed_participants=self._outer_pause_completed,
                    )
        except asyncio.CancelledError:
            self._emit_stage(
                self._last_schedule_stage or ScheduleFlowStage.OUTER_PAUSE_STARTED,
                completed_participants=self._outer_pause_completed,
                failure_category=ScheduleFlowFailureCategory.CANCELLED,
                best_effort=True,
            )
            raise
        except BaseException:
            self._emit_stage(
                self._last_schedule_stage or ScheduleFlowStage.OUTER_PAUSE_STARTED,
                completed_participants=self._outer_pause_completed,
                failure_category=ScheduleFlowFailureCategory.OUTER_PAUSE,
                best_effort=True,
            )
            raise
        self._emit_stage(
            ScheduleFlowStage.OUTER_PAUSE_COMPLETED,
            completed_participants=self._outer_pause_completed,
        )
        return record

    def _pause_write_allowed(
        self,
        experiment: ScheduleFlowExperimentSpec,
        record: LinkageTransactionRecord,
    ) -> bool:
        self._authorize_pause(experiment, record.snapshots)
        return self._forward_write_allowed(record)

    async def _activate_relationship(self, record: LinkageTransactionRecord) -> None:
        """Run the nested experiment while the outer transaction remains safely TimerOFF."""

        spec = self._require_experiment(record)
        if spec.sentinel_qualification:
            self._emit_stage(ScheduleFlowStage.SENTINEL_SNAPSHOT_STARTED)
            try:
                self._sentinel_result = await self._schedule_controller.run(
                    await self._sentinel_spec(spec)
                )
            except asyncio.CancelledError:
                if not self._schedule_failure_recorded:
                    self._emit_stage(
                        self._last_schedule_stage
                        or ScheduleFlowStage.SENTINEL_SNAPSHOT_STARTED,
                        failure_category=ScheduleFlowFailureCategory.CANCELLED,
                        best_effort=True,
                    )
                raise
            except BaseException:
                if not self._schedule_failure_recorded:
                    self._emit_stage(
                        self._last_schedule_stage
                        or ScheduleFlowStage.SENTINEL_SNAPSHOT_STARTED,
                        failure_category=ScheduleFlowFailureCategory.UNEXPECTED,
                        best_effort=True,
                    )
                raise

        if spec.sentinel_only:
            return

        async def observe(_record: TemporaryScheduleRecord) -> ObservationCompletion:
            role_error: BaseException | None = None
            timer_on_may_be_armed = False
            try:
                self._emit_stage(ScheduleFlowStage.TIMER_ON_ARM_STARTED)
                # This assignment has no suspension point after the externally persisted start
                # event.  From the first possible TimerON write onward, no filesystem-backed
                # observer may delay cancellation, disarm, or rollback.
                self._defer_external_evidence_delivery = True
                # Do not arm TimerON unless a fresh reply-only clock leaves the complete setup,
                # convergence, and guarded role-write reserve.  A refusal here has sent no
                # TimerON or role frame; the surrounding schedule transaction still restores its
                # exact staged image in the ordinary inverse order.
                await self._assert_timer_arm_budget(record)
                # This assignment has no suspension point between the successful gate and the
                # first operation that may send TimerON. From here onward inverse TimerOFF is
                # mandatory even if the first arm attempt has an uncertain outcome.
                timer_on_deadline = (
                    asyncio.get_running_loop().time()
                    + _STAGED_A_CONVERGENCE_TIMEOUT_SECONDS
                )
                timer_on_may_be_armed = True
                await self._run_before_staged_timer_deadline(
                    record,
                    self._arm_temporary_schedule(
                        record,
                        monotonic_deadline=timer_on_deadline,
                    ),
                    monotonic_deadline=timer_on_deadline,
                )
                self._emit_stage(ScheduleFlowStage.TIMER_ON_ARMED)
                self._emit_stage(ScheduleFlowStage.ROLE_PREFLIGHT_STARTED)
                await self._await_staged_current_a(
                    record,
                    monotonic_deadline=timer_on_deadline,
                )
                preflight = await self._run_forward_operation(
                    record,
                    self._role_controller.preflight(spec.role_observation_spec()),
                )
                self._emit_stage(ScheduleFlowStage.ROLE_PREFLIGHT_COMPLETED)
                if self._role_preflight_settle_seconds:
                    # Keep one quiet interval between two independently refreshed snapshots.
                    # The nested run remains fail-closed if any TimerON or schedule evidence
                    # changes during this read-only settling period.
                    await self._run_forward_operation(
                        record,
                        asyncio.sleep(self._role_preflight_settle_seconds),
                    )
                self._emit_stage(ScheduleFlowStage.ROLE_OBSERVATION_STARTED)
                self._role_result = await self._role_controller.run(preflight)
                self._emit_stage(ScheduleFlowStage.ROLE_OBSERVATION_COMPLETED)
            except asyncio.CancelledError as error:
                role_error = error
                self._emit_stage(
                    self._last_schedule_stage or ScheduleFlowStage.TIMER_ON_ARM_STARTED,
                    failure_category=ScheduleFlowFailureCategory.CANCELLED,
                    best_effort=True,
                )
            except BaseException as error:
                role_error = error
                current = self._last_schedule_stage
                if current in {
                    ScheduleFlowStage.ROLE_PREFLIGHT_STARTED,
                    ScheduleFlowStage.ROLE_PREFLIGHT_COMPLETED,
                }:
                    self._remember_role_preflight_failure(
                        error,
                        settled=current is ScheduleFlowStage.ROLE_PREFLIGHT_COMPLETED,
                    )
                category = (
                    ScheduleFlowFailureCategory.TIMER_ON_ARM
                    if current
                    in {
                        ScheduleFlowStage.TIMER_ON_ARM_STARTED,
                        ScheduleFlowStage.TIMER_ON_ARMED,
                    }
                    else ScheduleFlowFailureCategory.ROLE_PREFLIGHT
                    if current
                    in {
                        ScheduleFlowStage.ROLE_PREFLIGHT_STARTED,
                        ScheduleFlowStage.ROLE_PREFLIGHT_COMPLETED,
                    }
                    else ScheduleFlowFailureCategory.ROLE_OBSERVATION
                )
                self._emit_stage(
                    current or ScheduleFlowStage.ROLE_OBSERVATION_STARTED,
                    failure_category=category,
                    best_effort=True,
                )
            finally:
                self._emit_stage(
                    ScheduleFlowStage.ROLE_DISARM_STARTED,
                    best_effort=True,
                )
                try:
                    if timer_on_may_be_armed:
                        await self._disarm_temporary_schedule_uninterruptibly(record)
                        await self._clear_role_journal_before_schedule_restore(record)
                except BaseException:
                    self._emit_stage(
                        ScheduleFlowStage.ROLE_DISARM_STARTED,
                        failure_category=ScheduleFlowFailureCategory.ROLE_DISARM,
                        best_effort=True,
                    )
                    raise
                self._emit_stage(
                    ScheduleFlowStage.ROLE_DISARMED,
                    best_effort=True,
                )
                self._flush_deferred_external_evidence()
            self._role_error = role_error
            return ObservationCompletion.DISARM_VERIFIED

        try:
            self._temporary_result = await self._schedule_controller.run(
                spec.temporary_schedule_spec(),
                observe=observe,
            )
        except asyncio.CancelledError:
            try:
                self._schedule_restore_blocked = self._schedule_store.load() is not None
            except BaseException:
                self._schedule_restore_blocked = True
            if not self._schedule_failure_recorded:
                self._emit_stage(
                    self._last_schedule_stage or ScheduleFlowStage.FIELD_SNAPSHOT_STARTED,
                    failure_category=ScheduleFlowFailureCategory.CANCELLED,
                    best_effort=True,
                )
            raise
        except BaseException:
            # If the exact schedule journal survives, the controller could not prove that the
            # original 48 slots are back. Refuse the parent's TimerON control restore until an
            # attended recovery first disarms both devices and restores that journal.
            try:
                self._schedule_restore_blocked = self._schedule_store.load() is not None
            except BaseException:
                self._schedule_restore_blocked = True
            if not self._schedule_failure_recorded:
                self._emit_stage(
                    self._last_schedule_stage or ScheduleFlowStage.FIELD_SNAPSHOT_STARTED,
                    failure_category=ScheduleFlowFailureCategory.UNEXPECTED,
                    best_effort=True,
                )
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
        self._emit_stage(
            ScheduleFlowStage.OUTER_RESTORE_STARTED,
            best_effort=True,
        )
        try:
            await super()._rollback_uninterruptibly(
                record,
                schedule_change_ids=schedule_change_ids,
                read_failure_ids=read_failure_ids,
            )
        except BaseException:
            self._emit_stage(
                ScheduleFlowStage.OUTER_RESTORE_STARTED,
                failure_category=ScheduleFlowFailureCategory.OUTER_RESTORE,
                best_effort=True,
            )
            raise

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
            await self._disarm_nested_recovery_controls(outer_record)
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
                await self._disarm_nested_recovery_controls(outer_record)
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
        if self._defer_external_evidence_delivery:
            self._flush_deferred_external_evidence()
        return recovered

    async def _disarm_nested_recovery_controls(
        self,
        outer_record: LinkageTransactionRecord,
    ) -> None:
        """Report the real TimerOFF proof used by role and schedule-only recovery."""

        # A fresh process may enter recovery while TimerON/native roles are still active.  Gate
        # its first reported milestone before any external fsync callback can delay safe disarm.
        self._defer_external_evidence_delivery = True
        self._emit_stage(
            ScheduleFlowStage.ROLE_DISARM_STARTED,
            best_effort=True,
        )
        try:
            await self._disarm_temporary_schedule_uninterruptibly(outer_record)
        except BaseException:
            self._emit_stage(
                ScheduleFlowStage.ROLE_DISARM_STARTED,
                failure_category=ScheduleFlowFailureCategory.ROLE_DISARM,
                best_effort=True,
            )
            raise
        self._emit_stage(
            ScheduleFlowStage.ROLE_DISARMED,
            best_effort=True,
        )

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
        experiment = self._require_experiment(record)
        if experiment.sentinel_only:
            # Reaching the monitor proves sparse write/read/restore, the safe baseline readback,
            # and the durable ACTIVE transition all completed before outer exact rollback starts.
            self._wire_qualification_verified = True
        return LinkageStopReason.TIMEOUT, False

    async def _sentinel_spec(
        self,
        spec: ScheduleFlowExperimentSpec,
    ) -> TemporaryScheduleSpec:
        patches: list[DeviceSchedulePatch] = []
        for device_id in (spec.master_device_id, spec.slave_device_id):
            image = await self._get_device(device_id).read_schedule_image_explicit()
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

    async def _assert_timer_arm_budget(self, record: LinkageTransactionRecord) -> None:
        """Prove the staged TimerOFF pair still has the fixed pre-arm time reserve."""

        spec = self._require_experiment(record)
        states = await self._read_staged_pair_explicit(record)
        self._assert_staged_pair_state(
            record,
            spec,
            states,
            expected_timer=False,
            minimum_lead_seconds=_TIMER_ARM_MINIMUM_LEAD_SECONDS,
            require_current_a=False,
        )

    async def _await_staged_current_a(
        self,
        record: LinkageTransactionRecord,
        *,
        monotonic_deadline: float,
    ) -> None:
        """Wait read-only for the owned TimerON schedule to expose exact current-A evidence."""

        spec = self._require_experiment(record)
        loop = asyncio.get_running_loop()
        last_mismatch: _StagedAutoAwaitingConvergence | None = None
        while True:
            states = await self._read_staged_pair_explicit(
                record,
                monotonic_deadline=monotonic_deadline,
            )
            try:
                self._assert_staged_pair_state(
                    record,
                    spec,
                    states,
                    expected_timer=True,
                    minimum_lead_seconds=spec.minimum_lead_seconds,
                    require_current_a=True,
                )
            except _StagedAutoAwaitingConvergence as error:
                last_mismatch = error
            else:
                if loop.time() >= monotonic_deadline:
                    self._require_forward_write(record)
                    raise ScheduleLinkagePreflightError(
                        "staged current-A convergence exceeded its absolute deadline",
                        failure=ScheduleLinkageRunFailure.PREFLIGHT_SETTLE,
                    )
                return

            remaining = monotonic_deadline - loop.time()
            if remaining <= 0:
                self._require_forward_write(record)
                raise ScheduleLinkagePreflightError(
                    "staged current-A evidence did not converge before its deadline",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_SETTLE,
                ) from last_mismatch
            timeout = asyncio.timeout(remaining)
            try:
                async with timeout:
                    await self._run_forward_operation(
                        record,
                        asyncio.sleep(
                            min(_STAGED_A_CONVERGENCE_RETRY_SECONDS, remaining)
                        ),
                    )
            except TimeoutError:
                if not timeout.expired():
                    raise
                self._require_forward_write(record)
                raise ScheduleLinkagePreflightError(
                    "staged current-A evidence did not converge before its deadline",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_SETTLE,
                ) from last_mismatch

    async def _run_before_staged_timer_deadline(
        self,
        record: LinkageTransactionRecord,
        operation: Awaitable[None],
        *,
        monotonic_deadline: float,
    ) -> None:
        """Bound TimerON setup and convergence to one absolute monotonic window."""

        remaining = monotonic_deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            orphan = asyncio.ensure_future(operation)
            orphan.cancel()
            await asyncio.gather(orphan, return_exceptions=True)
            self._require_forward_write(record)
            raise ScheduleLinkagePreflightError(
                "staged TimerON setup exceeded its absolute deadline",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SETTLE,
            )
        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                return await operation
        except TimeoutError:
            if not timeout.expired():
                raise
            self._require_forward_write(record)
            raise ScheduleLinkagePreflightError(
                "staged TimerON setup exceeded its absolute deadline",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SETTLE,
            ) from None

    async def _read_staged_pair_explicit(
        self,
        record: LinkageTransactionRecord,
        *,
        monotonic_deadline: float | None = None,
    ) -> dict[str, DeviceState]:
        """Read both complete states from correlated replies on newly authenticated streams."""

        device_ids = (
            record.spec.master_device_id,
            record.spec.slave_device_id,
        )

        async def capture() -> dict[str, DeviceState]:
            await self._replace_device_sessions(record, device_ids)

            async def read_pair() -> tuple[DeviceState, DeviceState]:
                tasks = tuple(
                    asyncio.create_task(
                        self._get_device(device_id).get_explicit_state()
                    )
                    for device_id in device_ids
                )
                try:
                    values = await asyncio.gather(*tasks)
                finally:
                    for task in tasks:
                        if not task.done():
                            task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                return values[0], values[1]

            try:
                values = await self._run_forward_operation(record, read_pair())
            except BaseException:
                # A failed or cancelled explicit read retires the LAN session. Complete one
                # paired fresh transport boundary before compensation can send TimerOFF or
                # restore the exact schedule image, then preserve the original failure.
                devices = tuple(self._get_device(device_id) for device_id in device_ids)
                try:
                    await self._refresh_device_sessions_uninterruptibly(devices)
                except asyncio.CancelledError:
                    raise
                except BaseException as cleanup_error:
                    del cleanup_error
                raise
            return dict(zip(device_ids, values, strict=True))

        if monotonic_deadline is None:
            return await capture()
        remaining = monotonic_deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            self._require_forward_write(record)
            raise ScheduleLinkagePreflightError(
                "staged current-A convergence exceeded its absolute deadline",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SETTLE,
            )
        timeout = asyncio.timeout(remaining)
        try:
            async with timeout:
                return await capture()
        except TimeoutError:
            if not timeout.expired():
                raise
            self._require_forward_write(record)
            raise ScheduleLinkagePreflightError(
                "staged current-A convergence exceeded its absolute deadline",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SETTLE,
            ) from None

    def _assert_staged_pair_state(
        self,
        record: LinkageTransactionRecord,
        spec: ScheduleFlowExperimentSpec,
        states: Mapping[str, DeviceState],
        *,
        expected_timer: bool,
        minimum_lead_seconds: float,
        require_current_a: bool,
    ) -> None:
        """Reject every drift except a well-formed, not-yet-current Auto A tuple."""

        expected_entries = self._expected_staged_entries(spec)
        clocks: list[datetime] = []
        leads: list[float] = []
        awaiting_current_a = False
        boundary_hour, boundary_minute = (
            int(value) for value in spec.boundary_time.split(":", maxsplit=1)
        )
        expected_powers = {
            record.spec.master_device_id: record.spec.master_power,
            record.spec.slave_device_id: record.spec.slave_power,
        }
        for device_id in (record.spec.master_device_id, record.spec.slave_device_id):
            state = states[device_id]
            if (
                state.online is not True
                or state.error is not None
                or state.enabled is not True
                or state.power != expected_powers[device_id]
                or state.mode != record.spec.mode
                or state.frequency != record.spec.frequency
                or state.linkage is not LinkageRole.INDEPENDENT
                or state.timer_enabled is not expected_timer
            ):
                raise ScheduleLinkagePreflightError(
                    "staged control baseline changed before role observation",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_CONTROL_BASELINE,
                )
            schedule = state.schedule
            if (
                schedule is None
                or schedule.enabled is not expected_timer
                or schedule.invalid_slots
                or tuple(sorted(schedule.entries, key=lambda entry: entry.slot))
                != expected_entries[device_id]
            ):
                raise ScheduleLinkagePreflightError(
                    "decoded staged schedule no longer matches the owned fixed plan",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_STAGED_PLAN,
                )
            self._assert_guarded_power_value(device_id, state.power)
            for entry in expected_entries[device_id]:
                self._assert_guarded_power_value(
                    device_id,
                    entry.parameters.get("flow"),
                )
            clock = schedule.device_local_time
            if clock is None or clock.tzinfo is not None:
                raise ScheduleLinkagePreflightError(
                    "staged schedule clock is unavailable or invalid",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_CLOCK,
                )
            boundary = clock.replace(
                hour=boundary_hour,
                minute=boundary_minute,
                second=0,
                microsecond=0,
            )
            lead = (boundary - clock).total_seconds()
            if lead <= minimum_lead_seconds:
                raise ScheduleLinkagePreflightError(
                    "staged schedule lacks the required pre-boundary reserve",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW,
                )
            clocks.append(clock)
            leads.append(lead)

            if require_current_a:
                values = state.observed_attributes
                mode = values.get("AutoMode")
                flow = values.get("AutoFlow")
                frequency = values.get("AutoFreq")
                feed_time = values.get("AutoFeedTime")
                if (
                    not isinstance(mode, str)
                    or mode not in _STAGED_CURRENT_AUTO_MODES
                    or isinstance(flow, bool)
                    or not isinstance(flow, int)
                    or not 0 <= flow <= 100
                    or isinstance(frequency, bool)
                    or not isinstance(frequency, int)
                    or not 0 <= frequency <= 100
                ):
                    raise ScheduleLinkagePreflightError(
                        "staged current-A evidence is malformed",
                        failure=ScheduleLinkageRunFailure.PREFLIGHT_AUTO_EVIDENCE,
                    )
                if mode == "feed" and (
                    isinstance(feed_time, bool)
                    or not isinstance(feed_time, int)
                    or not 1 <= feed_time <= 60
                ):
                    raise ScheduleLinkagePreflightError(
                        "staged current-A feed evidence is malformed",
                        failure=ScheduleLinkageRunFailure.PREFLIGHT_AUTO_EVIDENCE,
                    )
                self._assert_guarded_power_value(device_id, flow)
                current = expected_entries[device_id][0]
                if mode != "constant" or flow != current.parameters["flow"]:
                    awaiting_current_a = True

        if max(clocks) - min(clocks) > timedelta(
            seconds=spec.maximum_clock_skew_seconds
        ):
            raise ScheduleLinkagePreflightError(
                "staged pair clocks exceed the fixed-plan skew allowance",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CLOCK,
            )
        if max(leads) - min(leads) > spec.maximum_clock_skew_seconds:
            raise ScheduleLinkagePreflightError(
                "staged pair boundary leads disagree",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CLOCK,
            )
        if awaiting_current_a:
            raise _StagedAutoAwaitingConvergence(
                "well-formed Auto evidence has not adopted the staged current A"
            )

    def _assert_guarded_power_value(
        self,
        device_id: str,
        value: object,
    ) -> None:
        """Reject manual, staged, or transient Flow outside the attended safe envelope."""

        capabilities = self._get_device(device_id).capabilities
        limits = capabilities.power_limits
        guarded_maximum = min(limits.max_power, _SCHEDULE_FLOW_TEST_MAX_POWER)
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not limits.min_power <= value <= guarded_maximum
            or value % capabilities.power_step
        ):
            raise ScheduleLinkagePreflightError(
                "staged Flow is outside the guarded power range",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_POWER_GUARD,
            )

    def _assert_experiment_power_guard(
        self,
        spec: ScheduleFlowExperimentSpec,
    ) -> None:
        """Validate every planned Flow before the outer transaction can write a frame."""

        planned = {
            spec.master_device_id: (
                spec.master_before_flow,
                spec.master_after_flow,
            ),
            spec.slave_device_id: (
                spec.slave_before_flow,
                spec.slave_after_flow,
            ),
        }
        for device_id, values in planned.items():
            for value in values:
                self._assert_guarded_power_value(device_id, value)

    @staticmethod
    def _expected_staged_entries(
        spec: ScheduleFlowExperimentSpec,
    ) -> dict[str, tuple[ScheduleEntry, ...]]:
        expected: dict[str, tuple[ScheduleEntry, ...]] = {}
        for patch in spec.temporary_schedule_spec().device_patches:
            entries = tuple(
                entry
                for slot in patch.slots
                if (
                    entry := decode_local_wavemaker_pro_slot_wire(
                        slot.wire_bytes,
                        slot_index=slot.slot,
                    )
                )
                is not None
            )
            expected[patch.device_id] = entries
        return expected

    async def _arm_temporary_schedule(
        self,
        record: LinkageTransactionRecord,
        *,
        monotonic_deadline: float,
    ) -> None:
        targets: list[tuple[str, DeviceTarget]] = []
        for device_id, power in (
            (record.spec.master_device_id, record.spec.master_power),
            (record.spec.slave_device_id, record.spec.slave_power),
        ):
            target = DeviceTarget(
                enabled=True,
                power=power,
                mode=record.spec.mode,
                frequency=record.spec.frequency,
                linkage=LinkageRole.INDEPENDENT,
                timer_enabled=True,
            )
            targets.append((device_id, target))
            self._require_forward_write(record)
            await self._run_forward_operation(
                record,
                self._get_device(device_id).write_target(
                    target,
                    guard=lambda current=record: self._forward_write_allowed(current),
                ),
            )

        # A successful multi-DP write can leave the paired 0x03 reply behind when its 0x04
        # report satisfied the adapter readback first.  Prove both complete TimerON targets on
        # newly authenticated streams, then replace those streams once more so the role
        # preflight cannot consume either proof read's queued companion frame.
        device_ids = tuple(device_id for device_id, _ in targets)
        states = await self._read_staged_pair_explicit(
            record,
            monotonic_deadline=monotonic_deadline,
        )
        for device_id, target in targets:
            self._assert_target(device_id, states[device_id], target)
        await self._replace_device_sessions(record, device_ids)

    async def _replace_device_sessions(
        self,
        record: LinkageTransactionRecord,
        device_ids: tuple[str, ...],
    ) -> None:
        """Replace paired streams without exposing a half-disconnected rollback state."""

        devices = tuple(self._get_device(device_id) for device_id in device_ids)

        async def replace_pair() -> None:
            try:
                disconnect_results = await asyncio.gather(
                    *(device.disconnect() for device in devices),
                    return_exceptions=True,
                )
                connect_results = await asyncio.gather(
                    *(device.connect() for device in devices),
                    return_exceptions=True,
                )
                failure = next(
                    (
                        result
                        for result in (*disconnect_results, *connect_results)
                        if isinstance(result, BaseException)
                    ),
                    None,
                )
                if failure is not None:
                    raise failure
            except BaseException:
                # Cancellation, deadline, revoked authority, or a failed paired boundary must not
                # strand the peer disconnected when the enclosing transaction begins exact
                # TimerOFF compensation. This is read-only transport cleanup, never a control
                # retry; the original boundary failure remains the forward result.
                try:
                    await self._reconnect_device_sessions_uninterruptibly(devices)
                except asyncio.CancelledError:
                    raise
                except BaseException as cleanup_error:
                    del cleanup_error
                raise

        await self._run_forward_operation(record, replace_pair())

    @staticmethod
    async def _refresh_device_sessions_uninterruptibly(
        devices: tuple[JebaoDevice, ...],
    ) -> None:
        """Complete a paired disconnect/reconnect before propagating cancellation."""

        async def refresh_pair() -> None:
            disconnect_results = await asyncio.gather(
                *(device.disconnect() for device in devices),
                return_exceptions=True,
            )
            connect_results = await asyncio.gather(
                *(device.connect() for device in devices),
                return_exceptions=True,
            )
            failure = next(
                (
                    result
                    for result in (*disconnect_results, *connect_results)
                    if isinstance(result, BaseException)
                ),
                None,
            )
            if failure is not None:
                raise failure

        task = asyncio.create_task(refresh_pair())
        cancellation_received = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancellation_received = True
        task.result()
        if cancellation_received:
            raise asyncio.CancelledError

    @staticmethod
    async def _reconnect_device_sessions_uninterruptibly(
        devices: tuple[JebaoDevice, ...],
    ) -> None:
        """Complete paired reconnect cleanup before propagating cancellation or failure."""

        async def reconnect_pair() -> None:
            results = await asyncio.gather(
                *(device.connect() for device in devices),
                return_exceptions=True,
            )
            failure = next(
                (result for result in results if isinstance(result, BaseException)),
                None,
            )
            if failure is not None:
                raise failure

        task = asyncio.create_task(reconnect_pair())
        cancellation_received = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancellation_received = True
        task.result()
        if cancellation_received:
            raise asyncio.CancelledError

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
        devices = tuple(self._get_device(device_id) for device_id, _power in targets)
        try:
            # Explicit-reply failure retires the underlying LAN stream. Always establish fresh
            # paired sessions before the first compensating TimerOFF frame, even when the read
            # path already attempted cleanup.
            await self._refresh_device_sessions_uninterruptibly(devices)
        except asyncio.CancelledError:
            cancellation_received = True
        except BaseException:
            # Still try both TimerOFF writes. Their fresh-state verification below decides
            # whether exact schedule restoration is authorized or recovery must retain control.
            pass
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
        end=_FIELD_SCHEDULE_END,
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
    "SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT",
    "SCHEDULE_FLOW_STAGE_EVENT_LIMIT",
    "PauseAuthorizer",
    "ScheduleFlowExperimentController",
    "ScheduleFlowExperimentResult",
    "ScheduleFlowExperimentSpec",
    "ScheduleFlowFailureCategory",
    "ScheduleFlowOutcome",
    "ScheduleFlowStage",
    "ScheduleFlowStageEvent",
    "classify_schedule_flow_sample",
    "schedule_flow_stage_rank",
]
