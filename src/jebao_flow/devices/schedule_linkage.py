"""Attended schedule-boundary verification using linkage-only writes.

This transaction is intentionally separate from the TimerOFF native-linkage diagnostic.  It
never writes TimerON, Flow, Mode, Frequency, power, or schedule slots.  Its only mutation is the
native ``Linkage`` datapoint, and every exit path detaches the slave before the master.  Because
a role change can expose the latent manual Flow, preflight caps that fallback and both boundary
AutoFlow values at the same guarded test maximum.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Literal, Protocol, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from jebao_flow.devices.base import (
    DeviceConnectionError,
    HeartbeatFencedStateError,
    HeartbeatFencedStateStage,
    JebaoDevice,
)
from jebao_flow.devices.identity import PhysicalDeviceBinding, physical_identity_key
from jebao_flow.devices.linkage import LinkageSafetyInterlock, schedule_structure_fingerprint
from jebao_flow.protocol.errors import ProtocolError
from jebao_flow.protocol.models import (
    Capability,
    DeviceSchedule,
    DeviceState,
    LinkageRole,
    ScheduleEntry,
)

DeviceIdentifier = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    ),
]
_CURRENT_MODES = frozenset({"constant", "pulse", "sine", "feed"})
_NEXT_MODES = frozenset({"constant", "pulse", "sine"})
_KNOWN_PRO_MODES = frozenset(
    {
        "pulse",
        "sine",
        "constant",
        "random",
        "tidal",
        "nutrient_transport",
        "circulation",
        "feed",
        "custom",
    }
)
_DAY_SECONDS = 24 * 60 * 60
_ROLE_ONLY_ROLLBACK_RESERVE_SECONDS = 15.0
_ROLE_FREQUENCY_CONVERGENCE_MAX_READS = 4
_ROLE_FREQUENCY_CONVERGENCE_ADMISSION_WINDOW_SECONDS = 20.0
_ROLE_FREQUENCY_CONVERGENCE_MAX_INTERVAL_SECONDS = 5.0
_ROLE_FREQUENCY_CONVERGENCE_REQUIRED_EXACT_READS = 2
_STAGED_TRANSPORT_RETRY_DELAY_SECONDS = 2.0
_STAGED_MONITOR_HEARTBEAT_MAX_INTERVAL_SECONDS = 4.0
_STAGED_AUTO_PARTIAL_SETTLE_MAX_SECONDS = 15.0
_STAGED_AUTO_PARTIAL_MAX_STALLED_SAMPLES = 3
# Read-only field sampling observed Pro NowTime advance in independent 22-25 second batches.
# Treat 30 seconds as a conservative early-boundary allowance: a larger hidden lag can only make
# the attended experiment fail closed, never authorize an early Auto transition as schedule-led.
_STAGED_CLOCK_STALENESS_ALLOWANCE_SECONDS = 30.0
# One LAN session boundary can spend 5s closing, 5s connecting, 10s authenticating and 5s
# querying.  Keep a further 5s scheduling margin and do not admit a convergence read unless that
# whole path fits before the observation deadline, which preserves a separate rollback reserve.
_ROLE_FREQUENCY_FRESH_READ_ADMISSION_SECONDS = 30.0
_SCHEDULE_LINKAGE_TEST_MAX_POWER = 45
_LOGGER = logging.getLogger(__name__)


class ScheduleLinkageError(RuntimeError):
    """Base error for the schedule-active linkage-only diagnostic."""


class ScheduleLinkagePreflightError(ScheduleLinkageError):
    """No write was authorized because fresh evidence was unsafe or unsupported."""

    def __init__(
        self,
        message: str,
        *,
        failure: ScheduleLinkageRunFailure | None = None,
    ) -> None:
        resolved = failure or ScheduleLinkageRunFailure.PREFLIGHT_UNEXPECTED
        if not resolved.value.startswith("preflight_"):
            raise ValueError("preflight errors require an allow-listed preflight failure")
        self.failure = resolved
        super().__init__(message)


class ScheduleLinkageApplyError(ScheduleLinkageError):
    """The observation failed, but all intended linkage writes were detached."""


class ScheduleLinkageRollbackError(ScheduleLinkageError):
    """At least one intended linkage write is not proven detached."""


class ScheduleLinkageBusyError(ScheduleLinkageError):
    """Another run or unfinished journal owns the schedule-linkage workflow."""


class ScheduleLinkageJournalClaimError(ScheduleLinkageError):
    """A durable journal was claimed by another process."""


class ScheduleLinkagePhase(StrEnum):
    PREPARED = "prepared"
    APPLYING = "applying"
    ACTIVE = "active"
    ROLLING_BACK = "rolling_back"
    RECOVERY_REQUIRED = "recovery_required"


class ScheduleLinkageStopReason(StrEnum):
    BOUNDARY_VERIFIED = "boundary_verified"
    EPOCH_COMPLETED = "epoch_completed"
    MANUAL = "manual"


class ScheduleLinkageRunProgressKind(StrEnum):
    """Allow-listed, identity-free milestones for one role activation run."""

    FRESH_CAPTURE_STARTED = "fresh_capture_started"
    FRESH_CAPTURE_RETRY_STARTED = "fresh_capture_retry_started"
    FRESH_CAPTURE_COMPLETED = "fresh_capture_completed"
    AUTHORIZATION_STARTED = "authorization_started"
    AUTHORIZATION_COMPLETED = "authorization_completed"
    CONFIRMATION_STARTED = "confirmation_started"
    CONFIRMATION_VERIFIED = "confirmation_verified"
    JOURNAL_STARTED = "journal_started"
    JOURNAL_CREATED = "journal_created"
    FIRST_WRITE_GATE_STARTED = "first_write_gate_started"
    FIRST_WRITE_GATE_VERIFIED = "first_write_gate_verified"
    MASTER_INTENT_STARTED = "master_intent_started"
    MASTER_INTENT_PERSISTED = "master_intent_persisted"
    MASTER_ADAPTER_WRITE_STARTED = "master_adapter_write_started"
    MASTER_ADAPTER_WRITE_COMPLETED = "master_adapter_write_completed"
    MASTER_PAIR_VERIFICATION_STARTED = "master_pair_verification_started"
    MASTER_PAIR_VERIFIED = "master_pair_verified"
    SLAVE_INTENT_STARTED = "slave_intent_started"
    SLAVE_INTENT_PERSISTED = "slave_intent_persisted"
    SLAVE_ADAPTER_WRITE_STARTED = "slave_adapter_write_started"
    SLAVE_ADAPTER_WRITE_COMPLETED = "slave_adapter_write_completed"
    SLAVE_PAIR_VERIFICATION_STARTED = "slave_pair_verification_started"
    SLAVE_PAIR_STATE_READ_RETRY_STARTED = "slave_pair_state_read_retry_started"
    SLAVE_PAIR_VERIFIED = "slave_pair_verified"
    MONITOR_STARTED = "monitor_started"
    MONITOR_TRANSPORT_RETRY_STARTED = "monitor_transport_retry_started"
    MONITOR_COMPLETED = "monitor_completed"
    FAILED = "failed"


class ScheduleLinkageRunFailure(StrEnum):
    """Allow-listed failure locations safe to persist outside the role transaction."""

    PREFLIGHT_STORE = "preflight_store"
    PREFLIGHT_BUSY = "preflight_busy"
    PREFLIGHT_SAFETY_INTERLOCK = "preflight_safety_interlock"
    PREFLIGHT_CAPABILITY = "preflight_capability"
    PREFLIGHT_SESSION_REFRESH = "preflight_session_refresh"
    PREFLIGHT_HEARTBEAT = "preflight_heartbeat"
    PREFLIGHT_EXPLICIT_STATE_READ = "preflight_explicit_state_read"
    PREFLIGHT_STATE_READ = "preflight_state_read"
    PREFLIGHT_CLOCK = "preflight_clock"
    PREFLIGHT_CONTROL_BASELINE = "preflight_control_baseline"
    PREFLIGHT_SCHEDULE_STRUCTURE = "preflight_schedule_structure"
    PREFLIGHT_AUTO_EVIDENCE = "preflight_auto_evidence"
    PREFLIGHT_TIME_WINDOW = "preflight_time_window"
    PREFLIGHT_POWER_GUARD = "preflight_power_guard"
    PREFLIGHT_PAIR_EVIDENCE = "preflight_pair_evidence"
    PREFLIGHT_STAGED_PLAN = "preflight_staged_plan"
    PREFLIGHT_AUTHORIZATION = "preflight_authorization"
    PREFLIGHT_SETTLE = "preflight_settle"
    PREFLIGHT_UNEXPECTED = "preflight_unexpected"
    FRESH_CAPTURE = "fresh_capture"
    FRESH_CAPTURE_SESSION_REFRESH = "fresh_capture_session_refresh"
    FRESH_CAPTURE_HEARTBEAT = "fresh_capture_heartbeat"
    FRESH_CAPTURE_EXPLICIT_STATE_READ = "fresh_capture_explicit_state_read"
    FRESH_CAPTURE_VALIDATION = "fresh_capture_validation"
    FRESH_CAPTURE_DEADLINE = "fresh_capture_deadline"
    FRESH_CAPTURE_SAFETY_INTERLOCK = "fresh_capture_safety_interlock"
    FRESH_CAPTURE_UNEXPECTED = "fresh_capture_unexpected"
    AUTHORIZATION = "authorization"
    CONFIRMATION = "confirmation"
    CONFIRMATION_MISMATCH = "confirmation_mismatch"
    JOURNAL = "journal"
    FIRST_WRITE_GATE = "first_write_gate"
    MASTER_INTENT = "master_intent"
    MASTER_ADAPTER_WRITE = "master_adapter_write"
    MASTER_PAIR_VERIFICATION = "master_pair_verification"
    MASTER_PAIR_SESSION_REFRESH = "master_pair_session_refresh"
    MASTER_PAIR_STATE_READ = "master_pair_state_read"
    MASTER_PAIR_DEADLINE = "master_pair_deadline"
    MASTER_PAIR_CLOCK = "master_pair_clock"
    MASTER_PAIR_CLOCK_SKEW = "master_pair_clock_skew"
    MASTER_PAIR_CLOCK_CONTINUITY = "master_pair_clock_continuity"
    MASTER_PAIR_STATE = "master_pair_state"
    MASTER_PAIR_MASTER_STATE = "master_pair_master_state"
    MASTER_PAIR_SLAVE_STATE = "master_pair_slave_state"
    MASTER_PAIR_AUTO = "master_pair_auto"
    MASTER_PAIR_MASTER_AUTO = "master_pair_master_auto"
    MASTER_PAIR_SLAVE_AUTO = "master_pair_slave_auto"
    SLAVE_INTENT = "slave_intent"
    SLAVE_ADAPTER_WRITE = "slave_adapter_write"
    SLAVE_PAIR_VERIFICATION = "slave_pair_verification"
    SLAVE_PAIR_SESSION_REFRESH = "slave_pair_session_refresh"
    SLAVE_PAIR_STATE_READ = "slave_pair_state_read"
    SLAVE_PAIR_DEADLINE = "slave_pair_deadline"
    SLAVE_PAIR_CLOCK = "slave_pair_clock"
    SLAVE_PAIR_CLOCK_SKEW = "slave_pair_clock_skew"
    SLAVE_PAIR_CLOCK_CONTINUITY = "slave_pair_clock_continuity"
    SLAVE_PAIR_STATE = "slave_pair_state"
    SLAVE_PAIR_MASTER_STATE = "slave_pair_master_state"
    SLAVE_PAIR_SLAVE_STATE = "slave_pair_slave_state"
    SLAVE_PAIR_AUTO = "slave_pair_auto"
    SLAVE_PAIR_MASTER_AUTO = "slave_pair_master_auto"
    SLAVE_PAIR_SLAVE_AUTO = "slave_pair_slave_auto"
    MONITOR = "monitor"
    MONITOR_HEARTBEAT = "monitor_heartbeat"
    MONITOR_STATE_READ = "monitor_state_read"
    MONITOR_STATE_EVIDENCE = "monitor_state_evidence"
    MONITOR_AUTO_EVIDENCE = "monitor_auto_evidence"
    MONITOR_EARLY_AUTO_TRANSITION = "monitor_early_auto_transition"
    MONITOR_AUTO_REGRESSION = "monitor_auto_regression"
    MONITOR_AUTO_TRANSITION_TIMEOUT = "monitor_auto_transition_timeout"
    MONITOR_SESSION_REFRESH = "monitor_session_refresh"
    MONITOR_DEADLINE = "monitor_deadline"
    CANCELLED = "cancelled"


class ScheduleLinkageDriftDimension(StrEnum):
    """Allow-listed dimensions only; no identity or before/after value is disclosed."""

    PHYSICAL_BINDING = "physical_binding"
    ONLINE = "online"
    ERROR = "error"
    ENABLED = "enabled"
    POWER = "power"
    MODE = "mode"
    FREQUENCY = "frequency"
    TIMER_ENABLED = "timer_enabled"
    LINKAGE = "linkage"
    SCHEDULE_FINGERPRINT = "schedule_fingerprint"
    BOUNDARY = "boundary"
    BEFORE_AUTO_MODE = "before_auto_mode"
    BEFORE_AUTO_FLOW = "before_auto_flow"
    BEFORE_AUTO_FREQUENCY = "before_auto_frequency"
    BEFORE_AUTO_FEED_TIME = "before_auto_feed_time"
    NEXT_AUTO_TUPLE = "next_auto_tuple"
    CONFIRMATION_TOKEN = "confirmation_token"
    AUTO_EVIDENCE = "auto_evidence"


class ScheduleLinkageRunProgressEvent(BaseModel):
    """Best-effort run telemetry containing neither device IDs, values, nor exceptions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ScheduleLinkageRunProgressKind
    occurred_at: datetime
    failure: ScheduleLinkageRunFailure | None = None
    drift_dimensions: tuple[ScheduleLinkageDriftDimension, ...] = Field(
        default=(),
        max_length=len(ScheduleLinkageDriftDimension),
    )

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("schedule-linkage progress timestamps must be timezone-aware")
        if self.kind is ScheduleLinkageRunProgressKind.FAILED:
            if self.failure is None:
                raise ValueError("failed schedule-linkage progress requires an allow-listed stage")
        elif self.failure is not None:
            raise ValueError("only failed schedule-linkage progress may include a failure")
        pair_state_failures = {
            ScheduleLinkageRunFailure.MASTER_PAIR_STATE,
            ScheduleLinkageRunFailure.MASTER_PAIR_MASTER_STATE,
            ScheduleLinkageRunFailure.MASTER_PAIR_SLAVE_STATE,
            ScheduleLinkageRunFailure.SLAVE_PAIR_STATE,
            ScheduleLinkageRunFailure.SLAVE_PAIR_MASTER_STATE,
            ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE,
        }
        monitor_state_failures = {
            ScheduleLinkageRunFailure.MONITOR_STATE_EVIDENCE,
        }
        pair_auto_failures = {
            ScheduleLinkageRunFailure.MASTER_PAIR_AUTO,
            ScheduleLinkageRunFailure.MASTER_PAIR_MASTER_AUTO,
            ScheduleLinkageRunFailure.MASTER_PAIR_SLAVE_AUTO,
            ScheduleLinkageRunFailure.SLAVE_PAIR_AUTO,
            ScheduleLinkageRunFailure.SLAVE_PAIR_MASTER_AUTO,
            ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_AUTO,
        }
        monitor_auto_failures = {
            ScheduleLinkageRunFailure.MONITOR_AUTO_EVIDENCE,
            ScheduleLinkageRunFailure.MONITOR_EARLY_AUTO_TRANSITION,
            ScheduleLinkageRunFailure.MONITOR_AUTO_REGRESSION,
            ScheduleLinkageRunFailure.MONITOR_AUTO_TRANSITION_TIMEOUT,
        }
        dimensional_failures = {
            ScheduleLinkageRunFailure.CONFIRMATION_MISMATCH,
            *monitor_state_failures,
            *monitor_auto_failures,
            *pair_state_failures,
            *pair_auto_failures,
        }
        if self.drift_dimensions:
            if self.failure not in dimensional_failures:
                raise ValueError(
                    "drift dimensions require confirmation or pair state evidence"
                )
            canonical = tuple(
                dimension
                for dimension in ScheduleLinkageDriftDimension
                if dimension in self.drift_dimensions
            )
            if self.drift_dimensions != canonical:
                raise ValueError("drift dimensions must be unique and canonically ordered")
        elif self.failure in dimensional_failures:
            raise ValueError("dimensional failure requires at least one drift dimension")
        pair_state_dimensions = {
            ScheduleLinkageDriftDimension.ONLINE,
            ScheduleLinkageDriftDimension.ERROR,
            ScheduleLinkageDriftDimension.ENABLED,
            ScheduleLinkageDriftDimension.POWER,
            ScheduleLinkageDriftDimension.MODE,
            ScheduleLinkageDriftDimension.FREQUENCY,
            ScheduleLinkageDriftDimension.TIMER_ENABLED,
            ScheduleLinkageDriftDimension.LINKAGE,
            ScheduleLinkageDriftDimension.SCHEDULE_FINGERPRINT,
        }
        confirmation_dimensions = {
            ScheduleLinkageDriftDimension.PHYSICAL_BINDING,
            ScheduleLinkageDriftDimension.ENABLED,
            ScheduleLinkageDriftDimension.POWER,
            ScheduleLinkageDriftDimension.MODE,
            ScheduleLinkageDriftDimension.FREQUENCY,
            ScheduleLinkageDriftDimension.TIMER_ENABLED,
            ScheduleLinkageDriftDimension.LINKAGE,
            ScheduleLinkageDriftDimension.SCHEDULE_FINGERPRINT,
            ScheduleLinkageDriftDimension.BOUNDARY,
            ScheduleLinkageDriftDimension.BEFORE_AUTO_MODE,
            ScheduleLinkageDriftDimension.BEFORE_AUTO_FLOW,
            ScheduleLinkageDriftDimension.BEFORE_AUTO_FREQUENCY,
            ScheduleLinkageDriftDimension.BEFORE_AUTO_FEED_TIME,
            ScheduleLinkageDriftDimension.NEXT_AUTO_TUPLE,
            ScheduleLinkageDriftDimension.CONFIRMATION_TOKEN,
        }
        if (
            self.failure is ScheduleLinkageRunFailure.CONFIRMATION_MISMATCH
            and any(
                dimension not in confirmation_dimensions
                for dimension in self.drift_dimensions
            )
        ):
            raise ValueError("confirmation mismatch contains a non-confirmation dimension")
        if self.failure in {*pair_state_failures, *monitor_state_failures} and any(
            dimension not in pair_state_dimensions for dimension in self.drift_dimensions
        ):
            raise ValueError("pair state failure contains a non-state dimension")
        if self.failure in pair_auto_failures and self.drift_dimensions != (
            ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
        ):
            raise ValueError("pair Auto failure requires only the Auto evidence dimension")
        if self.failure in monitor_auto_failures and self.drift_dimensions != (
            ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
        ):
            raise ValueError("monitor Auto failure requires only the Auto evidence dimension")
        return self


def schedule_linkage_run_progress_rank(kind: ScheduleLinkageRunProgressKind) -> int:
    """Return the canonical monotonic rank used by durable outer-stage validation."""

    return tuple(ScheduleLinkageRunProgressKind).index(kind)


def _is_retryable_transport_failure(error: BaseException) -> bool:
    """Return whether a fresh session can safely resolve an acquisition failure."""

    if isinstance(error, HeartbeatFencedStateError):
        error = error.cause
    return isinstance(error, (DeviceConnectionError, ProtocolError, OSError))


class ScheduleLinkageSpec(BaseModel):
    """One bounded, previously-qualified async boundary observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str = Field(
        default_factory=lambda: uuid4().hex,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    qualification_operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    master_device_id: DeviceIdentifier
    slave_device_id: DeviceIdentifier
    observation_window_seconds: float = Field(default=180, gt=0, le=930)
    verification_interval_seconds: float = Field(default=1, gt=0, le=10)
    minimum_lead_seconds: float = Field(default=45, ge=10, le=180)
    ambiguous_band_seconds: float = Field(default=1, ge=0.1, le=5)
    post_boundary_stability_seconds: float = Field(default=0, ge=0, le=300)
    # The dedicated schedule-flow experiment needs to observe firmware behavior, including a
    # slave that remains on its prior tuple or follows the master.  Keep the ordinary role-only
    # diagnostic strict unless this journaled, opt-in evidence mode is explicitly selected.
    observe_slave_after_tuple_variance: bool = False
    complete_observation_epoch: bool = False
    maximum_clock_skew_seconds: float = Field(default=2, ge=0.1, le=30)
    clock_advance_tolerance_seconds: float = Field(default=2, ge=0.1, le=10)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.master_device_id == self.slave_device_id:
            raise ValueError("master and slave devices must be different")
        post_boundary_budget = (
            self.post_boundary_stability_seconds
            + 2 * self.ambiguous_band_seconds
            + 3 * self.verification_interval_seconds
        )
        if self.observation_window_seconds <= self.minimum_lead_seconds:
            raise ValueError("observation window must extend beyond the minimum setup lead")
        if self.observation_window_seconds <= post_boundary_budget:
            raise ValueError("observation window is too short for two fresh boundary samples")
        return self


class ScheduleAutoEvidence(BaseModel):
    """Effective Auto* values observed from a fresh controller read."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: Literal["constant", "pulse", "sine", "feed"]
    flow: int = Field(ge=0, le=100)
    frequency: int = Field(ge=0, le=100)
    feed_time: int | None = Field(default=None, ge=1, le=60)

    @model_validator(mode="after")
    def validate_feed_time(self) -> Self:
        if (self.mode == "feed") != (self.feed_time is not None):
            raise ValueError("AutoFeedTime is required only for feed evidence")
        return self


class ScheduleLinkageSample(BaseModel):
    """One redacted effective sample suitable for durable experiment evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    phase: Literal["before", "after"]
    master: ScheduleAutoEvidence
    slave: ScheduleAutoEvidence
    master_manual_power: int = Field(ge=0, le=100)
    slave_manual_power: int = Field(ge=0, le=100)
    # Pro boundary reports may mirror the scheduled Mode/Frequency into the separate live
    # control registers.  Optional defaults keep previously persisted diagnostic intents
    # readable while new evidence records the exact bounded pair used for stability.
    master_reported_mode: str | None = None
    master_reported_frequency: int | None = Field(default=None, ge=0, le=100)
    slave_reported_mode: str | None = None
    slave_reported_frequency: int | None = Field(default=None, ge=0, le=100)
    master_linkage: Literal[LinkageRole.MASTER]
    slave_linkage: Literal[LinkageRole.ASYNC_SLAVE]

    @model_validator(mode="after")
    def validate_timestamp(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("sample timestamp must be timezone-aware")
        return self


class ScheduleBoundaryExpectation(BaseModel):
    """Absolute device-local boundary and mode-relevant before/after evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    current_slot: int = Field(ge=0, lt=48)
    next_slot: int = Field(ge=0, lt=48)
    boundary_at: datetime
    after_valid_until: datetime
    before: ScheduleAutoEvidence
    after_mode: Literal["constant", "pulse", "sine"]
    after_flow: int = Field(ge=0, le=100)
    # Pro constant slots encode frequency=0 while fresh AutoFreq reports a stable default (5 in
    # captured hardware).  None means range+two-sample stability, never decoded-byte equality.
    after_frequency: int | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def validate_transition(self) -> Self:
        if self.current_slot == self.next_slot:
            raise ValueError("current and next schedule slots must differ")
        if self.boundary_at.tzinfo is not None:
            raise ValueError("device-local schedule boundary must be timezone-naive")
        if self.after_valid_until.tzinfo is not None:
            raise ValueError("device-local next-entry end must be timezone-naive")
        if self.after_valid_until <= self.boundary_at:
            raise ValueError("next-entry validity must end after its boundary")
        if (self.after_mode == "constant") != (self.after_frequency is None):
            raise ValueError("only constant uses an effective, observed AutoFreq")
        return self


class ScheduleLinkageSnapshot(BaseModel):
    """Immutable state that every linkage-only read must preserve."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: DeviceIdentifier
    physical_binding: PhysicalDeviceBinding
    enabled: Literal[True]
    power: int = Field(ge=0, le=100)
    mode: str = Field(min_length=1)
    frequency: int = Field(ge=0, le=100)
    timer_enabled: Literal[True]
    linkage: Literal[LinkageRole.INDEPENDENT]
    schedule_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expectation: ScheduleBoundaryExpectation


class ScheduleLinkageExternalDisarmState(BaseModel):
    """Redacted, in-memory state captured by the composed TimerOFF proof."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: DeviceIdentifier
    physical_binding: PhysicalDeviceBinding
    observed_at: datetime
    online: Literal[True]
    error: None = None
    enabled: Literal[True]
    power: int = Field(ge=0, le=100)
    mode: str = Field(min_length=1)
    frequency: int = Field(ge=0, le=100)
    timer_enabled: Literal[False]
    linkage: Literal[LinkageRole.INDEPENDENT]
    schedule_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")

    @classmethod
    def from_state(
        cls,
        device_id: str,
        state: DeviceState,
        *,
        physical_binding: PhysicalDeviceBinding,
    ) -> Self:
        fingerprint = schedule_structure_fingerprint(state.schedule)
        if fingerprint is None:
            raise ScheduleLinkageRollbackError(
                "external disarm proof has no schedule fingerprint"
            )
        return cls(
            device_id=device_id,
            physical_binding=physical_binding,
            observed_at=state.observed_at,
            online=state.online,
            error=state.error,
            enabled=state.enabled,
            power=state.power,
            mode=state.mode,
            frequency=state.frequency,
            timer_enabled=state.timer_enabled,
            linkage=state.linkage,
            schedule_fingerprint=fingerprint,
        )

    @model_validator(mode="after")
    def validate_timestamp(self) -> Self:
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("external disarm timestamp must be timezone-aware")
        return self


class ScheduleLinkageExternalDisarmProof(BaseModel):
    """Exact pair proof handed directly from composed disarm to role closure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
    )
    states: tuple[ScheduleLinkageExternalDisarmState, ...] = Field(
        min_length=2,
        max_length=2,
    )

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        device_ids = tuple(state.device_id for state in self.states)
        if len(set(device_ids)) != len(device_ids):
            raise ValueError("external disarm proof device IDs must be distinct")
        identity_keys = tuple(
            physical_identity_key(state.physical_binding) for state in self.states
        )
        if len(set(identity_keys)) != len(identity_keys):
            raise ValueError("external disarm proof physical bindings must be distinct")
        return self


class ScheduleLinkagePreflight(BaseModel):
    """Attended read-only evidence bound to a single later run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    spec: ScheduleLinkageSpec
    snapshots: tuple[ScheduleLinkageSnapshot, ...] = Field(min_length=2, max_length=2)
    confirmation_token: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_snapshot_order(self) -> Self:
        expected = (self.spec.master_device_id, self.spec.slave_device_id)
        if tuple(snapshot.device_id for snapshot in self.snapshots) != expected:
            raise ValueError("preflight snapshots must be ordered master then slave")
        return self


class ScheduleLinkageRecord(BaseModel):
    """Durable role-only mutation intent and compensation progress."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    mutation_scope: Literal["linkage_only"] = "linkage_only"
    operation_id: str = Field(min_length=1)
    phase: ScheduleLinkagePhase
    spec: ScheduleLinkageSpec
    snapshots: tuple[ScheduleLinkageSnapshot, ...] = Field(min_length=2, max_length=2)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    linkage_write_intent_device_ids: tuple[str, ...] = ()
    linked_device_ids: tuple[str, ...] = ()
    detached_device_ids: tuple[str, ...] = ()
    error: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def validate_progress(self) -> Self:
        if self.operation_id != self.spec.operation_id:
            raise ValueError("record operation_id must match its spec")
        timestamps = (self.created_at, self.updated_at, self.expires_at)
        if any(value.tzinfo is None for value in timestamps):
            raise ValueError("journal timestamps must be timezone-aware")
        if self.updated_at < self.created_at or self.expires_at <= self.created_at:
            raise ValueError("journal timestamps are not monotonic")
        expected_ids = (self.spec.master_device_id, self.spec.slave_device_id)
        if tuple(snapshot.device_id for snapshot in self.snapshots) != expected_ids:
            raise ValueError("snapshots must be ordered master then slave")
        first_binding, second_binding = (
            snapshot.physical_binding for snapshot in self.snapshots
        )
        if physical_identity_key(first_binding) == physical_identity_key(second_binding):
            raise ValueError("record physical bindings must be distinct")
        intents = self.linkage_write_intent_device_ids
        linked = self.linked_device_ids
        detached = self.detached_device_ids
        if intents not in ((), expected_ids[:1], expected_ids):
            raise ValueError("linkage write intents must be a master-first prefix")
        if linked != intents[: len(linked)]:
            raise ValueError("linked progress must be a prefix of durable write intent")
        detach_order = tuple(reversed(intents))
        if detached != detach_order[: len(detached)]:
            raise ValueError("detached progress must be a strict slave-to-master prefix")
        if self.phase is ScheduleLinkagePhase.PREPARED and (intents or linked or detached):
            raise ValueError("prepared journal cannot contain physical-write progress")
        if self.phase is ScheduleLinkagePhase.PREPARED and self.error is not None:
            raise ValueError("prepared journal cannot contain an error")
        if self.phase is ScheduleLinkagePhase.ACTIVE:
            if linked != expected_ids:
                raise ValueError("active journal must prove both linkage writes")
            if self.error is not None:
                raise ValueError("active journal cannot contain an error")
        if self.phase is ScheduleLinkagePhase.RECOVERY_REQUIRED:
            if not intents or self.error is None:
                raise ValueError(
                    "recovery-required journal needs durable write intent and an error"
                )
        elif self.error is not None:
            raise ValueError("only recovery-required journal may contain an error")
        return self


class ScheduleLinkageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: str
    stop_reason: ScheduleLinkageStopReason
    schedule_transition_verified: bool
    completed_at: datetime


class ScheduleLinkageJournalStore(Protocol):
    def load(self) -> ScheduleLinkageRecord | None: ...

    def lease(self) -> AbstractContextManager[None]: ...

    def create(self, record: ScheduleLinkageRecord) -> None: ...

    def save(self, record: ScheduleLinkageRecord) -> None: ...

    def confirms_lease_successor(self, record: ScheduleLinkageRecord) -> bool: ...

    def clear(self) -> None: ...


PrerequisiteAuthorizer = Callable[
    [ScheduleLinkageSpec, tuple[ScheduleLinkageSnapshot, ...]],
    None,
]
SampleObserver = Callable[[ScheduleLinkageSample], None]
ScheduleLinkageRunProgressObserver = Callable[[ScheduleLinkageRunProgressEvent], None]


@dataclass(frozen=True, slots=True)
class _TransitionPlan:
    current: ScheduleEntry
    next: ScheduleEntry
    seconds_until_boundary: float


@dataclass(frozen=True, slots=True)
class _ClockAnchor:
    clocks: Mapping[str, datetime]
    sampled_at_monotonic: float


type _StagedTransitionField = Literal[
    "auto_mode",
    "auto_flow",
    "auto_frequency",
    "reported_mode",
    "reported_frequency",
]
type _StablePairEvidence = tuple[tuple[str, int, int, str, int], ...]


@dataclass(frozen=True, slots=True)
class _StagedAutoClassification:
    side: Literal["before", "transitional", "after"]
    auto_side: Literal["before", "transitional", "after"]
    control_side: Literal["before", "transitional", "after"]
    b_fields: frozenset[_StagedTransitionField] = frozenset()


def schedule_linkage_confirmation_token(
    spec: ScheduleLinkageSpec,
    snapshots: tuple[ScheduleLinkageSnapshot, ...],
) -> str:
    canonical = {
        "version": 1,
        "spec": spec.model_dump(mode="json"),
        "snapshots": [snapshot.model_dump(mode="json") for snapshot in snapshots],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _schedule_linkage_drift_dimensions(
    expected: tuple[ScheduleLinkageSnapshot, ...],
    actual: tuple[ScheduleLinkageSnapshot, ...],
    *,
    token_matches: bool,
) -> tuple[ScheduleLinkageDriftDimension, ...]:
    """Classify confirmation drift without retaining identities or compared values."""

    dimensions: set[ScheduleLinkageDriftDimension] = set()
    for before, after in zip(expected, actual, strict=True):
        if before.physical_binding != after.physical_binding:
            dimensions.add(ScheduleLinkageDriftDimension.PHYSICAL_BINDING)
        for dimension, before_value, after_value in (
            (ScheduleLinkageDriftDimension.ENABLED, before.enabled, after.enabled),
            (ScheduleLinkageDriftDimension.POWER, before.power, after.power),
            (ScheduleLinkageDriftDimension.MODE, before.mode, after.mode),
            (ScheduleLinkageDriftDimension.FREQUENCY, before.frequency, after.frequency),
            (
                ScheduleLinkageDriftDimension.TIMER_ENABLED,
                before.timer_enabled,
                after.timer_enabled,
            ),
            (ScheduleLinkageDriftDimension.LINKAGE, before.linkage, after.linkage),
            (
                ScheduleLinkageDriftDimension.SCHEDULE_FINGERPRINT,
                before.schedule_fingerprint,
                after.schedule_fingerprint,
            ),
        ):
            if before_value != after_value:
                dimensions.add(dimension)
        before_expectation = before.expectation
        after_expectation = after.expectation
        if (
            before_expectation.current_slot,
            before_expectation.next_slot,
            before_expectation.boundary_at,
            before_expectation.after_valid_until,
        ) != (
            after_expectation.current_slot,
            after_expectation.next_slot,
            after_expectation.boundary_at,
            after_expectation.after_valid_until,
        ):
            dimensions.add(ScheduleLinkageDriftDimension.BOUNDARY)
        for dimension, before_value, after_value in (
            (
                ScheduleLinkageDriftDimension.BEFORE_AUTO_MODE,
                before_expectation.before.mode,
                after_expectation.before.mode,
            ),
            (
                ScheduleLinkageDriftDimension.BEFORE_AUTO_FLOW,
                before_expectation.before.flow,
                after_expectation.before.flow,
            ),
            (
                ScheduleLinkageDriftDimension.BEFORE_AUTO_FREQUENCY,
                before_expectation.before.frequency,
                after_expectation.before.frequency,
            ),
            (
                ScheduleLinkageDriftDimension.BEFORE_AUTO_FEED_TIME,
                before_expectation.before.feed_time,
                after_expectation.before.feed_time,
            ),
        ):
            if before_value != after_value:
                dimensions.add(dimension)
        if (
            before_expectation.after_mode,
            before_expectation.after_flow,
            before_expectation.after_frequency,
        ) != (
            after_expectation.after_mode,
            after_expectation.after_flow,
            after_expectation.after_frequency,
        ):
            dimensions.add(ScheduleLinkageDriftDimension.NEXT_AUTO_TUPLE)
    if not token_matches and not dimensions:
        dimensions.add(ScheduleLinkageDriftDimension.CONFIRMATION_TOKEN)
    return tuple(
        dimension
        for dimension in ScheduleLinkageDriftDimension
        if dimension in dimensions
    )


def _wall_seconds(value: str) -> int:
    try:
        hour_text, minute_text = value.split(":", maxsplit=1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (AttributeError, TypeError, ValueError) as error:
        raise ScheduleLinkagePreflightError(
            "decoded schedule has an invalid wall time",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
        ) from error
    if hour == 24 and minute == 0:
        return _DAY_SECONDS
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ScheduleLinkagePreflightError(
            "decoded schedule has an invalid wall time",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
        )
    return hour * 60 * 60 + minute * 60


def _decoded_values(entry: ScheduleEntry) -> tuple[str, int, int, int | None]:
    mode = entry.mode
    if mode not in _KNOWN_PRO_MODES:
        raise ScheduleLinkagePreflightError(
            "decoded schedule has an unknown mode",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
        )
    flow = entry.parameters.get("flow")
    frequency = entry.parameters.get("frequency")
    for label, value in (("flow", flow), ("frequency", frequency)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ScheduleLinkagePreflightError(
                f"decoded schedule has an invalid {label} value",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
            )
    feed_time: int | None = None
    if mode == "feed":
        value = entry.parameters.get("feed_time")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 60:
            raise ScheduleLinkagePreflightError(
                "decoded feed entry has an invalid feed time",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
            )
        feed_time = value
    return mode, flow, frequency, feed_time


def _validated_entries(schedule: DeviceSchedule) -> tuple[ScheduleEntry, ...]:
    if schedule.invalid_slots:
        raise ScheduleLinkagePreflightError(
            "decoded schedule contains invalid slots",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
        )
    if len(schedule.entries) < 2:
        raise ScheduleLinkagePreflightError(
            "at least two decoded schedule entries are required",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
        )
    entries = tuple(sorted(schedule.entries, key=lambda entry: _wall_seconds(entry.start)))
    starts = tuple(_wall_seconds(entry.start) for entry in entries)
    if len(set(starts)) != len(starts):
        raise ScheduleLinkagePreflightError(
            "decoded schedule has duplicate entry starts",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
        )
    for index, entry in enumerate(entries):
        start = starts[index]
        end = _wall_seconds(entry.end) % _DAY_SECONDS
        duration = (end - start) % _DAY_SECONDS
        if duration == 0:
            raise ScheduleLinkagePreflightError(
                "decoded schedule has a zero-length entry",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
            )
        next_start = starts[(index + 1) % len(entries)]
        if index + 1 == len(entries):
            next_start += _DAY_SECONDS
        if start + duration > next_start:
            raise ScheduleLinkagePreflightError(
                "decoded schedule entries overlap",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
            )
        _decoded_values(entry)
    return entries


def _transition_plan(device_id: str, state: DeviceState) -> _TransitionPlan:
    schedule = state.schedule
    if schedule is None or schedule.device_local_time is None:
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} has no decoded device-local schedule clock",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_CLOCK,
        )
    if state.timer_enabled is not True or schedule.enabled is not True:
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} schedule-active test requires TimerON",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_CONTROL_BASELINE,
        )
    if schedule.device_local_time.tzinfo is not None:
        raise ScheduleLinkagePreflightError(
            "device-local schedule clock must be timezone-naive",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_CLOCK,
        )
    entries = _validated_entries(schedule)
    clock = schedule.device_local_time
    now_seconds = (
        clock.hour * 60 * 60
        + clock.minute * 60
        + clock.second
        + clock.microsecond / 1_000_000
    )
    active: list[tuple[int, ScheduleEntry, float]] = []
    for index, entry in enumerate(entries):
        start = _wall_seconds(entry.start)
        end = _wall_seconds(entry.end) % _DAY_SECONDS
        duration = (end - start) % _DAY_SECONDS
        elapsed = (now_seconds - start) % _DAY_SECONDS
        if elapsed < duration:
            active.append((index, entry, duration - elapsed))
    if len(active) != 1:
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} clock does not select exactly one schedule entry",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
        )
    index, current, remaining = active[0]
    next_entry = entries[(index + 1) % len(entries)]
    if _wall_seconds(current.end) % _DAY_SECONDS != _wall_seconds(next_entry.start):
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} current boundary is not contiguous with its next entry",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
        )
    current_mode = _decoded_values(current)[0]
    next_mode = _decoded_values(next_entry)[0]
    if current_mode not in _CURRENT_MODES or next_mode not in _NEXT_MODES:
        raise ScheduleLinkagePreflightError(
            "current/next schedule modes are outside the first audited boundary set",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
        )
    return _TransitionPlan(current=current, next=next_entry, seconds_until_boundary=remaining)


def _observed_auto(device_id: str, state: DeviceState) -> ScheduleAutoEvidence:
    values = state.observed_attributes
    mode = values.get("AutoMode")
    flow = values.get("AutoFlow")
    frequency = values.get("AutoFreq")
    if not isinstance(mode, str) or mode not in _CURRENT_MODES:
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} reported invalid AutoMode",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_AUTO_EVIDENCE,
        )
    for label, value in (("AutoFlow", flow), ("AutoFreq", frequency)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ScheduleLinkagePreflightError(
                f"device {device_id!r} reported invalid {label}",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_AUTO_EVIDENCE,
            )
    feed_time: int | None = None
    if mode == "feed":
        value = values.get("AutoFeedTime")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 60:
            raise ScheduleLinkagePreflightError(
                f"device {device_id!r} reported invalid AutoFeedTime",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_AUTO_EVIDENCE,
            )
        feed_time = value
    return ScheduleAutoEvidence(
        mode=mode,
        flow=flow,
        frequency=frequency,
        feed_time=feed_time,
    )


def _assert_entry_evidence(
    device_id: str,
    evidence: ScheduleAutoEvidence,
    entry: ScheduleEntry,
) -> None:
    mode, flow, frequency, feed_time = _decoded_values(entry)
    if evidence.mode != mode:
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} AutoMode disagrees with its active entry",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_AUTO_EVIDENCE,
        )
    if mode == "feed":
        # Captured Pro firmware reports effective defaults (30/5) while feed encodes 0/0.
        if evidence.feed_time != feed_time:
            raise ScheduleLinkagePreflightError(
                f"device {device_id!r} AutoFeedTime disagrees with its feed entry",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_AUTO_EVIDENCE,
            )
    elif mode == "constant":
        # Constant frequency is likewise an ignored encoded zero; only Mode+Flow are semantic.
        if evidence.flow != flow:
            raise ScheduleLinkagePreflightError(
                f"device {device_id!r} AutoFlow disagrees with its constant entry",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_AUTO_EVIDENCE,
            )
    elif evidence.flow != flow or evidence.frequency != frequency:
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} AutoFlow/AutoFreq disagree with its active entry",
            failure=ScheduleLinkageRunFailure.PREFLIGHT_AUTO_EVIDENCE,
        )


def _expectation_from_state(
    device_id: str,
    state: DeviceState,
) -> tuple[_TransitionPlan, ScheduleBoundaryExpectation]:
    plan = _transition_plan(device_id, state)
    before = _observed_auto(device_id, state)
    _assert_entry_evidence(device_id, before, plan.current)
    after_mode, after_flow, after_frequency, _ = _decoded_values(plan.next)
    schedule = state.schedule
    if schedule is None or schedule.device_local_time is None:
        raise AssertionError("validated transition has no schedule clock")
    next_start = _wall_seconds(plan.next.start)
    next_end = _wall_seconds(plan.next.end) % _DAY_SECONDS
    next_duration = (next_end - next_start) % _DAY_SECONDS
    boundary_at = schedule.device_local_time + timedelta(
        seconds=plan.seconds_until_boundary
    )
    return plan, ScheduleBoundaryExpectation(
        current_slot=plan.current.slot,
        next_slot=plan.next.slot,
        boundary_at=boundary_at,
        after_valid_until=boundary_at + timedelta(seconds=next_duration),
        before=before,
        after_mode=after_mode,
        after_flow=after_flow,
        after_frequency=None if after_mode == "constant" else after_frequency,
    )


class ScheduleActiveLinkageController:
    """Bounded role-only saga with durable intent-before-write compensation."""

    def __init__(
        self,
        devices: Mapping[str, JebaoDevice],
        store: ScheduleLinkageJournalStore,
        *,
        prerequisite_authorizer: PrerequisiteAuthorizer,
        safety_interlock: LinkageSafetyInterlock,
        monotonic_clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        sample_observer: SampleObserver | None = None,
        progress_observer: ScheduleLinkageRunProgressObserver | None = None,
        refresh_sessions_before_critical_reads: bool = False,
        owned_staged_auto_transition_observation: bool = False,
    ) -> None:
        self._devices = dict(devices)
        self._store = store
        self._authorize = prerequisite_authorizer
        self._safety_interlock = safety_interlock
        self._monotonic_clock = monotonic_clock
        self._sleep = sleep
        self._sample_observer = sample_observer
        self._progress_observer = progress_observer
        self._refresh_sessions_before_critical_reads = (
            refresh_sessions_before_critical_reads
        )
        self._owned_staged_auto_transition_observation = (
            owned_staged_auto_transition_observation
        )
        self._run_lock = asyncio.Lock()
        self._active_operation_id: str | None = None
        self._safety_epoch: int | None = None
        self._stop_event: asyncio.Event | None = None
        self._forward_deadline: float | None = None
        self._observation_deadline: float | None = None
        self._staged_transition_not_before: float | None = None
        self._staged_role_frequency_allowlist: frozenset[int] = frozenset()
        self._staged_role_frequency_pins: dict[str, int] = {}
        self._run_failure: ScheduleLinkageRunFailure | None = None
        self._run_drift_dimensions: tuple[ScheduleLinkageDriftDimension, ...] = ()
        self._run_pair_participant: Literal["master", "slave"] | None = None

    @property
    def active_operation_id(self) -> str | None:
        return self._active_operation_id

    async def preflight(self, spec: ScheduleLinkageSpec) -> ScheduleLinkagePreflight:
        """Capture an attended, write-free authorization bound to an absolute boundary."""

        try:
            pending = self._store.load()
        except Exception as error:
            raise ScheduleLinkagePreflightError(
                "schedule-linkage preflight store could not be inspected",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_STORE,
            ) from error
        if pending is not None:
            raise ScheduleLinkageBusyError("unfinished schedule-linkage recovery exists")
        if not self._safety_interlock.permitted:
            raise ScheduleLinkagePreflightError(
                "schedule-linkage is blocked by the safety latch",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SAFETY_INTERLOCK,
            )
        snapshots = await self._capture_pair(spec)
        try:
            self._authorize(spec, snapshots)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise ScheduleLinkagePreflightError(
                "schedule-linkage preflight authorization was rejected",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_AUTHORIZATION,
            ) from error
        return ScheduleLinkagePreflight(
            spec=spec,
            snapshots=snapshots,
            confirmation_token=schedule_linkage_confirmation_token(spec, snapshots),
        )

    async def run(self, preflight: ScheduleLinkagePreflight) -> ScheduleLinkageResult:
        if self._run_lock.locked():
            raise ScheduleLinkageBusyError("another schedule-linkage transaction is running")
        async with self._run_lock:
            try:
                lease = self._store.lease()
                lease.__enter__()
            except ScheduleLinkageJournalClaimError as error:
                raise ScheduleLinkageBusyError(
                    "another process owns the schedule-linkage journal"
                ) from error
            try:
                return await self._run_owned(preflight)
            finally:
                lease.__exit__(None, None, None)

    async def stop(self, operation_id: str | None = None) -> bool:
        if self._stop_event is None or self._active_operation_id is None:
            return False
        if operation_id is not None and operation_id != self._active_operation_id:
            return False
        self._stop_event.set()
        return True

    async def recover_pending(self) -> bool:
        """Detach only roles from an unfinished journal; never resume the observation."""

        if self._run_lock.locked():
            raise ScheduleLinkageBusyError("another schedule-linkage transaction is running")
        async with self._run_lock:
            try:
                lease = self._store.lease()
                lease.__enter__()
            except ScheduleLinkageJournalClaimError as error:
                raise ScheduleLinkageBusyError(
                    "another process owns the schedule-linkage journal"
                ) from error
            try:
                record = self._store.load()
                if record is None:
                    return False
                self._validate_recovery_bindings(
                    record,
                    permit_disconnected=self._refresh_sessions_before_critical_reads,
                )
                self._active_operation_id = record.operation_id
                self._stop_event = asyncio.Event()
                if not record.linkage_write_intent_device_ids:
                    await self._assert_recovery_role_topology(record)
                    self._store.clear()
                else:
                    await self._rollback_uninterruptibly(record)
                return True
            finally:
                self._active_operation_id = None
                self._stop_event = None
                self._staged_role_frequency_allowlist = frozenset()
                self._staged_role_frequency_pins.clear()
                lease.__exit__(None, None, None)

    async def finalize_externally_disarmed(
        self,
        operation_id: str,
        *,
        proof: ScheduleLinkageExternalDisarmProof,
    ) -> bool:
        """Clear a role journal only after both controls prove independent and TimerOFF.

        The composed schedule-flow transaction owns the compensating full-control write.  Once it
        has stopped TimerON on both devices, ordinary role recovery can no longer require the
        immutable TimerON snapshot. The composed controller passes the redacted immutable states
        captured by that exact fresh disarm proof, avoiding an immediate second LAN session
        replacement. This no-write closure keeps the exception narrow and bound to the exact role
        operation while the temporary schedule fingerprint is still installed.
        """

        if self._run_lock.locked():
            raise ScheduleLinkageBusyError("another schedule-linkage transaction is running")
        async with self._run_lock:
            try:
                lease = self._store.lease()
                lease.__enter__()
            except ScheduleLinkageJournalClaimError as error:
                raise ScheduleLinkageBusyError(
                    "another process owns the schedule-linkage journal"
                ) from error
            try:
                try:
                    record = self._store.load()
                    if record is None:
                        return False
                    if record.operation_id != operation_id:
                        raise ScheduleLinkageRollbackError(
                            "external disarm does not own this schedule-linkage journal"
                        )
                    if proof.operation_id != operation_id:
                        raise ScheduleLinkageRollbackError(
                            "external disarm proof does not own this schedule-linkage journal"
                        )
                    self._validate_recovery_bindings(
                        record,
                        permit_disconnected=True,
                    )
                    expected_ids = (
                        record.spec.master_device_id,
                        record.spec.slave_device_id,
                    )
                    if tuple(state.device_id for state in proof.states) != expected_ids:
                        raise ScheduleLinkageRollbackError(
                            "external disarm proof does not contain the ordered role pair"
                        )
                    for snapshot, state in zip(
                        record.snapshots,
                        proof.states,
                        strict=True,
                    ):
                        if state.physical_binding != snapshot.physical_binding:
                            raise ScheduleLinkageRollbackError(
                                "external disarm proof changed physical binding"
                            )
                        self._assert_externally_disarmed_proof(snapshot, state)
                    self._store.clear()
                    return True
                except ScheduleLinkageRollbackError:
                    raise
                except Exception as error:
                    raise ScheduleLinkageRollbackError(
                        "external schedule-linkage disarm could not be proven"
                    ) from error
            finally:
                lease.__exit__(None, None, None)

    async def _run_owned(
        self,
        preflight: ScheduleLinkagePreflight,
    ) -> ScheduleLinkageResult:
        if self._store.load() is not None:
            raise ScheduleLinkageBusyError("unfinished schedule-linkage recovery exists")
        spec = preflight.spec
        self._active_operation_id = spec.operation_id
        self._stop_event = asyncio.Event()
        self._staged_role_frequency_allowlist = frozenset()
        self._staged_role_frequency_pins.clear()
        self._safety_epoch = self._safety_interlock.epoch
        started_at = datetime.now(UTC)
        started_monotonic = self._monotonic()
        self._observation_deadline = (
            started_monotonic
            + spec.observation_window_seconds
            - _ROLE_ONLY_ROLLBACK_RESERVE_SECONDS
        )
        record: ScheduleLinkageRecord | None = None
        journal_created = False
        try:
            self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.FRESH_CAPTURE_STARTED
            )
            # The owned staged capture refreshes internally so standalone opt-in behavior stays
            # unchanged while both attended preflight and run use the same reply-only contract.
            if not self._owned_staged_auto_transition_observation:
                await self._refresh_pair_sessions_if_enabled(spec)
            fresh = await self._capture_fresh_pair_for_run(spec)
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.FRESH_CAPTURE_COMPLETED
            )
            self._run_failure = ScheduleLinkageRunFailure.AUTHORIZATION
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.AUTHORIZATION_STARTED
            )
            self._authorize(spec, fresh)
            self._assert_observation_deadline()
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.AUTHORIZATION_COMPLETED
            )
            self._run_failure = ScheduleLinkageRunFailure.CONFIRMATION
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.CONFIRMATION_STARTED
            )
            fresh_token = schedule_linkage_confirmation_token(spec, fresh)
            token_matches = _constant_time_equal(
                fresh_token,
                preflight.confirmation_token,
            )
            if fresh != preflight.snapshots or not token_matches:
                self._run_failure = ScheduleLinkageRunFailure.CONFIRMATION_MISMATCH
                self._run_drift_dimensions = _schedule_linkage_drift_dimensions(
                    preflight.snapshots,
                    fresh,
                    token_matches=token_matches,
                )
                raise ScheduleLinkagePreflightError(
                    "schedule evidence changed after confirmation; no role write was sent"
                )
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.CONFIRMATION_VERIFIED
            )
            self._run_failure = ScheduleLinkageRunFailure.JOURNAL
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.JOURNAL_STARTED
            )
            record = ScheduleLinkageRecord(
                operation_id=spec.operation_id,
                phase=ScheduleLinkagePhase.PREPARED,
                spec=spec,
                snapshots=fresh,
                created_at=started_at,
                updated_at=started_at,
                expires_at=started_at + timedelta(seconds=spec.observation_window_seconds),
            )
            self._store.create(record)
            journal_created = True
            record = self._transition(record, ScheduleLinkagePhase.APPLYING)
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.JOURNAL_CREATED
            )
            # The final gate is after the durable APPLYING record and directly before the first
            # durable write intent.  The device-level guard then checks the monotonic budget at
            # the last possible moment without retransmitting the control frame.
            self._run_failure = ScheduleLinkageRunFailure.FIRST_WRITE_GATE
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.FIRST_WRITE_GATE_STARTED
            )
            clock_anchor = await self._assert_first_write_gate(record)
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.FIRST_WRITE_GATE_VERIFIED
            )
            if self._stop_requested():
                self._store.clear()
                return ScheduleLinkageResult(
                    operation_id=spec.operation_id,
                    stop_reason=ScheduleLinkageStopReason.MANUAL,
                    schedule_transition_verified=False,
                    completed_at=datetime.now(UTC),
                )
            record, clock_anchor = await self._link_device(
                record,
                spec.master_device_id,
                LinkageRole.MASTER,
                clock_anchor,
            )
            record, clock_anchor = await self._link_device(
                record,
                spec.slave_device_id,
                LinkageRole.ASYNC_SLAVE,
                clock_anchor,
            )
            self._run_failure = ScheduleLinkageRunFailure.MONITOR
            record = self._transition(record, ScheduleLinkagePhase.ACTIVE)
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.MONITOR_STARTED
            )
            stop_reason, verified = await self._monitor_boundary(record, clock_anchor)
            self._emit_progress_best_effort(
                ScheduleLinkageRunProgressKind.MONITOR_COMPLETED
            )
        except BaseException as operation_error:
            if isinstance(operation_error, asyncio.CancelledError):
                self._run_failure = ScheduleLinkageRunFailure.CANCELLED
                self._run_drift_dimensions = ()
                self._run_pair_participant = None
            self._emit_failure_best_effort()
            if record is None or not journal_created:
                raise
            try:
                await self._rollback_uninterruptibly(record)
            except asyncio.CancelledError:
                raise
            except ScheduleLinkageRollbackError:
                raise
            except BaseException as rollback_error:
                raise ScheduleLinkageRollbackError(
                    "schedule-linkage role detach could not be completed"
                ) from rollback_error
            if isinstance(operation_error, asyncio.CancelledError):
                raise operation_error
            if self._stop_requested():
                return ScheduleLinkageResult(
                    operation_id=spec.operation_id,
                    stop_reason=ScheduleLinkageStopReason.MANUAL,
                    schedule_transition_verified=False,
                    completed_at=datetime.now(UTC),
                )
            raise ScheduleLinkageApplyError(
                "schedule-linkage observation failed and all roles were detached"
            ) from operation_error
        else:
            await self._rollback_uninterruptibly(record)
            return ScheduleLinkageResult(
                operation_id=spec.operation_id,
                stop_reason=stop_reason,
                schedule_transition_verified=verified,
                completed_at=datetime.now(UTC),
            )
        finally:
            self._active_operation_id = None
            self._stop_event = None
            self._safety_epoch = None
            self._forward_deadline = None
            self._observation_deadline = None
            self._staged_transition_not_before = None
            self._staged_role_frequency_allowlist = frozenset()
            self._staged_role_frequency_pins.clear()
            self._run_failure = None
            self._run_drift_dimensions = ()
            self._run_pair_participant = None

    async def _capture_pair(
        self,
        spec: ScheduleLinkageSpec,
        *,
        permit_disconnected_before_refresh: bool = False,
    ) -> tuple[ScheduleLinkageSnapshot, ...]:
        master = self._get_device(spec.master_device_id)
        slave = self._get_device(spec.slave_device_id)
        self._validate_capabilities(
            master,
            LinkageRole.MASTER,
            permit_disconnected=permit_disconnected_before_refresh,
        )
        self._validate_capabilities(
            slave,
            LinkageRole.ASYNC_SLAVE,
            permit_disconnected=permit_disconnected_before_refresh,
        )
        if master.capabilities.product_key != slave.capabilities.product_key:
            raise ScheduleLinkagePreflightError(
                "schedule-linkage requires the same qualified product family",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CAPABILITY,
            )
        if self._owned_staged_auto_transition_observation:
            if not self._refresh_sessions_before_critical_reads:
                raise ScheduleLinkagePreflightError(
                    "staged Auto transition observation requires explicit critical reads",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_CAPABILITY,
                )
            # Finish both disconnects and both reconnects before either device can contribute
            # proof. Cancellation completes that paired boundary, then propagates without a read.
            try:
                await self._refresh_pair_sessions_uninterruptibly(spec)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise ScheduleLinkagePreflightError(
                    "schedule-linkage preflight session refresh failure",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_SESSION_REFRESH,
                ) from error
            # A failed prior refresh may leave one participant disconnected. Static capability
            # checks are safe before the retry, but only this completed paired boundary may
            # contribute connectivity evidence to a fresh capture.
            self._validate_capabilities(master, LinkageRole.MASTER)
            self._validate_capabilities(slave, LinkageRole.ASYNC_SLAVE)
            try:
                await self._heartbeat_pair(spec)
            except BaseException as error:
                # Heartbeat is read-only, but an incomplete exchange leaves the paired streams
                # unsuitable for any later owner. Retire both before returning the refusal.
                try:
                    await self._refresh_pair_sessions_uninterruptibly(spec)
                except asyncio.CancelledError:
                    raise
                except BaseException as cleanup_error:
                    del cleanup_error
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise ScheduleLinkagePreflightError(
                    "schedule-linkage preflight heartbeat failed",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_HEARTBEAT,
                ) from error
            try:
                states = await self._read_pair_explicit_states(spec)
            except BaseException as error:
                # Explicit reply failure/cancellation retires at least one LAN session. Complete
                # a paired fresh transport boundary before the composed owner can issue its first
                # compensating TimerOFF write, while preserving the original failure category.
                try:
                    await self._refresh_pair_sessions_uninterruptibly(spec)
                except asyncio.CancelledError:
                    raise
                except BaseException as cleanup_error:
                    del cleanup_error
                if isinstance(error, asyncio.CancelledError):
                    raise
                raise ScheduleLinkagePreflightError(
                    "schedule-linkage explicit preflight state read failed",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_EXPLICIT_STATE_READ,
                ) from error
        else:
            try:
                states = await self._read_pair_states(spec)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise ScheduleLinkagePreflightError(
                    "schedule-linkage preflight state read failed",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_STATE_READ,
                ) from error
        try:
            self._assert_pair_clock_skew(spec, states)
        except ScheduleLinkagePreflightError as error:
            if error.failure is ScheduleLinkageRunFailure.PREFLIGHT_CLOCK:
                raise
            raise ScheduleLinkagePreflightError(
                "schedule-linkage preflight clock evidence is invalid",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CLOCK,
            ) from error
        except Exception as error:
            raise ScheduleLinkagePreflightError(
                "schedule-linkage preflight clock evidence is invalid",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CLOCK,
            ) from error
        snapshots = self._snapshots_from_states(spec, states)
        return snapshots

    async def _capture_fresh_pair_for_run(
        self,
        spec: ScheduleLinkageSpec,
    ) -> tuple[ScheduleLinkageSnapshot, ...]:
        """Capture run authorization, retrying one transport-only staged failure.

        No role journal or role write exists at this point. The owned composed experiment may
        therefore replace both sessions once after a refresh or explicit-reply failure. Any
        capability, control, clock, schedule, Auto, time-window, or power failure remains an
        immediate refusal, and the successful capture still has to match the token-bound
        preflight exactly before the first role write.
        """

        try:
            fresh = await self._capture_pair(
                spec,
                permit_disconnected_before_refresh=(
                    self._owned_staged_auto_transition_observation
                ),
            )
        except asyncio.CancelledError:
            raise
        except ScheduleLinkagePreflightError as error:
            self._run_failure = self._fresh_capture_failure(error.failure)
            if (
                not self._owned_staged_auto_transition_observation
                or error.failure
                not in {
                    ScheduleLinkageRunFailure.PREFLIGHT_SESSION_REFRESH,
                    ScheduleLinkageRunFailure.PREFLIGHT_HEARTBEAT,
                    ScheduleLinkageRunFailure.PREFLIGHT_EXPLICIT_STATE_READ,
                }
            ):
                raise
        except BaseException:
            self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_UNEXPECTED
            raise
        else:
            return self._complete_fresh_capture(fresh)

        self._emit_progress_best_effort(
            ScheduleLinkageRunProgressKind.FRESH_CAPTURE_RETRY_STARTED
        )
        await self._wait_for_fresh_capture_retry()
        try:
            fresh = await self._capture_pair(
                spec,
                permit_disconnected_before_refresh=(
                    self._owned_staged_auto_transition_observation
                ),
            )
        except asyncio.CancelledError:
            raise
        except ScheduleLinkagePreflightError as error:
            self._run_failure = self._fresh_capture_failure(error.failure)
            raise
        except BaseException:
            self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_UNEXPECTED
            raise
        else:
            return self._complete_fresh_capture(fresh)

    def _complete_fresh_capture(
        self,
        fresh: tuple[ScheduleLinkageSnapshot, ...],
    ) -> tuple[ScheduleLinkageSnapshot, ...]:
        # A paired session refresh is intentionally uninterruptible so cancellation cannot leave
        # one participant contributing evidence from an older transport.  Stop or safety may
        # therefore change while that boundary or its explicit reads complete.  Recheck both
        # authorities here, before authorization, confirmation, and durable journal creation.
        if self._stop_requested():
            self._run_failure = ScheduleLinkageRunFailure.CANCELLED
            raise ScheduleLinkageApplyError(
                "schedule-linkage stop was requested during fresh capture"
            )
        if (
            self._safety_epoch is None
            or not self._safety_interlock.permitted
            or self._safety_interlock.epoch != self._safety_epoch
        ):
            self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_SAFETY_INTERLOCK
            raise ScheduleLinkageApplyError(
                "schedule-linkage safety authority was revoked during fresh capture"
            )
        try:
            self._assert_observation_deadline()
        except ScheduleLinkageApplyError:
            self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_DEADLINE
            raise
        return fresh

    @staticmethod
    def _fresh_capture_failure(
        failure: ScheduleLinkageRunFailure,
    ) -> ScheduleLinkageRunFailure:
        if failure is ScheduleLinkageRunFailure.PREFLIGHT_SESSION_REFRESH:
            return ScheduleLinkageRunFailure.FRESH_CAPTURE_SESSION_REFRESH
        if failure is ScheduleLinkageRunFailure.PREFLIGHT_HEARTBEAT:
            return ScheduleLinkageRunFailure.FRESH_CAPTURE_HEARTBEAT
        if failure is ScheduleLinkageRunFailure.PREFLIGHT_EXPLICIT_STATE_READ:
            return ScheduleLinkageRunFailure.FRESH_CAPTURE_EXPLICIT_STATE_READ
        if failure is ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW:
            return ScheduleLinkageRunFailure.FRESH_CAPTURE_DEADLINE
        if failure is ScheduleLinkageRunFailure.PREFLIGHT_UNEXPECTED:
            return ScheduleLinkageRunFailure.FRESH_CAPTURE_UNEXPECTED
        return ScheduleLinkageRunFailure.FRESH_CAPTURE_VALIDATION

    async def _wait_for_fresh_capture_retry(self) -> None:
        """Wait once without allowing stop, safety, or deadline authority to drift."""

        retry_delay = _STAGED_TRANSPORT_RETRY_DELAY_SECONDS
        now = self._monotonic()
        if (
            self._require_observation_deadline() - now
            < retry_delay + _ROLE_FREQUENCY_FRESH_READ_ADMISSION_SECONDS
        ):
            self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_DEADLINE
            raise ScheduleLinkagePreflightError(
                "fresh capture retry lacks a complete read-only session budget",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW,
            )
        if (
            self._safety_epoch is None
            or not self._safety_interlock.permitted
            or self._safety_interlock.epoch != self._safety_epoch
        ):
            self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_SAFETY_INTERLOCK
            raise ScheduleLinkagePreflightError(
                "fresh capture retry lost safety authority",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SAFETY_INTERLOCK,
            )
        if self._stop_requested():
            self._run_failure = ScheduleLinkageRunFailure.CANCELLED
            raise ScheduleLinkageApplyError(
                "schedule-linkage stop was requested before fresh capture retry"
            )

        stop_event = self._stop_event
        if stop_event is None:
            self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_UNEXPECTED
            raise ScheduleLinkageApplyError("schedule-linkage stop authority is unavailable")
        settle_task = asyncio.ensure_future(self._sleep(retry_delay))
        stop_task = asyncio.create_task(stop_event.wait())
        safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        tasks = (settle_task, stop_task, safety_task)
        try:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                self._run_failure = ScheduleLinkageRunFailure.CANCELLED
                raise ScheduleLinkageApplyError(
                    "schedule-linkage stop was requested during fresh capture retry"
                )
            if safety_task in done:
                self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_SAFETY_INTERLOCK
                raise ScheduleLinkageApplyError(
                    "schedule-linkage safety authority was revoked during fresh capture retry"
                )
            try:
                settle_task.result()
            except BaseException:
                self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_UNEXPECTED
                raise
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        now = self._monotonic()
        if self._stop_requested():
            self._run_failure = ScheduleLinkageRunFailure.CANCELLED
            raise ScheduleLinkageApplyError(
                "schedule-linkage stop was requested after fresh capture retry settle"
            )
        if (
            self._require_observation_deadline() - now
            < _ROLE_FREQUENCY_FRESH_READ_ADMISSION_SECONDS
        ):
            self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_DEADLINE
            raise ScheduleLinkagePreflightError(
                "fresh capture retry no longer has a complete read-only session budget",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW,
            )
        if (
            self._safety_epoch is None
            or not self._safety_interlock.permitted
            or self._safety_interlock.epoch != self._safety_epoch
        ):
            self._run_failure = ScheduleLinkageRunFailure.FRESH_CAPTURE_SAFETY_INTERLOCK
            raise ScheduleLinkagePreflightError(
                "fresh capture retry lost safety authority",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SAFETY_INTERLOCK,
            )

    def _snapshots_from_states(
        self,
        spec: ScheduleLinkageSpec,
        states: Mapping[str, DeviceState],
    ) -> tuple[ScheduleLinkageSnapshot, ...]:
        snapshots = tuple(
            self._snapshot_from_state(
                self._get_device(device_id),
                states[device_id],
                spec,
            )
            for device_id in (spec.master_device_id, spec.slave_device_id)
        )
        master_expectation, slave_expectation = (
            snapshot.expectation for snapshot in snapshots
        )
        if physical_identity_key(snapshots[0].physical_binding) == physical_identity_key(
            snapshots[1].physical_binding
        ):
            raise ScheduleLinkagePreflightError(
                "master and slave physical bindings must be distinct",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_PAIR_EVIDENCE,
            )
        if (
            master_expectation.boundary_at != slave_expectation.boundary_at
            or master_expectation.before.mode != slave_expectation.before.mode
            or master_expectation.after_mode != slave_expectation.after_mode
        ):
            raise ScheduleLinkagePreflightError(
                "both devices must authorize the same absolute schedule boundary",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_PAIR_EVIDENCE,
            )
        if slave_expectation.before.flow == slave_expectation.after_flow:
            raise ScheduleLinkagePreflightError(
                "slave boundary must change AutoFlow to prove its own schedule advanced",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_PAIR_EVIDENCE,
            )
        master_after = (
            master_expectation.after_mode,
            master_expectation.after_flow,
            master_expectation.after_frequency,
        )
        slave_after = (
            slave_expectation.after_mode,
            slave_expectation.after_flow,
            slave_expectation.after_frequency,
        )
        if slave_after == master_after:
            raise ScheduleLinkagePreflightError(
                "slave next Auto tuple must differ from master to prove its own schedule",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_PAIR_EVIDENCE,
            )
        if self._owned_staged_auto_transition_observation:
            self._assert_staged_auto_transition_preconditions(spec, states, snapshots)
        return snapshots

    def _assert_staged_auto_transition_preconditions(
        self,
        spec: ScheduleLinkageSpec,
        states: Mapping[str, DeviceState],
        snapshots: tuple[ScheduleLinkageSnapshot, ...],
    ) -> None:
        """Restrict clock-free observation to the owned two-entry field schedule."""

        if not self._refresh_sessions_before_critical_reads:
            raise ScheduleLinkagePreflightError(
                "staged Auto transition observation requires explicit critical reads",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_STAGED_PLAN,
            )
        after_valid_until = {snapshot.expectation.after_valid_until for snapshot in snapshots}
        if len(after_valid_until) != 1:
            raise ScheduleLinkagePreflightError(
                "staged Auto transition entries must share one validity window",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_STAGED_PLAN,
            )
        frequency_allowlist = {
            snapshot.frequency
            for snapshot in snapshots
        }
        for snapshot in snapshots:
            schedule = states[snapshot.device_id].schedule
            if schedule is None:
                raise ScheduleLinkagePreflightError(
                    f"device {snapshot.device_id!r} has no staged schedule",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_STAGED_PLAN,
                )
            entries = _validated_entries(schedule)
            expectation = snapshot.expectation
            if (
                len(entries) != 2
                or expectation.current_slot != entries[0].slot
                or expectation.next_slot != entries[1].slot
                or _wall_seconds(entries[1].end) <= _wall_seconds(entries[1].start)
            ):
                raise ScheduleLinkagePreflightError(
                    "Auto transition observation requires a non-wrapping two-entry schedule",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_STAGED_PLAN,
                )
            if (
                expectation.before.mode != "constant"
                or expectation.after_mode != "sine"
                or snapshot.mode != expectation.before.mode
                or entries[0].mode != "constant"
                or entries[0].parameters.get("frequency") != 0
            ):
                raise ScheduleLinkagePreflightError(
                    "staged Auto transition requires exact Constant(0) to Sine entries",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_STAGED_PLAN,
                )
            if expectation.before.mode == expectation.after_mode:
                raise ScheduleLinkagePreflightError(
                    "staged Auto transition must change mode at the observed boundary",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_STAGED_PLAN,
                )
            stable_budget = (
                spec.post_boundary_stability_seconds
                + 2 * spec.verification_interval_seconds
            )
            if (
                expectation.after_valid_until - expectation.boundary_at
            ).total_seconds() <= stable_budget:
                raise ScheduleLinkagePreflightError(
                    "staged next entry is too short for stable Auto evidence",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_STAGED_PLAN,
                )
            frequency_allowlist.add(expectation.before.frequency)
            if expectation.after_frequency is not None:
                frequency_allowlist.add(expectation.after_frequency)
        # Zero is an approved role-side effect only because both token-bound A entries above
        # proved the Pro wire's ignored Constant frequency byte is exactly zero.
        frequency_allowlist.add(0)
        self._staged_role_frequency_allowlist = frozenset(frequency_allowlist)

    def _snapshot_from_state(
        self,
        device: JebaoDevice,
        state: DeviceState,
        spec: ScheduleLinkageSpec,
    ) -> ScheduleLinkageSnapshot:
        self._assert_healthy(device.device_id, state)
        if state.enabled is not True:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} must be enabled before role-only testing",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CONTROL_BASELINE,
            )
        if state.linkage is not LinkageRole.INDEPENDENT:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} must start independent",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CONTROL_BASELINE,
            )
        if state.frequency is None:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} did not report manual frequency",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CONTROL_BASELINE,
            )
        binding = device.physical_binding
        if binding is None or binding.product_key != device.capabilities.product_key:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} has no exact physical binding",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CONTROL_BASELINE,
            )
        fingerprint = schedule_structure_fingerprint(state.schedule)
        if fingerprint is None:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} has no decoded schedule fingerprint",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_SCHEDULE_STRUCTURE,
            )
        plan, expectation = _expectation_from_state(device.device_id, state)
        remaining = plan.seconds_until_boundary
        if remaining < spec.minimum_lead_seconds:
            raise ScheduleLinkagePreflightError(
                "schedule boundary is too close for guarded role setup",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW,
            )
        if remaining > spec.observation_window_seconds:
            raise ScheduleLinkagePreflightError(
                "next schedule boundary is outside the observation window",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW,
            )
        required_window = (
            remaining
            + spec.post_boundary_stability_seconds
            + 2 * spec.ambiguous_band_seconds
            + 4 * spec.verification_interval_seconds
            + _ROLE_ONLY_ROLLBACK_RESERVE_SECONDS
        )
        if required_window > spec.observation_window_seconds:
            raise ScheduleLinkagePreflightError(
                "observation window lacks post-boundary verification and rollback reserve",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_TIME_WINDOW,
            )
        limits = device.capabilities.power_limits
        if not limits.min_power <= state.power <= limits.max_power:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} manual fallback Flow is outside limits",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_POWER_GUARD,
            )
        current_flow = expectation.before.flow
        if not limits.min_power <= current_flow <= limits.max_power:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} current effective AutoFlow is outside limits",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_POWER_GUARD,
            )
        if not limits.min_power <= expectation.after_flow <= limits.max_power:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} next AutoFlow is outside limits",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_POWER_GUARD,
            )
        guarded_maximum = min(limits.max_power, _SCHEDULE_LINKAGE_TEST_MAX_POWER)
        if state.power > guarded_maximum:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} manual fallback Flow exceeds "
                f"the guarded schedule-linkage maximum of {guarded_maximum}",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_POWER_GUARD,
            )
        for boundary_side, flow in (
            ("current", current_flow),
            ("next", expectation.after_flow),
        ):
            if flow > guarded_maximum:
                raise ScheduleLinkagePreflightError(
                    f"device {device.device_id!r} {boundary_side} AutoFlow exceeds "
                    f"the guarded schedule-linkage maximum of {guarded_maximum}",
                    failure=ScheduleLinkageRunFailure.PREFLIGHT_POWER_GUARD,
                )
        return ScheduleLinkageSnapshot(
            device_id=device.device_id,
            physical_binding=binding,
            enabled=state.enabled,
            power=state.power,
            mode=state.mode,
            frequency=state.frequency,
            timer_enabled=True,
            linkage=LinkageRole.INDEPENDENT,
            schedule_fingerprint=fingerprint,
            expectation=expectation,
        )

    async def _read_pair(self, spec: ScheduleLinkageSpec) -> dict[str, DeviceState]:
        result = await self._read_pair_states(spec)
        self._assert_pair_clock_skew(spec, result)
        return result

    async def _read_pair_states(self, spec: ScheduleLinkageSpec) -> dict[str, DeviceState]:
        """Read both states without folding clock validation into transport failure."""

        ids = (spec.master_device_id, spec.slave_device_id)
        states = await asyncio.gather(
            *(self._get_device(device_id).get_state() for device_id in ids)
        )
        return dict(zip(ids, states, strict=True))

    async def _read_pair_explicit_states(
        self,
        spec: ScheduleLinkageSpec,
    ) -> dict[str, DeviceState]:
        """Read correlated replies only when a driver can distinguish them from reports."""

        ids = (spec.master_device_id, spec.slave_device_id)
        read_tasks = tuple(
            asyncio.create_task(self._get_device(device_id).get_explicit_state())
            for device_id in ids
        )
        try:
            states = await asyncio.gather(*read_tasks)
        finally:
            for task in read_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*read_tasks, return_exceptions=True)
        return dict(zip(ids, states, strict=True))

    async def _heartbeat_pair(self, spec: ScheduleLinkageSpec) -> None:
        """Exchange paired read-only keepalives without leaving an uncertain peer running."""

        ids = (spec.master_device_id, spec.slave_device_id)
        heartbeat_tasks = tuple(
            asyncio.create_task(self._get_device(device_id).heartbeat())
            for device_id in ids
        )
        try:
            await asyncio.gather(*heartbeat_tasks)
        finally:
            for task in heartbeat_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*heartbeat_tasks, return_exceptions=True)

    async def _read_pair_explicit_states_guarded(
        self,
        spec: ScheduleLinkageSpec,
        *,
        context: Literal[
            "first-write gate",
            "post-role verification",
            "frequency convergence",
            "active observation",
        ] = "frequency convergence",
    ) -> dict[str, DeviceState]:
        """Cancel a reply-only read promptly if stop or safety authority changes."""

        stop_event = self._stop_event
        if stop_event is None:
            raise ScheduleLinkageApplyError("schedule-linkage stop authority is unavailable")
        read_task = asyncio.create_task(self._read_pair_explicit_states(spec))
        stop_task = asyncio.create_task(stop_event.wait())
        safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        tasks = (read_task, stop_task, safety_task)
        try:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                self._set_pair_verification_checkpoint()
                raise ScheduleLinkageApplyError(
                    f"schedule-linkage stop was requested during {context}"
                )
            if safety_task in done:
                self._set_pair_verification_checkpoint()
                raise ScheduleLinkageApplyError(
                    "schedule-linkage safety authority was revoked"
                )
            return read_task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _read_pair_heartbeat_fenced_states_guarded(
        self,
        spec: ScheduleLinkageSpec,
    ) -> dict[str, DeviceState]:
        """Acquire paired heartbeat-fenced states under stop and safety authority.

        A role-active GAgent may interleave, or return, the complete state produced by a
        0x90/0x02 query as an action-0x04 report rather than the action-0x03 reply commonly seen
        in independent mode. Each device driver owns one atomic heartbeat/read operation so no
        other request or session replacement can cross the fence. This relaxed read is
        deliberately monitor-only; capture, first-write, post-role and restore evidence remain
        reply-only.
        """

        stop_event = self._stop_event
        if stop_event is None:
            raise ScheduleLinkageApplyError("schedule-linkage stop authority is unavailable")
        ids = (spec.master_device_id, spec.slave_device_id)
        state_tasks = tuple(
            asyncio.create_task(
                self._get_device(device_id).get_heartbeat_fenced_state()
            )
            for device_id in ids
        )

        async def read_pair() -> dict[str, DeviceState]:
            try:
                states = await asyncio.gather(*state_tasks)
            finally:
                for task in state_tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*state_tasks, return_exceptions=True)
            return dict(zip(ids, states, strict=True))

        read_task = asyncio.create_task(read_pair())
        stop_task = asyncio.create_task(stop_event.wait())
        safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        tasks = (read_task, stop_task, safety_task)
        try:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                raise ScheduleLinkageApplyError(
                    "schedule-linkage stop was requested during active observation"
                )
            if safety_task in done:
                raise ScheduleLinkageApplyError(
                    "schedule-linkage safety authority was revoked"
                )
            return read_task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _read_critical_pair_states(
        self,
        spec: ScheduleLinkageSpec,
        *,
        context: Literal["first-write gate", "post-role verification"],
    ) -> dict[str, DeviceState]:
        """Use reply-only reads only when the caller has opened fresh sessions."""

        if not self._refresh_sessions_before_critical_reads:
            return await self._read_pair_states(spec)
        return await self._read_pair_explicit_states_guarded(spec, context=context)

    async def _assert_first_write_gate(
        self,
        record: ScheduleLinkageRecord,
    ) -> _ClockAnchor:
        await self._refresh_pair_sessions_if_enabled(record.spec)
        states = await self._read_critical_pair_states(
            record.spec,
            context="first-write gate",
        )
        self._assert_pair_clock_skew(record.spec, states)
        sampled_at = self._monotonic()
        self._assert_observation_deadline(sampled_at)
        fresh = self._snapshots_from_states(record.spec, states)
        self._authorize(record.spec, fresh)
        self._assert_observation_deadline()
        if fresh != record.snapshots:
            raise ScheduleLinkagePreflightError(
                "schedule boundary/evidence changed before the first role write"
            )
        remaining = min(
            (
                snapshot.expectation.boundary_at
                - self._state_clock_for(snapshot.device_id, states[snapshot.device_id])
            ).total_seconds()
            for snapshot in fresh
        )
        if remaining < record.spec.minimum_lead_seconds:
            raise ScheduleLinkagePreflightError("insufficient lead remains before the boundary")
        if self._owned_staged_auto_transition_observation:
            required_early_guard = (
                _STAGED_CLOCK_STALENESS_ALLOWANCE_SECONDS
                + 2 * record.spec.verification_interval_seconds
            )
            if remaining <= required_early_guard:
                raise ScheduleLinkagePreflightError(
                    "insufficient lead remains for staged Auto transition attribution"
                )
            self._staged_transition_not_before = (
                sampled_at
                + remaining
                - _STAGED_CLOCK_STALENESS_ALLOWANCE_SECONDS
            )
        post_write_margin = (
            record.spec.ambiguous_band_seconds
            + 3 * record.spec.verification_interval_seconds
        )
        self._forward_deadline = (
            min(
                sampled_at + remaining - post_write_margin,
                self._require_observation_deadline(),
            )
        )
        return self._clock_anchor(states, sampled_at)

    async def _link_device(
        self,
        record: ScheduleLinkageRecord,
        device_id: str,
        role: LinkageRole,
        previous_anchor: _ClockAnchor,
    ) -> tuple[ScheduleLinkageRecord, _ClockAnchor]:
        if role is LinkageRole.MASTER:
            intent_failure = ScheduleLinkageRunFailure.MASTER_INTENT
            intent_started = ScheduleLinkageRunProgressKind.MASTER_INTENT_STARTED
            intent_persisted = ScheduleLinkageRunProgressKind.MASTER_INTENT_PERSISTED
            adapter_failure = ScheduleLinkageRunFailure.MASTER_ADAPTER_WRITE
            adapter_started = (
                ScheduleLinkageRunProgressKind.MASTER_ADAPTER_WRITE_STARTED
            )
            adapter_completed = (
                ScheduleLinkageRunProgressKind.MASTER_ADAPTER_WRITE_COMPLETED
            )
            pair_failure = ScheduleLinkageRunFailure.MASTER_PAIR_VERIFICATION
            pair_started = (
                ScheduleLinkageRunProgressKind.MASTER_PAIR_VERIFICATION_STARTED
            )
            pair_read_retry_started = None
            pair_verified = ScheduleLinkageRunProgressKind.MASTER_PAIR_VERIFIED
        else:
            intent_failure = ScheduleLinkageRunFailure.SLAVE_INTENT
            intent_started = ScheduleLinkageRunProgressKind.SLAVE_INTENT_STARTED
            intent_persisted = ScheduleLinkageRunProgressKind.SLAVE_INTENT_PERSISTED
            adapter_failure = ScheduleLinkageRunFailure.SLAVE_ADAPTER_WRITE
            adapter_started = ScheduleLinkageRunProgressKind.SLAVE_ADAPTER_WRITE_STARTED
            adapter_completed = (
                ScheduleLinkageRunProgressKind.SLAVE_ADAPTER_WRITE_COMPLETED
            )
            pair_failure = ScheduleLinkageRunFailure.SLAVE_PAIR_VERIFICATION
            pair_started = (
                ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFICATION_STARTED
            )
            pair_read_retry_started = (
                ScheduleLinkageRunProgressKind.SLAVE_PAIR_STATE_READ_RETRY_STARTED
            )
            pair_verified = ScheduleLinkageRunProgressKind.SLAVE_PAIR_VERIFIED
        self._run_failure = intent_failure
        self._emit_progress_best_effort(intent_started)
        intents = (*record.linkage_write_intent_device_ids, device_id)
        record = record.model_copy(
            update={
                "linkage_write_intent_device_ids": intents,
                "updated_at": self._record_now(record),
            }
        )
        self._store.save(record)
        self._emit_progress_best_effort(intent_persisted)
        self._run_failure = adapter_failure
        self._emit_progress_best_effort(adapter_started)
        await self._get_device(device_id).write_linkage(role, guard=self._forward_write_allowed)
        self._emit_progress_best_effort(adapter_completed)
        self._run_failure = pair_failure
        self._run_pair_participant = "master" if role is LinkageRole.MASTER else "slave"
        self._emit_progress_best_effort(pair_started)
        self._set_pair_verification_failure("session_refresh")
        await self._refresh_pair_sessions_if_enabled(record.spec)
        self._set_pair_verification_failure("state_read")
        states = await self._read_post_role_pair_states(
            record,
            retry_progress=pair_read_retry_started,
            pair_failure=pair_failure,
        )
        if not self._owned_staged_auto_transition_observation:
            self._set_pair_verification_failure("clock_skew")
            self._assert_pair_clock_skew(record.spec, states)
        sampled_at = self._monotonic()
        self._set_pair_verification_failure("deadline")
        self._assert_observation_deadline(sampled_at)
        self._assert_staged_pre_boundary_sample_time(sampled_at)
        if not self._owned_staged_auto_transition_observation:
            self._set_pair_verification_failure("clock_continuity")
            self._assert_clock_continuity(
                record.spec,
                states,
                previous_clocks=previous_anchor.clocks,
                elapsed_monotonic=sampled_at - previous_anchor.sampled_at_monotonic,
            )
        expected_roles = {
            record.spec.master_device_id: (
                LinkageRole.MASTER
                if record.spec.master_device_id in intents
                else LinkageRole.INDEPENDENT
            ),
            record.spec.slave_device_id: (
                LinkageRole.ASYNC_SLAVE
                if record.spec.slave_device_id in intents
                else LinkageRole.INDEPENDENT
            ),
        }
        self._set_pair_verification_checkpoint()
        try:
            self._assert_pair_sample(record, states, expected_roles, phase="before")
        except ScheduleLinkageApplyError as error:
            if not self._frequency_only_pair_state_failure():
                raise
            states, sampled_at = await self._converge_role_frequency(
                record,
                expected_roles,
                initial_states=states,
                initial_anchor=(
                    previous_anchor
                    if self._owned_staged_auto_transition_observation
                    else self._clock_anchor(states, sampled_at)
                ),
                initial_error=error,
                pair_failure=pair_failure,
            )
        self._assert_staged_pre_boundary_sample_time(sampled_at)
        self._run_failure = pair_failure
        self._run_drift_dimensions = ()
        linked = (*record.linked_device_ids, device_id)
        record = record.model_copy(
            update={"linked_device_ids": linked, "updated_at": self._record_now(record)}
        )
        self._store.save(record)
        self._emit_progress_best_effort(pair_verified)
        self._run_pair_participant = None
        next_anchor = (
            previous_anchor
            if self._owned_staged_auto_transition_observation
            else self._clock_anchor(states, sampled_at)
        )
        return record, next_anchor

    async def _read_post_role_pair_states(
        self,
        record: ScheduleLinkageRecord,
        *,
        retry_progress: ScheduleLinkageRunProgressKind | None,
        pair_failure: ScheduleLinkageRunFailure,
    ) -> dict[str, DeviceState]:
        """Retry one owned staged slave pair read without repeating its role write."""

        try:
            return await self._read_critical_pair_states(
                record.spec,
                context="post-role verification",
            )
        except asyncio.CancelledError:
            raise
        except ScheduleLinkageError:
            raise
        except Exception as error:
            if (
                not self._owned_staged_auto_transition_observation
                or retry_progress is None
                or self._run_pair_participant != "slave"
                or not _is_retryable_transport_failure(error)
            ):
                raise

        self._assert_staged_slave_read_retry_record(record, pair_failure)

        self._emit_progress_best_effort(retry_progress)
        await self._wait_for_staged_slave_read_retry(pair_failure)
        self._assert_staged_slave_read_retry_record(record, pair_failure)
        self._set_pair_verification_failure("session_refresh")
        await self._refresh_pair_sessions_uninterruptibly(record.spec)
        self._set_pair_verification_checkpoint()
        self._validate_recovery_bindings(record)
        self._assert_staged_slave_read_retry_authority(
            pair_failure,
            minimum_remaining_seconds=0,
        )
        self._assert_staged_slave_read_retry_record(record, pair_failure)
        self._set_pair_verification_failure("state_read")
        states = await self._read_critical_pair_states(
            record.spec,
            context="post-role verification",
        )
        self._assert_staged_slave_read_retry_authority(
            pair_failure,
            minimum_remaining_seconds=0,
        )
        self._assert_staged_slave_read_retry_record(record, pair_failure)
        return states

    def _assert_staged_slave_read_retry_record(
        self,
        record: ScheduleLinkageRecord,
        pair_failure: ScheduleLinkageRunFailure,
    ) -> None:
        """Require the exact leased post-slave-write journal before each retry phase."""

        expected_ids = (
            record.spec.master_device_id,
            record.spec.slave_device_id,
        )
        try:
            durable = self._store.confirms_lease_successor(record)
        except BaseException:
            durable = False
        if (
            not durable
            or record.phase is not ScheduleLinkagePhase.APPLYING
            or record.linkage_write_intent_device_ids != expected_ids
            or record.linked_device_ids != expected_ids[:1]
            or record.detached_device_ids
        ):
            self._run_failure = pair_failure
            self._run_drift_dimensions = ()
            raise ScheduleLinkageApplyError(
                "staged slave read retry lacks the exact durable role intent"
            )

    async def _wait_for_staged_slave_read_retry(
        self,
        pair_failure: ScheduleLinkageRunFailure,
    ) -> None:
        retry_delay = _STAGED_TRANSPORT_RETRY_DELAY_SECONDS
        self._assert_staged_slave_read_retry_authority(
            pair_failure,
            minimum_remaining_seconds=(
                retry_delay + _ROLE_FREQUENCY_FRESH_READ_ADMISSION_SECONDS
            ),
        )
        stop_event = self._stop_event
        if stop_event is None:
            self._run_failure = pair_failure
            raise ScheduleLinkageApplyError(
                "staged slave read retry has no stop authority"
            )
        settle_task = asyncio.ensure_future(self._sleep(retry_delay))
        stop_task = asyncio.create_task(stop_event.wait())
        safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        tasks = (settle_task, stop_task, safety_task)
        try:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                self._run_failure = pair_failure
                raise ScheduleLinkageApplyError(
                    "schedule-linkage stop was requested during staged slave read retry"
                )
            if safety_task in done:
                self._run_failure = pair_failure
                raise ScheduleLinkageApplyError(
                    "schedule-linkage safety authority was revoked during staged slave read retry"
                )
            settle_task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self._assert_staged_slave_read_retry_authority(
            pair_failure,
            minimum_remaining_seconds=_ROLE_FREQUENCY_FRESH_READ_ADMISSION_SECONDS,
        )

    def _assert_staged_slave_read_retry_authority(
        self,
        pair_failure: ScheduleLinkageRunFailure,
        *,
        minimum_remaining_seconds: float,
    ) -> None:
        self._run_failure = pair_failure
        self._run_drift_dimensions = ()
        if self._stop_requested():
            raise ScheduleLinkageApplyError(
                "schedule-linkage stop was requested during staged slave read retry"
            )
        if (
            self._safety_epoch is None
            or not self._safety_interlock.permitted
            or self._safety_interlock.epoch != self._safety_epoch
        ):
            raise ScheduleLinkageApplyError(
                "schedule-linkage safety authority was revoked during staged slave read retry"
            )
        forward_deadline = self._forward_deadline
        transition_not_before = self._staged_transition_not_before
        if forward_deadline is None or transition_not_before is None:
            self._set_pair_verification_checkpoint()
            raise ScheduleLinkageApplyError(
                "staged slave read retry has no authorized pre-boundary window"
            )
        remaining = min(
            self._require_observation_deadline(),
            forward_deadline,
            transition_not_before,
        ) - self._monotonic()
        if remaining <= minimum_remaining_seconds:
            self._set_pair_verification_failure("deadline")
            raise ScheduleLinkageApplyError(
                "staged slave read retry lacks a complete pre-boundary session budget"
            )
        self._run_failure = pair_failure
        self._run_drift_dimensions = ()

    def _assert_staged_pre_boundary_sample_time(self, sampled_at: float) -> None:
        if not self._owned_staged_auto_transition_observation:
            return
        transition_not_before = self._staged_transition_not_before
        if transition_not_before is None:
            self._set_pair_verification_checkpoint()
            raise ScheduleLinkageApplyError(
                "staged role verification has no authorized boundary window"
            )
        if sampled_at >= transition_not_before:
            self._set_pair_verification_checkpoint()
            raise ScheduleLinkageApplyError(
                "staged role verification exceeded the conservative boundary window"
            )

    async def _converge_role_frequency(
        self,
        record: ScheduleLinkageRecord,
        expected_roles: Mapping[str, LinkageRole],
        *,
        initial_states: Mapping[str, DeviceState],
        initial_anchor: _ClockAnchor,
        initial_error: ScheduleLinkageApplyError,
        pair_failure: ScheduleLinkageRunFailure,
    ) -> tuple[dict[str, DeviceState], float]:
        """Require two exact fresh reads after a frequency-only role transition report."""

        spec = record.spec
        convergence_deadline = min(
            self._monotonic() + _ROLE_FREQUENCY_CONVERGENCE_ADMISSION_WINDOW_SECONDS,
            self._require_observation_deadline(),
        )
        if self._owned_staged_auto_transition_observation:
            transition_not_before = self._staged_transition_not_before
            if transition_not_before is None:
                self._set_pair_verification_checkpoint()
                raise ScheduleLinkageApplyError(
                    "staged role convergence has no authorized boundary window"
                )
            convergence_deadline = min(
                convergence_deadline,
                transition_not_before,
            )
        interval = min(
            spec.verification_interval_seconds,
            _ROLE_FREQUENCY_CONVERGENCE_MAX_INTERVAL_SECONDS,
        )
        exact_reads = 0
        alternate_frequency: int | None = None
        alternate_exact_reads = 0
        previous_anchor = initial_anchor
        last_frequency_error = initial_error
        last_frequency_failure = self._run_failure
        if self._owned_staged_auto_transition_observation:
            # The first mismatch authorizes no alternate baseline.  It only proves that the
            # controller should open fresh authenticated sessions.  Reject values outside the
            # token-bound Constant/Sine plan before spending the convergence budget.
            self._staged_slave_frequency_candidate(
                record,
                initial_states,
                expected_roles,
            )
        for _attempt in range(_ROLE_FREQUENCY_CONVERGENCE_MAX_READS):
            settled = await self._wait_for_role_frequency_settle(
                interval,
                convergence_deadline=convergence_deadline,
                pair_failure=pair_failure,
            )
            if not settled:
                break
            self._set_pair_verification_failure("session_refresh")
            await self._refresh_pair_sessions_uninterruptibly(spec)
            self._validate_recovery_bindings(record)
            self._assert_role_frequency_retry_authority(pair_failure)
            self._set_pair_verification_failure("state_read")
            states = await self._read_pair_explicit_states_guarded(spec)
            if not self._owned_staged_auto_transition_observation:
                self._set_pair_verification_failure("clock_skew")
                self._assert_pair_clock_skew(spec, states)
            sampled_at = self._monotonic()
            self._set_pair_verification_failure("deadline")
            self._assert_observation_deadline(sampled_at)
            self._assert_staged_pre_boundary_sample_time(sampled_at)
            if not self._owned_staged_auto_transition_observation:
                self._set_pair_verification_failure("clock_continuity")
                self._assert_clock_continuity(
                    spec,
                    states,
                    previous_clocks=previous_anchor.clocks,
                    elapsed_monotonic=(
                        sampled_at - previous_anchor.sampled_at_monotonic
                    ),
                )
            self._assert_role_frequency_retry_authority(pair_failure)
            convergence_expired = sampled_at > convergence_deadline
            self._set_pair_verification_checkpoint()
            try:
                self._assert_pair_sample(
                    record,
                    states,
                    expected_roles,
                    phase="before",
                    emit_sample=False,
                )
            except ScheduleLinkageApplyError as error:
                if not self._frequency_only_pair_state_failure():
                    raise
                exact_reads = 0
                last_frequency_error = error
                last_frequency_failure = self._run_failure
                if self._owned_staged_auto_transition_observation:
                    candidate = self._staged_slave_frequency_candidate(
                        record,
                        states,
                        expected_roles,
                    )
                    if candidate == alternate_frequency:
                        alternate_exact_reads += 1
                    else:
                        alternate_frequency = candidate
                        alternate_exact_reads = 1
                    if (
                        not convergence_expired
                        and alternate_exact_reads
                        >= _ROLE_FREQUENCY_CONVERGENCE_REQUIRED_EXACT_READS
                    ):
                        slave_id = record.spec.slave_device_id
                        self._staged_role_frequency_pins[slave_id] = candidate
                        try:
                            self._assert_pair_sample(
                                record,
                                states,
                                expected_roles,
                                phase="before",
                                emit_sample=True,
                            )
                        except BaseException:
                            self._staged_role_frequency_pins.pop(slave_id, None)
                            raise
                        return states, sampled_at
            else:
                alternate_frequency = None
                alternate_exact_reads = 0
                exact_reads += 1
                if (
                    not convergence_expired
                    and exact_reads
                    >= _ROLE_FREQUENCY_CONVERGENCE_REQUIRED_EXACT_READS
                ):
                    # The retry reads are diagnostic until the pair has converged twice in a
                    # row.  Reuse the final immutable states to publish exactly one
                    # evidence sample; this must not open another session or device read.
                    self._assert_pair_sample(
                        record,
                        states,
                        expected_roles,
                        phase="before",
                        emit_sample=True,
                    )
                    return states, sampled_at
            if not self._owned_staged_auto_transition_observation:
                previous_anchor = self._clock_anchor(states, sampled_at)
            if convergence_expired:
                break
        self._run_failure = last_frequency_failure
        self._run_drift_dimensions = (ScheduleLinkageDriftDimension.FREQUENCY,)
        raise last_frequency_error

    def _staged_slave_frequency_candidate(
        self,
        record: ScheduleLinkageRecord,
        states: Mapping[str, DeviceState],
        expected_roles: Mapping[str, LinkageRole],
    ) -> int:
        """Return one allow-listed ASYNC side effect or fail closed without another write."""

        master_id = record.spec.master_device_id
        slave_id = record.spec.slave_device_id
        if (
            not self._owned_staged_auto_transition_observation
            or self._run_failure is not ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE
            or self._run_drift_dimensions
            != (ScheduleLinkageDriftDimension.FREQUENCY,)
            or expected_roles.get(master_id) is not LinkageRole.MASTER
            or expected_roles.get(slave_id) is not LinkageRole.ASYNC_SLAVE
        ):
            raise ScheduleLinkageApplyError(
                "role-induced frequency evidence is outside the owned staged slave scope"
            )
        candidate = states[slave_id].frequency
        snapshot = self._snapshot(record, slave_id)
        if (
            isinstance(candidate, bool)
            or not isinstance(candidate, int)
            or candidate == snapshot.frequency
            or candidate not in self._staged_role_frequency_allowlist
        ):
            raise ScheduleLinkageApplyError(
                "role-induced slave frequency is outside the confirmed staged plan"
            )
        return candidate

    async def _wait_for_role_frequency_settle(
        self,
        interval: float,
        *,
        convergence_deadline: float,
        pair_failure: ScheduleLinkageRunFailure,
    ) -> bool:
        self._assert_role_frequency_retry_authority(pair_failure)
        now = self._monotonic()
        convergence_remaining = convergence_deadline - now
        observation_remaining = self._require_observation_deadline() - now
        settle_seconds = min(interval, convergence_remaining)
        if (
            settle_seconds <= 0
            or observation_remaining
            < settle_seconds + _ROLE_FREQUENCY_FRESH_READ_ADMISSION_SECONDS
        ):
            return False
        stop_event = self._stop_event
        if stop_event is None:
            raise ScheduleLinkageApplyError("schedule-linkage stop authority is unavailable")
        settle_task = asyncio.create_task(self._sleep(settle_seconds))
        stop_task = asyncio.create_task(stop_event.wait())
        safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        tasks = (settle_task, stop_task, safety_task)
        try:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                raise ScheduleLinkageApplyError(
                    "schedule-linkage stop was requested during frequency convergence"
                )
            if safety_task in done:
                raise ScheduleLinkageApplyError(
                    "schedule-linkage safety authority was revoked"
                )
            settle_task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self._assert_role_frequency_retry_authority(pair_failure)
        now = self._monotonic()
        return (
            now <= convergence_deadline
            and self._require_observation_deadline() - now
            >= _ROLE_FREQUENCY_FRESH_READ_ADMISSION_SECONDS
        )

    def _assert_role_frequency_retry_authority(
        self,
        pair_failure: ScheduleLinkageRunFailure | None,
    ) -> None:
        self._run_failure = pair_failure
        self._run_drift_dimensions = ()
        if self._stop_requested():
            raise ScheduleLinkageApplyError(
                "schedule-linkage stop was requested during frequency convergence"
            )
        self._set_pair_verification_failure("deadline")
        self._assert_observation_deadline()
        if not self._active_observation_allowed():
            self._run_failure = pair_failure
            raise ScheduleLinkageApplyError("schedule-linkage safety authority was revoked")
        self._run_failure = pair_failure
        self._run_drift_dimensions = ()

    def _frequency_only_pair_state_failure(self) -> bool:
        return self._run_failure in {
            ScheduleLinkageRunFailure.MASTER_PAIR_STATE,
            ScheduleLinkageRunFailure.MASTER_PAIR_MASTER_STATE,
            ScheduleLinkageRunFailure.MASTER_PAIR_SLAVE_STATE,
            ScheduleLinkageRunFailure.SLAVE_PAIR_STATE,
            ScheduleLinkageRunFailure.SLAVE_PAIR_MASTER_STATE,
            ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE,
        } and self._run_drift_dimensions == (
            ScheduleLinkageDriftDimension.FREQUENCY,
        )

    async def _monitor_boundary(
        self,
        record: ScheduleLinkageRecord,
        activation_anchor: _ClockAnchor,
    ) -> tuple[ScheduleLinkageStopReason, bool]:
        spec = record.spec
        if self._owned_staged_auto_transition_observation:
            return await self._monitor_staged_auto_transition(record)
        expected_roles = {
            spec.master_device_id: LinkageRole.MASTER,
            spec.slave_device_id: LinkageRole.ASYNC_SLAVE,
        }
        initial_states = await self._read_pair(spec)
        initial_sampled_at = self._monotonic()
        self._assert_observation_deadline(initial_sampled_at)
        self._assert_clock_continuity(
            spec,
            initial_states,
            previous_clocks=activation_anchor.clocks,
            elapsed_monotonic=(
                initial_sampled_at - activation_anchor.sampled_at_monotonic
            ),
        )
        boundary_remaining = min(
            (
                snapshot.expectation.boundary_at
                - self._state_clock_for(snapshot.device_id, initial_states[snapshot.device_id])
            ).total_seconds()
            for snapshot in record.snapshots
        )
        previous_clocks = {
            device_id: self._state_clock_for(device_id, state)
            for device_id, state in initial_states.items()
        }
        previous_monotonic = initial_sampled_at
        deadline = previous_monotonic + max(
            0.0, boundary_remaining
        ) + (
            spec.post_boundary_stability_seconds
            + 2 * spec.ambiguous_band_seconds
            + 4 * spec.verification_interval_seconds
        )
        deadline = min(deadline, self._require_observation_deadline())
        consecutive_after = 0
        previous_after: _StablePairEvidence | None = None
        stable_after_started_at: float | None = None
        while self._monotonic() <= deadline:
            if self._stop_requested():
                return ScheduleLinkageStopReason.MANUAL, False
            if not self._active_observation_allowed():
                raise ScheduleLinkageApplyError("schedule-linkage safety authority was revoked")
            states = await self._read_pair(spec)
            sampled_at = self._monotonic()
            self._assert_observation_deadline(sampled_at)
            self._assert_clock_continuity(
                spec,
                states,
                previous_clocks=previous_clocks,
                elapsed_monotonic=sampled_at - previous_monotonic,
            )
            previous_clocks = {
                device_id: self._state_clock_for(device_id, state)
                for device_id, state in states.items()
            }
            previous_monotonic = sampled_at
            positions = tuple(
                (
                    self._state_clock_for(snapshot.device_id, states[snapshot.device_id])
                    - snapshot.expectation.boundary_at
                ).total_seconds()
                for snapshot in record.snapshots
            )
            if any(abs(position) <= spec.ambiguous_band_seconds for position in positions):
                self._assert_pair_sample(record, states, expected_roles, phase="ambiguous")
                consecutive_after = 0
                previous_after = None
                stable_after_started_at = None
            elif all(position < -spec.ambiguous_band_seconds for position in positions):
                self._assert_pair_sample(record, states, expected_roles, phase="before")
                consecutive_after = 0
                previous_after = None
                stable_after_started_at = None
            elif all(position > spec.ambiguous_band_seconds for position in positions):
                self._assert_after_within_immediate_slot(record, states)
                evidence = self._assert_pair_sample(
                    record,
                    states,
                    expected_roles,
                    phase="after",
                )
                if previous_after is not None and evidence == previous_after:
                    consecutive_after += 1
                else:
                    consecutive_after = 1
                    previous_after = evidence
                    stable_after_started_at = sampled_at
                stable_for = (
                    0.0
                    if stable_after_started_at is None
                    else sampled_at - stable_after_started_at
                )
                if (
                    consecutive_after >= 2
                    and stable_for >= spec.post_boundary_stability_seconds
                ):
                    self._assert_after_within_immediate_slot(record, states)
                    self._assert_observation_deadline()
                    return ScheduleLinkageStopReason.BOUNDARY_VERIFIED, True
            else:
                # Device clocks straddling the band cannot prove one coherent transition.
                self._assert_pair_sample(record, states, expected_roles, phase="ambiguous")
                consecutive_after = 0
                previous_after = None
                stable_after_started_at = None
            await self._sleep(spec.verification_interval_seconds)
        raise ScheduleLinkageApplyError(
            "schedule boundary was missed or lacked two consecutive fresh samples"
        )

    async def _acquire_staged_monitor_pair(
        self,
        record: ScheduleLinkageRecord,
    ) -> tuple[dict[str, DeviceState], float]:
        """Heartbeat-fence and read one staged pair without changing device state."""

        self._run_failure = ScheduleLinkageRunFailure.MONITOR_HEARTBEAT
        heartbeat_started_at = self._monotonic()
        try:
            states = await self._read_pair_heartbeat_fenced_states_guarded(record.spec)
        except HeartbeatFencedStateError as error:
            self._run_failure = (
                ScheduleLinkageRunFailure.MONITOR_HEARTBEAT
                if error.stage is HeartbeatFencedStateStage.HEARTBEAT
                else ScheduleLinkageRunFailure.MONITOR_STATE_READ
            )
            raise
        try:
            self._assert_observation_deadline()
        except ScheduleLinkageApplyError:
            self._run_failure = ScheduleLinkageRunFailure.MONITOR_DEADLINE
            raise
        return states, heartbeat_started_at

    async def _acquire_staged_monitor_pair_with_retry(
        self,
        record: ScheduleLinkageRecord,
    ) -> tuple[dict[str, DeviceState], float, bool]:
        """Retry one transport-only acquisition on a fresh, exact paired session."""

        try:
            states, heartbeat_started_at = await self._acquire_staged_monitor_pair(record)
            return states, heartbeat_started_at, False
        except asyncio.CancelledError:
            raise
        except ScheduleLinkageError:
            raise
        except Exception as error:
            if not _is_retryable_transport_failure(error):
                raise

        self._assert_staged_monitor_retry_record(record)
        self._validate_recovery_bindings(record, permit_disconnected=True)
        self._assert_staged_monitor_retry_authority(
            minimum_remaining_seconds=(
                _STAGED_TRANSPORT_RETRY_DELAY_SECONDS
                + _ROLE_FREQUENCY_FRESH_READ_ADMISSION_SECONDS
            )
        )
        self._emit_progress_best_effort(
            ScheduleLinkageRunProgressKind.MONITOR_TRANSPORT_RETRY_STARTED
        )
        await self._wait_for_staged_monitor_retry()
        self._assert_staged_monitor_retry_record(record)
        self._run_failure = ScheduleLinkageRunFailure.MONITOR_SESSION_REFRESH
        await self._refresh_pair_sessions_uninterruptibly(record.spec)
        self._validate_recovery_bindings(record)
        self._assert_staged_monitor_retry_record(record)
        self._assert_staged_monitor_retry_authority(minimum_remaining_seconds=0)
        states, heartbeat_started_at = await self._acquire_staged_monitor_pair(record)
        return states, heartbeat_started_at, True

    def _assert_staged_monitor_retry_record(
        self,
        record: ScheduleLinkageRecord,
    ) -> None:
        """Require the exact durable ACTIVE pair before every monitor recovery phase."""

        expected_ids = (
            record.spec.master_device_id,
            record.spec.slave_device_id,
        )
        try:
            durable = self._store.confirms_lease_successor(record)
        except BaseException:
            durable = False
        if (
            not durable
            or record.phase is not ScheduleLinkagePhase.ACTIVE
            or record.linkage_write_intent_device_ids != expected_ids
            or record.linked_device_ids != expected_ids
            or record.detached_device_ids
        ):
            self._run_failure = ScheduleLinkageRunFailure.MONITOR
            self._run_drift_dimensions = ()
            raise ScheduleLinkageApplyError(
                "staged monitor retry lacks the exact durable active role pair"
            )

    async def _wait_for_staged_monitor_retry(self) -> None:
        """Wait the bounded transport settle while stop and safety remain authoritative."""

        stop_event = self._stop_event
        if stop_event is None:
            self._run_failure = ScheduleLinkageRunFailure.MONITOR
            raise ScheduleLinkageApplyError(
                "staged monitor retry has no stop authority"
            )
        settle_task = asyncio.ensure_future(
            self._sleep(_STAGED_TRANSPORT_RETRY_DELAY_SECONDS)
        )
        stop_task = asyncio.create_task(stop_event.wait())
        safety_task = asyncio.create_task(self._safety_interlock.wait_until_blocked())
        tasks = (settle_task, stop_task, safety_task)
        try:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                self._run_failure = ScheduleLinkageRunFailure.MONITOR
                raise ScheduleLinkageApplyError(
                    "schedule-linkage stop was requested during staged monitor retry"
                )
            if safety_task in done:
                self._run_failure = ScheduleLinkageRunFailure.MONITOR
                raise ScheduleLinkageApplyError(
                    "schedule-linkage safety authority was revoked during staged monitor retry"
                )
            settle_task.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        self._assert_staged_monitor_retry_authority(
            minimum_remaining_seconds=_ROLE_FREQUENCY_FRESH_READ_ADMISSION_SECONDS
        )

    def _assert_staged_monitor_retry_authority(
        self,
        *,
        minimum_remaining_seconds: float,
    ) -> None:
        """Keep a monitor reconnect inside the same stop, safety and deadline authority."""

        self._run_drift_dimensions = ()
        if self._stop_requested():
            self._run_failure = ScheduleLinkageRunFailure.MONITOR
            raise ScheduleLinkageApplyError(
                "schedule-linkage stop was requested during staged monitor retry"
            )
        if (
            self._safety_epoch is None
            or not self._safety_interlock.permitted
            or self._safety_interlock.epoch != self._safety_epoch
        ):
            self._run_failure = ScheduleLinkageRunFailure.MONITOR
            raise ScheduleLinkageApplyError(
                "schedule-linkage safety authority was revoked during staged monitor retry"
            )
        remaining = self._require_observation_deadline() - self._monotonic()
        if remaining < minimum_remaining_seconds:
            self._run_failure = ScheduleLinkageRunFailure.MONITOR_DEADLINE
            raise ScheduleLinkageApplyError(
                "staged monitor retry lacks a complete read-only session budget"
            )

    async def _monitor_staged_auto_transition(
        self,
        record: ScheduleLinkageRecord,
    ) -> tuple[ScheduleLinkageStopReason, bool]:
        """Observe the complete attended epoch without trusting the batched NowTime DP.

        Once the owned field schedule and roles are active, measurement mismatches and transient
        read failures are evidence, not a reason to erase the experiment early.  Only stop,
        revoked safety authority, an unsafe active Flow, or exhausted observation authority may
        end the epoch before its fixed deadline.
        """

        spec = record.spec
        expected_roles = {
            spec.master_device_id: LinkageRole.MASTER,
            spec.slave_device_id: LinkageRole.ASYNC_SLAVE,
        }
        auto_after_seen: set[str] = set()
        b_fields_seen: dict[
            str,
            set[_StagedTransitionField],
        ] = {device_id: set() for device_id in expected_roles}
        partial_started_at: dict[str, float | None] = {
            device_id: None for device_id in expected_roles
        }
        partial_stalled_count = {device_id: 0 for device_id in expected_roles}
        consecutive_after = 0
        previous_after: _StablePairEvidence | None = None
        stable_after_started_at: float | None = None
        transition_not_before = self._staged_transition_not_before
        if transition_not_before is None:
            raise ScheduleLinkageApplyError(
                "staged Auto transition has no authorized monotonic boundary window"
            )
        transition_verified = False
        contradictory_after_verification = False
        diagnostic_issue_count = 0

        def reset_candidate_tracking() -> None:
            nonlocal consecutive_after, previous_after, stable_after_started_at
            auto_after_seen.clear()
            for fields in b_fields_seen.values():
                fields.clear()
            for device_id in expected_roles:
                partial_started_at[device_id] = None
                partial_stalled_count[device_id] = 0
            consecutive_after = 0
            previous_after = None
            stable_after_started_at = None

        def record_diagnostic_issue(
            reason: str,
            *,
            contradictory: bool = False,
        ) -> None:
            nonlocal diagnostic_issue_count, contradictory_after_verification
            diagnostic_issue_count += 1
            if contradictory and transition_verified:
                contradictory_after_verification = True
            failure = self._run_failure.value if self._run_failure is not None else "monitor"
            dimensions = ",".join(value.value for value in self._run_drift_dimensions) or "none"
            _LOGGER.warning(
                "q2-observation continued issue=%s failure=%s drift=%s count=%d",
                reason,
                failure,
                dimensions,
                diagnostic_issue_count,
            )

        def assert_no_unsafe_active_flow(states: Mapping[str, DeviceState]) -> None:
            for snapshot in record.snapshots:
                state = states[snapshot.device_id]
                capabilities = self._get_device(snapshot.device_id).capabilities
                guarded_maximum = min(
                    capabilities.power_limits.max_power,
                    _SCHEDULE_LINKAGE_TEST_MAX_POWER,
                )
                if state.timer_enabled is True:
                    active_flow = state.observed_attributes.get("AutoFlow")
                else:
                    active_flow = state.power
                if (
                    isinstance(active_flow, int)
                    and not isinstance(active_flow, bool)
                    and active_flow > guarded_maximum
                ):
                    self._run_failure = ScheduleLinkageRunFailure.MONITOR_AUTO_EVIDENCE
                    self._run_drift_dimensions = (
                        ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
                    )
                    raise ScheduleLinkageApplyError(
                        "staged monitor observed unsafe AutoFlow above the attended cap"
                    )

        def log_observed_pair(states: Mapping[str, DeviceState], sampled_at: float) -> None:
            master = states[spec.master_device_id]
            slave = states[spec.slave_device_id]
            master_values = master.observed_attributes
            slave_values = slave.observed_attributes
            _LOGGER.warning(
                "q2-observation sample monotonic=%.3f "
                "master=timer:%s,role:%s,auto:%s/%s/%s "
                "slave=timer:%s,role:%s,auto:%s/%s/%s",
                sampled_at,
                master.timer_enabled,
                getattr(master.linkage, "value", master.linkage),
                master_values.get("AutoMode"),
                master_values.get("AutoFlow"),
                master_values.get("AutoFreq"),
                slave.timer_enabled,
                getattr(slave.linkage, "value", slave.linkage),
                slave_values.get("AutoMode"),
                slave_values.get("AutoFlow"),
                slave_values.get("AutoFreq"),
            )

        async def wait_for_next_sample(heartbeat_started_at: float | None = None) -> None:
            elapsed = (
                0.0
                if heartbeat_started_at is None
                else max(0.0, self._monotonic() - heartbeat_started_at)
            )
            await self._sleep(
                min(
                    spec.verification_interval_seconds,
                    max(0.0, _STAGED_MONITOR_HEARTBEAT_MAX_INTERVAL_SECONDS - elapsed),
                )
            )

        while self._monotonic() <= self._require_observation_deadline():
            self._run_failure = ScheduleLinkageRunFailure.MONITOR
            self._run_drift_dimensions = ()
            if self._stop_requested():
                return ScheduleLinkageStopReason.MANUAL, False
            if not self._active_observation_allowed():
                raise ScheduleLinkageApplyError(
                    "schedule-linkage safety authority was revoked"
                )
            try:
                states, heartbeat_started_at, transport_retried = (
                    await self._acquire_staged_monitor_pair_with_retry(record)
                )
            except asyncio.CancelledError:
                raise
            except ScheduleLinkageApplyError:
                if self._run_failure is ScheduleLinkageRunFailure.MONITOR_DEADLINE:
                    break
                raise
            except Exception:
                if not spec.complete_observation_epoch:
                    raise
                if not self._active_observation_allowed() or self._stop_requested():
                    raise
                record_diagnostic_issue("read")
                reset_candidate_tracking()
                await wait_for_next_sample()
                continue
            sampled_at = self._monotonic()
            if sampled_at > self._require_observation_deadline():
                break
            assert_no_unsafe_active_flow(states)
            if spec.complete_observation_epoch:
                log_observed_pair(states, sampled_at)
            if transport_retried:
                # A reconnect creates an unobserved interval between two session generations.
                # Preserve irreversible A-to-B field evidence, but never count that gap toward
                # the required unchanged post-boundary observation.
                consecutive_after = 0
                previous_after = None
                stable_after_started_at = None
            try:
                classifications = self._classify_staged_auto_sides(
                    record,
                    states,
                    expected_roles,
                )
            except ScheduleLinkageApplyError:
                if not spec.complete_observation_epoch:
                    raise
                record_diagnostic_issue("classification")
                reset_candidate_tracking()
                await wait_for_next_sample(heartbeat_started_at)
                continue
            sides = {
                device_id: classification.side
                for device_id, classification in classifications.items()
            }
            master_classification = classifications[spec.master_device_id]
            terminal_candidate = master_classification.auto_side == "after" and all(
                classification.auto_side != "transitional"
                and classification.control_side != "transitional"
                for classification in classifications.values()
            )
            if (
                sampled_at < transition_not_before
                and any(
                    classification.side != "before" or classification.b_fields
                    for classification in classifications.values()
                )
            ):
                self._run_failure = (
                    ScheduleLinkageRunFailure.MONITOR_EARLY_AUTO_TRANSITION
                )
                self._run_drift_dimensions = (
                    ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
                )
                if not spec.complete_observation_epoch:
                    raise ScheduleLinkageApplyError(
                        "staged Auto evidence changed before the conservative boundary window"
                    )
                record_diagnostic_issue("early_transition")
                reset_candidate_tracking()
                await wait_for_next_sample(heartbeat_started_at)
                continue
            auto_regression_reason: str | None = None
            for device_id in auto_after_seen:
                if classifications[device_id].auto_side == "before":
                    auto_regression_reason = (
                        "staged Auto evidence returned to its prior entry"
                    )
                    break
                if classifications[device_id].auto_side == "transitional":
                    auto_regression_reason = (
                        "staged Auto evidence regressed to a partial transition"
                    )
                    break
            field_regressed = any(
                not seen_fields.issubset(classifications[device_id].b_fields)
                for device_id, seen_fields in b_fields_seen.items()
            )
            if auto_regression_reason is not None or field_regressed:
                self._run_failure = ScheduleLinkageRunFailure.MONITOR_AUTO_REGRESSION
                self._run_drift_dimensions = (
                    ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
                )
                if not spec.complete_observation_epoch:
                    raise ScheduleLinkageApplyError(
                        auto_regression_reason
                        or "staged transition field evidence regressed toward its prior entry"
                    )
                record_diagnostic_issue("regression", contradictory=True)
                reset_candidate_tracking()
                await wait_for_next_sample(heartbeat_started_at)
                continue
            partial_timeout_reason: str | None = None
            for device_id, classification in classifications.items():
                started_at = partial_started_at[device_id]
                new_b_fields = classification.b_fields - b_fields_seen[device_id]
                settled = classification.side == "after" or (
                    terminal_candidate
                    and device_id == spec.slave_device_id
                    and classification.auto_side == "before"
                    and classification.control_side != "transitional"
                )
                if started_at is not None and (
                    sampled_at - started_at
                    > _STAGED_AUTO_PARTIAL_SETTLE_MAX_SECONDS
                ):
                    self._run_failure = (
                        ScheduleLinkageRunFailure.MONITOR_AUTO_TRANSITION_TIMEOUT
                    )
                    self._run_drift_dimensions = (
                        ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
                    )
                    partial_timeout_reason = (
                        "staged Auto partial transition did not settle in time"
                    )
                    break
                if classification.side == "transitional" and not settled:
                    if started_at is None:
                        partial_started_at[device_id] = sampled_at
                    if new_b_fields:
                        partial_stalled_count[device_id] = 0
                    else:
                        partial_stalled_count[device_id] += 1
                    if (
                        partial_stalled_count[device_id]
                        > _STAGED_AUTO_PARTIAL_MAX_STALLED_SAMPLES
                    ):
                        self._run_failure = (
                            ScheduleLinkageRunFailure.MONITOR_AUTO_TRANSITION_TIMEOUT
                        )
                        self._run_drift_dimensions = (
                            ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
                        )
                        partial_timeout_reason = (
                            "staged Auto partial transition exceeded its report limit"
                        )
                        break
                elif settled:
                    partial_started_at[device_id] = None
                    partial_stalled_count[device_id] = 0
                b_fields_seen[device_id].update(classification.b_fields)
            if partial_timeout_reason is not None:
                if not spec.complete_observation_epoch:
                    raise ScheduleLinkageApplyError(partial_timeout_reason)
                record_diagnostic_issue("partial_timeout")
                reset_candidate_tracking()
                await wait_for_next_sample(heartbeat_started_at)
                continue
            auto_after_seen.update(
                device_id
                for device_id, classification in classifications.items()
                if classification.auto_side == "after"
            )
            if all(side == "before" for side in sides.values()):
                try:
                    self._assert_pair_sample(
                        record,
                        states,
                        expected_roles,
                        phase="before",
                        allow_staged_control_transition=True,
                    )
                except ScheduleLinkageApplyError:
                    if not spec.complete_observation_epoch:
                        raise
                    record_diagnostic_issue("before_sample", contradictory=True)
                    reset_candidate_tracking()
                    await wait_for_next_sample(heartbeat_started_at)
                    continue
                consecutive_after = 0
                previous_after = None
                stable_after_started_at = None
            elif terminal_candidate:
                # The master's exact B tuple proves that the staged boundary has happened.  The
                # slave may expose its own B Flow, follow the master's Flow, keep A's Flow, or
                # even retain the exact A tuple; holding that bounded safe candidate stable is the
                # behavior this experiment is designed to classify.
                try:
                    evidence = self._assert_pair_sample(
                        record,
                        states,
                        expected_roles,
                        phase="after",
                        allow_staged_control_transition=True,
                    )
                except ScheduleLinkageApplyError:
                    if not spec.complete_observation_epoch:
                        raise
                    record_diagnostic_issue("after_sample", contradictory=True)
                    reset_candidate_tracking()
                    await wait_for_next_sample(heartbeat_started_at)
                    continue
                if previous_after == evidence:
                    consecutive_after += 1
                else:
                    consecutive_after = 1
                    previous_after = evidence
                    stable_after_started_at = sampled_at
                stable_for = (
                    0.0
                    if stable_after_started_at is None
                    else sampled_at - stable_after_started_at
                )
                if (
                    consecutive_after >= 2
                    and stable_for >= spec.post_boundary_stability_seconds
                ):
                    self._assert_observation_deadline()
                    if not spec.complete_observation_epoch:
                        return ScheduleLinkageStopReason.BOUNDARY_VERIFIED, True
                    if not transition_verified:
                        _LOGGER.warning(
                            "q2-observation stable boundary evidence reached; "
                            "continuing to the fixed epoch deadline"
                        )
                    transition_verified = True
            else:
                # AutoMode, AutoFlow and AutoFreq reports can refresh independently within one
                # controller.  An allow-listed partial tuple proves neither A nor B, so it emits
                # no sample and contributes no time or count toward stable-after evidence.
                consecutive_after = 0
                previous_after = None
                stable_after_started_at = None
            self._run_failure = ScheduleLinkageRunFailure.MONITOR
            self._run_drift_dimensions = ()
            await wait_for_next_sample(heartbeat_started_at)
        if spec.complete_observation_epoch:
            _LOGGER.warning(
                "q2-observation epoch completed verified=%s contradictory=%s issues=%d",
                transition_verified,
                contradictory_after_verification,
                diagnostic_issue_count,
            )
            return (
                ScheduleLinkageStopReason.EPOCH_COMPLETED,
                transition_verified and not contradictory_after_verification,
            )
        raise ScheduleLinkageApplyError(
            "staged Auto transition lacked two consecutive stable after samples"
        )

    def _classify_staged_auto_sides(
        self,
        record: ScheduleLinkageRecord,
        states: Mapping[str, DeviceState],
        expected_roles: Mapping[str, LinkageRole],
    ) -> dict[str, _StagedAutoClassification]:
        """Classify token-bound A/B evidence and safe fieldwise A-to-B reports."""

        self._run_failure = ScheduleLinkageRunFailure.MONITOR_STATE_EVIDENCE
        self._run_drift_dimensions = ()
        self._assert_pair_sample(
            record,
            states,
            expected_roles,
            phase="ambiguous",
            emit_sample=False,
            allow_staged_control_transition=True,
        )
        self._run_failure = ScheduleLinkageRunFailure.MONITOR_AUTO_EVIDENCE
        self._run_drift_dimensions = (
            ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
        )
        classifications: dict[str, _StagedAutoClassification] = {}
        for snapshot in record.snapshots:
            observed_role: Literal["master", "slave"] = (
                "master"
                if snapshot.device_id == record.spec.master_device_id
                else "slave"
            )
            self._set_pair_verification_failure(
                "auto",
                dimensions=(ScheduleLinkageDriftDimension.AUTO_EVIDENCE,),
                observed_role=observed_role,
            )
            state = states[snapshot.device_id]
            evidence = _observed_auto(snapshot.device_id, state)
            auto_b_fields = self._staged_auto_b_fields(record, snapshot, evidence)
            if evidence == snapshot.expectation.before:
                auto_side: Literal["before", "transitional", "after"] = "before"
            elif self._matches_after_evidence(record, snapshot, evidence):
                auto_side = "after"
            elif self._is_safe_staged_transitional_auto(record, snapshot, evidence):
                auto_side = "transitional"
            else:
                raise ScheduleLinkageApplyError(
                    f"device {snapshot.device_id!r} reported an unknown staged Auto tuple"
                )
            control_side, control_b_fields = self._classify_staged_control_side(
                snapshot,
                state,
                expected_roles[snapshot.device_id],
            )
            side = (
                "transitional"
                if auto_side == "transitional"
                or control_side == "transitional"
                or (auto_side == "before" and control_side == "after")
                else auto_side
            )
            classifications[snapshot.device_id] = _StagedAutoClassification(
                side=side,
                auto_side=auto_side,
                control_side=control_side,
                b_fields=auto_b_fields | control_b_fields,
            )
        return classifications

    @staticmethod
    def _staged_auto_b_fields(
        record: ScheduleLinkageRecord,
        snapshot: ScheduleLinkageSnapshot,
        evidence: ScheduleAutoEvidence,
    ) -> frozenset[_StagedTransitionField]:
        """Return irreversible B-side fields, excluding observed slave Flow variance."""

        expectation = snapshot.expectation
        fields: set[_StagedTransitionField] = set()
        if (
            expectation.before.mode != expectation.after_mode
            and evidence.mode == expectation.after_mode
        ):
            fields.add("auto_mode")
        slave_flow_variance = (
            record.spec.observe_slave_after_tuple_variance
            and snapshot.device_id == record.spec.slave_device_id
        )
        if (
            not slave_flow_variance
            and expectation.before.flow != expectation.after_flow
            and evidence.flow == expectation.after_flow
        ):
            fields.add("auto_flow")
        if (
            expectation.after_frequency is not None
            and expectation.before.frequency != expectation.after_frequency
            and evidence.frequency == expectation.after_frequency
        ):
            fields.add("auto_frequency")
        return frozenset(fields)

    def _classify_staged_control_side(
        self,
        snapshot: ScheduleLinkageSnapshot,
        state: DeviceState,
        expected_role: LinkageRole,
    ) -> tuple[
        Literal["before", "transitional", "after"],
        frozenset[_StagedTransitionField],
    ]:
        """Classify the separate live Mode/Frequency pair against this device's own A/B."""

        expectation = snapshot.expectation
        after_frequency = expectation.after_frequency
        if after_frequency is None:
            raise ScheduleLinkageApplyError(
                "staged live control evidence has no token-bound B frequency"
            )
        before = (
            snapshot.mode,
            self._expected_snapshot_frequency(snapshot, expected_role),
        )
        after = (expectation.after_mode, after_frequency)
        observed = (state.mode, state.frequency)
        fields: set[_StagedTransitionField] = set()
        if before[0] != after[0] and observed[0] == after[0]:
            fields.add("reported_mode")
        if before[1] != after[1] and observed[1] == after[1]:
            fields.add("reported_frequency")
        if observed == before:
            side: Literal["before", "transitional", "after"] = "before"
        elif observed == after:
            side = "after"
        else:
            # The staged invariant already proved both scalar values are participant-own A/B.
            # Therefore the only remaining shape is one field from each side of the pair.
            side = "transitional"
        return side, frozenset(fields)

    def _is_safe_staged_transitional_auto(
        self,
        record: ScheduleLinkageRecord,
        snapshot: ScheduleLinkageSnapshot,
        evidence: ScheduleAutoEvidence,
    ) -> bool:
        """Allow only fieldwise A-to-B reports inside the owned active monitor."""

        expectation = snapshot.expectation
        self._assert_auto_flow_safe(snapshot, evidence)
        if evidence.mode not in {expectation.before.mode, expectation.after_mode}:
            return False
        # The owned staged plan is Constant-to-Sine.  Retaining None here prevents a transient
        # feed tuple (and its AutoFeedTime) from being accepted as a boundary update.
        if evidence.feed_time != expectation.before.feed_time:
            return False
        allowed_frequencies = {expectation.before.frequency}
        if expectation.after_frequency is not None:
            allowed_frequencies.add(expectation.after_frequency)
        if evidence.frequency not in allowed_frequencies:
            return False
        slave_variance = (
            record.spec.observe_slave_after_tuple_variance
            and snapshot.device_id == record.spec.slave_device_id
        )
        return slave_variance or evidence.flow in {
            expectation.before.flow,
            expectation.after_flow,
        }

    def _assert_pair_sample(
        self,
        record: ScheduleLinkageRecord,
        states: Mapping[str, DeviceState],
        expected_roles: Mapping[str, LinkageRole],
        *,
        phase: Literal["before", "ambiguous", "after"],
        emit_sample: bool = True,
        allow_staged_control_transition: bool = False,
    ) -> _StablePairEvidence:
        effective: list[tuple[str, int, int, str, int]] = []
        observed: dict[str, ScheduleAutoEvidence] = {}
        for snapshot in record.snapshots:
            state = states[snapshot.device_id]
            observed_role: Literal["master", "slave"] = (
                "master"
                if snapshot.device_id == record.spec.master_device_id
                else "slave"
            )
            self._set_pair_verification_checkpoint()
            if allow_staged_control_transition:
                self._assert_staged_control_snapshot(
                    snapshot,
                    state,
                    expected_roles[snapshot.device_id],
                    observed_role=observed_role,
                )
            else:
                self._assert_immutable_snapshot(
                    snapshot,
                    state,
                    expected_roles[snapshot.device_id],
                    observed_role=observed_role,
                )
            if phase == "ambiguous":
                continue
            self._set_pair_verification_failure(
                "auto",
                dimensions=(ScheduleLinkageDriftDimension.AUTO_EVIDENCE,),
                observed_role=observed_role,
            )
            observed[snapshot.device_id] = _observed_auto(snapshot.device_id, state)

        full_linkage_topology = (
            expected_roles.get(record.spec.master_device_id) is LinkageRole.MASTER
            and expected_roles.get(record.spec.slave_device_id) is LinkageRole.ASYNC_SLAVE
        )
        sample: ScheduleLinkageSample | None = None
        if phase != "ambiguous" and full_linkage_topology:
            master_state = states[record.spec.master_device_id]
            slave_state = states[record.spec.slave_device_id]
            if master_state.power is None or slave_state.power is None:
                raise ScheduleLinkageApplyError("manual fallback Flow evidence is unavailable")
            sample = ScheduleLinkageSample(
                observed_at=datetime.now(UTC),
                phase=phase,
                master=observed[record.spec.master_device_id],
                slave=observed[record.spec.slave_device_id],
                master_manual_power=master_state.power,
                slave_manual_power=slave_state.power,
                master_reported_mode=master_state.mode,
                master_reported_frequency=master_state.frequency,
                slave_reported_mode=slave_state.mode,
                slave_reported_frequency=slave_state.frequency,
                master_linkage=LinkageRole.MASTER,
                slave_linkage=LinkageRole.ASYNC_SLAVE,
            )
            # Evidence is useful even when the following strict expectation check fails.  A sink
            # is diagnostic only: persistence trouble must not interrupt role compensation.
            if emit_sample:
                self._emit_sample_best_effort(sample)

        for snapshot in record.snapshots:
            if phase == "ambiguous":
                continue
            observed_role = (
                "master"
                if snapshot.device_id == record.spec.master_device_id
                else "slave"
            )
            self._set_pair_verification_failure(
                "auto",
                dimensions=(ScheduleLinkageDriftDimension.AUTO_EVIDENCE,),
                observed_role=observed_role,
            )
            evidence = observed[snapshot.device_id]
            if phase == "before":
                if evidence != snapshot.expectation.before:
                    raise ScheduleLinkageApplyError(
                        f"device {snapshot.device_id!r} pre-boundary Auto evidence drifted"
                    )
            elif not self._matches_after_evidence(record, snapshot, evidence):
                raise ScheduleLinkageApplyError(
                    f"device {snapshot.device_id!r} did not enter its next schedule entry"
                )
            state = states[snapshot.device_id]
            if state.frequency is None:
                raise ScheduleLinkageApplyError(
                    f"device {snapshot.device_id!r} has no reported Frequency evidence"
                )
            effective.append(
                (
                    evidence.mode,
                    evidence.flow,
                    evidence.frequency,
                    state.mode,
                    state.frequency,
                )
            )
        return tuple(effective)

    def _matches_after_evidence(
        self,
        record: ScheduleLinkageRecord,
        snapshot: ScheduleLinkageSnapshot,
        evidence: ScheduleAutoEvidence,
    ) -> bool:
        """Match B exactly, except for the experiment's deliberately observed slave Flow."""

        expectation = snapshot.expectation
        slave_variance = (
            record.spec.observe_slave_after_tuple_variance
            and snapshot.device_id == record.spec.slave_device_id
        )
        if slave_variance and not self._owned_staged_auto_transition_observation:
            return self._slave_flow_is_safe(snapshot, evidence)
        staged_slave_prior = (
            self._owned_staged_auto_transition_observation
            and slave_variance
            and evidence == expectation.before
        )
        if staged_slave_prior:
            return True
        if evidence.mode != expectation.after_mode:
            return False
        if (
            expectation.after_frequency is not None
            and evidence.frequency != expectation.after_frequency
        ):
            return False
        if not slave_variance:
            return evidence.flow == expectation.after_flow
        return self._slave_flow_is_safe(snapshot, evidence)

    def _slave_flow_is_safe(
        self,
        snapshot: ScheduleLinkageSnapshot,
        evidence: ScheduleAutoEvidence,
    ) -> bool:
        self._assert_auto_flow_safe(snapshot, evidence)
        return True

    def _assert_auto_flow_safe(
        self,
        snapshot: ScheduleLinkageSnapshot,
        evidence: ScheduleAutoEvidence,
    ) -> None:
        capabilities = self._get_device(snapshot.device_id).capabilities
        limits = capabilities.power_limits
        guarded_maximum = min(limits.max_power, _SCHEDULE_LINKAGE_TEST_MAX_POWER)
        if (
            not limits.min_power <= evidence.flow <= guarded_maximum
            or evidence.flow % capabilities.power_step
        ):
            raise ScheduleLinkageApplyError(
                f"device {snapshot.device_id!r} observed unsafe AutoFlow"
            )

    def _emit_sample_best_effort(self, sample: ScheduleLinkageSample) -> None:
        if self._sample_observer is None:
            return
        try:
            self._sample_observer(sample)
        except BaseException:
            _LOGGER.warning("schedule-linkage sample evidence could not be persisted")

    def _set_pair_verification_failure(
        self,
        substage: Literal[
            "session_refresh",
            "state_read",
            "deadline",
            "clock",
            "clock_skew",
            "clock_continuity",
            "state",
            "auto",
        ],
        *,
        dimensions: tuple[ScheduleLinkageDriftDimension, ...] = (),
        observed_role: Literal["master", "slave"] | None = None,
    ) -> None:
        """Set in-memory detail only while a post-write pair checkpoint owns failure."""

        participant = self._run_pair_participant
        if participant is None:
            if (
                self._run_failure
                is ScheduleLinkageRunFailure.MONITOR_STATE_EVIDENCE
                and substage == "state"
            ):
                self._run_drift_dimensions = dimensions
            return
        failure_by_stage = {
            ("master", "session_refresh"): (
                ScheduleLinkageRunFailure.MASTER_PAIR_SESSION_REFRESH
            ),
            ("master", "state_read"): ScheduleLinkageRunFailure.MASTER_PAIR_STATE_READ,
            ("master", "deadline"): ScheduleLinkageRunFailure.MASTER_PAIR_DEADLINE,
            ("master", "clock"): ScheduleLinkageRunFailure.MASTER_PAIR_CLOCK,
            ("master", "clock_skew"): (
                ScheduleLinkageRunFailure.MASTER_PAIR_CLOCK_SKEW
            ),
            ("master", "clock_continuity"): (
                ScheduleLinkageRunFailure.MASTER_PAIR_CLOCK_CONTINUITY
            ),
            ("master", "state"): ScheduleLinkageRunFailure.MASTER_PAIR_STATE,
            ("master", "auto"): ScheduleLinkageRunFailure.MASTER_PAIR_AUTO,
            ("slave", "session_refresh"): (
                ScheduleLinkageRunFailure.SLAVE_PAIR_SESSION_REFRESH
            ),
            ("slave", "state_read"): ScheduleLinkageRunFailure.SLAVE_PAIR_STATE_READ,
            ("slave", "deadline"): ScheduleLinkageRunFailure.SLAVE_PAIR_DEADLINE,
            ("slave", "clock"): ScheduleLinkageRunFailure.SLAVE_PAIR_CLOCK,
            ("slave", "clock_skew"): (
                ScheduleLinkageRunFailure.SLAVE_PAIR_CLOCK_SKEW
            ),
            ("slave", "clock_continuity"): (
                ScheduleLinkageRunFailure.SLAVE_PAIR_CLOCK_CONTINUITY
            ),
            ("slave", "state"): ScheduleLinkageRunFailure.SLAVE_PAIR_STATE,
            ("slave", "auto"): ScheduleLinkageRunFailure.SLAVE_PAIR_AUTO,
        }
        observed_failure = {
            ("master", "state", "master"): (
                ScheduleLinkageRunFailure.MASTER_PAIR_MASTER_STATE
            ),
            ("master", "state", "slave"): (
                ScheduleLinkageRunFailure.MASTER_PAIR_SLAVE_STATE
            ),
            ("master", "auto", "master"): (
                ScheduleLinkageRunFailure.MASTER_PAIR_MASTER_AUTO
            ),
            ("master", "auto", "slave"): (
                ScheduleLinkageRunFailure.MASTER_PAIR_SLAVE_AUTO
            ),
            ("slave", "state", "master"): (
                ScheduleLinkageRunFailure.SLAVE_PAIR_MASTER_STATE
            ),
            ("slave", "state", "slave"): (
                ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_STATE
            ),
            ("slave", "auto", "master"): (
                ScheduleLinkageRunFailure.SLAVE_PAIR_MASTER_AUTO
            ),
            ("slave", "auto", "slave"): (
                ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_AUTO
            ),
        }
        self._run_failure = observed_failure.get(
            (participant, substage, observed_role),
            failure_by_stage[(participant, substage)],
        )
        self._run_drift_dimensions = dimensions

    def _set_pair_verification_checkpoint(self) -> None:
        """Use a dimension-free stage until strict state inspection finds a mismatch."""

        participant = self._run_pair_participant
        if participant is None:
            return
        self._run_failure = {
            "master": ScheduleLinkageRunFailure.MASTER_PAIR_VERIFICATION,
            "slave": ScheduleLinkageRunFailure.SLAVE_PAIR_VERIFICATION,
        }[participant]
        self._run_drift_dimensions = ()

    async def _refresh_pair_sessions_if_enabled(
        self,
        spec: ScheduleLinkageSpec,
    ) -> None:
        """Optionally force two fresh sessions without exposing half-refreshed cancellation."""

        if not self._refresh_sessions_before_critical_reads:
            return
        await self._refresh_pair_sessions_uninterruptibly(spec)

    async def _refresh_pair_sessions_uninterruptibly(
        self,
        spec: ScheduleLinkageSpec,
    ) -> None:
        """Complete a paired transport boundary before propagating cancellation."""

        task = asyncio.create_task(self._refresh_pair_sessions(spec))
        cancellation_received = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancellation_received = True
        task.result()
        if cancellation_received:
            raise asyncio.CancelledError

    async def _refresh_pair_sessions(self, spec: ScheduleLinkageSpec) -> None:
        """Attempt both disconnects and both reconnects before reporting any failure."""

        devices = tuple(
            self._get_device(device_id)
            for device_id in (spec.master_device_id, spec.slave_device_id)
        )
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

    def _emit_progress_best_effort(
        self,
        kind: ScheduleLinkageRunProgressKind,
    ) -> None:
        observer = self._progress_observer
        if observer is None:
            return
        try:
            observer(
                ScheduleLinkageRunProgressEvent(
                    kind=kind,
                    occurred_at=datetime.now(UTC),
                )
            )
        except BaseException:
            _LOGGER.warning("schedule-linkage run progress could not be persisted")

    def _emit_failure_best_effort(self) -> None:
        observer = self._progress_observer
        failure = self._run_failure
        if observer is None or failure is None:
            return
        try:
            observer(
                ScheduleLinkageRunProgressEvent(
                    kind=ScheduleLinkageRunProgressKind.FAILED,
                    occurred_at=datetime.now(UTC),
                    failure=failure,
                    drift_dimensions=self._run_drift_dimensions,
                )
            )
        except BaseException:
            _LOGGER.warning("schedule-linkage run failure could not be persisted")

    async def _rollback_uninterruptibly(self, record: ScheduleLinkageRecord) -> None:
        task = asyncio.create_task(self._rollback(record))
        cancellation_received = False
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                cancellation_received = True
        task.result()
        if cancellation_received:
            raise asyncio.CancelledError

    async def _rollback(self, record: ScheduleLinkageRecord) -> None:
        # `_link_device` persists intent before it writes.  If the write, its ACK readback, or the
        # following progress save raises, the caller still holds an older Python object.  Recovery
        # must therefore start from the latest durable successor, never overwrite it with that
        # stale object and lose knowledge of a possibly-applied role.
        durable = self._store.load()
        if durable is None or durable.operation_id != record.operation_id:
            raise ScheduleLinkageRollbackError(
                "latest schedule-linkage recovery journal is unavailable"
            )
        if durable.spec != record.spec or durable.snapshots != record.snapshots:
            raise ScheduleLinkageRollbackError(
                "schedule-linkage recovery journal changed transaction identity"
            )
        self._assert_durable_progress_successor(record, durable)
        record = durable
        await self._assert_recovery_role_topology(record)
        rollback_record = record.model_copy(
            update={
                "phase": ScheduleLinkagePhase.ROLLING_BACK,
                "updated_at": self._record_now(record),
                "error": None,
            }
        )
        try:
            self._store.save(rollback_record)
        except Exception as error:
            # A save may report failure after its replacement and directory fsync completed.
            # Continue only when the store can prove that *this exact lease successor* is the
            # value it durably accepted.  Merely loading a same-identity record is insufficient:
            # an external predecessor could otherwise shrink the intent prefix and cause us to
            # detach the master while an unrecorded async slave remains attached.
            try:
                accepted = self._store.confirms_lease_successor(rollback_record)
            except Exception:
                accepted = False
            if not accepted:
                raise ScheduleLinkageRollbackError(
                    "schedule-linkage journal changed during rollback transition"
                ) from error
        record = rollback_record
        record = await self._reconcile_detached(record)
        intended = record.linkage_write_intent_device_ids
        detached = set(record.detached_device_ids)
        errors: set[str] = set()
        operation_id = record.operation_id

        def restore_guard() -> bool:
            return self._active_operation_id == operation_id

        for device_id in reversed(intended):
            if device_id in detached:
                continue
            device = self._get_device(device_id)
            try:
                await device.write_linkage(
                    LinkageRole.INDEPENDENT,
                    guard=restore_guard,
                )
                state = await device.get_state()
                self._assert_immutable_snapshot(
                    self._snapshot(record, device_id),
                    state,
                    LinkageRole.INDEPENDENT,
                )
            except Exception:
                try:
                    state = await device.get_state()
                    self._assert_immutable_snapshot(
                        self._snapshot(record, device_id),
                        state,
                        LinkageRole.INDEPENDENT,
                    )
                except Exception:
                    errors.add(device_id)
                    # Never detach the master while an async slave remains attached.  The
                    # durable intent keeps both roles recoverable in the same strict order.
                    break
            record = self._mark_detached(record, device_id)
            detached.add(device_id)
            errors.discard(device_id)
        record = await self._reconcile_detached(record)
        missing = set(intended) - set(record.detached_device_ids)
        if missing:
            errors.update(missing)
        if errors:
            updated = record.model_copy(
                update={
                    "phase": ScheduleLinkagePhase.RECOVERY_REQUIRED,
                    "updated_at": self._record_now(record),
                    "error": "role-only detach verification failed for: "
                    + ",".join(sorted(errors)),
                }
            )
            self._store.save(updated)
            raise ScheduleLinkageRollbackError(
                "schedule-linkage recovery is required for role-only detach"
            )
        self._store.clear()

    @staticmethod
    def _assert_durable_progress_successor(
        caller: ScheduleLinkageRecord,
        durable: ScheduleLinkageRecord,
    ) -> None:
        """Reject same-identity journals that regress any durable mutation progress."""

        progress = (
            (
                caller.linkage_write_intent_device_ids,
                durable.linkage_write_intent_device_ids,
            ),
            (caller.linked_device_ids, durable.linked_device_ids),
            (caller.detached_device_ids, durable.detached_device_ids),
        )
        if any(later[: len(earlier)] != earlier for earlier, later in progress):
            raise ScheduleLinkageRollbackError(
                "schedule-linkage durable progress regressed before rollback"
            )

    async def _assert_recovery_role_topology(
        self,
        record: ScheduleLinkageRecord,
    ) -> None:
        """Prove current roles are reachable from this exact intent before any write."""

        role_by_device = {
            record.spec.master_device_id: LinkageRole.MASTER,
            record.spec.slave_device_id: LinkageRole.ASYNC_SLAVE,
        }
        intended = set(record.linkage_write_intent_device_ids)
        detached = set(record.detached_device_ids)
        try:
            await self._refresh_pair_sessions_if_enabled(record.spec)
            self._validate_recovery_bindings(record)
            states = await asyncio.gather(
                *(
                    self._get_device(snapshot.device_id).get_state()
                    for snapshot in record.snapshots
                )
            )
            states_by_id = {
                snapshot.device_id: state
                for snapshot, state in zip(record.snapshots, states, strict=True)
            }
            if self._owned_staged_auto_transition_observation:
                self._assert_staged_auto_transition_preconditions(
                    record.spec,
                    states_by_id,
                    record.snapshots,
                )
            for snapshot in record.snapshots:
                state = states_by_id[snapshot.device_id]
                allowed = {LinkageRole.INDEPENDENT}
                if snapshot.device_id in intended and snapshot.device_id not in detached:
                    allowed.add(role_by_device[snapshot.device_id])
                if state.linkage not in allowed:
                    raise ScheduleLinkageApplyError(
                        f"device {snapshot.device_id!r} role is outside durable intent"
                    )
                if (
                    self._owned_staged_auto_transition_observation
                    and state.linkage is not LinkageRole.INDEPENDENT
                ):
                    # Recovery may begin while a scheduled report is incomplete or anomalous.
                    # Once forward execution has already failed, refusing the sole inverse
                    # Linkage write would strand a native role.  Ignore only the separate live
                    # Mode/Frequency pair long enough to detach this exact bound participant;
                    # fixed controls, schedule fingerprint and token-bound Auto evidence remain
                    # exact, and every post-detach read still requires the original snapshot.
                    self._assert_staged_recovery_control_snapshot(
                        record,
                        snapshot,
                        state,
                        state.linkage,
                    )
                else:
                    self._assert_immutable_snapshot(
                        snapshot,
                        state,
                        state.linkage,
                    )
        except Exception as error:
            raise ScheduleLinkageRollbackError(
                "controller role topology does not match durable recovery intent"
            ) from error

    async def _reconcile_detached(
        self,
        record: ScheduleLinkageRecord,
    ) -> ScheduleLinkageRecord:
        detached: list[str] = []
        for device_id in reversed(record.linkage_write_intent_device_ids):
            try:
                state = await self._get_device(device_id).get_state()
                self._assert_immutable_snapshot(
                    self._snapshot(record, device_id),
                    state,
                    LinkageRole.INDEPENDENT,
                )
            except Exception:
                break
            detached.append(device_id)
        detached_ids = tuple(detached)
        if detached_ids == record.detached_device_ids:
            return record
        updated = record.model_copy(
            update={
                "detached_device_ids": detached_ids,
                "updated_at": self._record_now(record),
                "error": (
                    record.error
                    if record.phase is ScheduleLinkagePhase.RECOVERY_REQUIRED
                    else None
                ),
            }
        )
        self._store.save(updated)
        return updated

    def _mark_detached(
        self,
        record: ScheduleLinkageRecord,
        device_id: str,
    ) -> ScheduleLinkageRecord:
        expected = tuple(reversed(record.linkage_write_intent_device_ids))
        detached = (*record.detached_device_ids, device_id)
        if detached != expected[: len(detached)]:
            raise ScheduleLinkageRollbackError("role detach order is not slave-to-master")
        updated = record.model_copy(
            update={
                "detached_device_ids": detached,
                "updated_at": self._record_now(record),
                "error": (
                    record.error
                    if record.phase is ScheduleLinkagePhase.RECOVERY_REQUIRED
                    else None
                ),
            }
        )
        self._store.save(updated)
        return updated

    def _transition(
        self,
        record: ScheduleLinkageRecord,
        phase: ScheduleLinkagePhase,
    ) -> ScheduleLinkageRecord:
        updated = record.model_copy(
            update={"phase": phase, "updated_at": self._record_now(record), "error": None}
        )
        self._store.save(updated)
        return updated

    def _assert_immutable_snapshot(
        self,
        snapshot: ScheduleLinkageSnapshot,
        state: DeviceState,
        expected_role: LinkageRole,
        *,
        observed_role: Literal["master", "slave"] | None = None,
    ) -> None:
        self._assert_snapshot_control_sets(
            snapshot,
            state,
            expected_role,
            allowed_modes=frozenset({snapshot.mode}),
            allowed_frequencies=frozenset(
                {self._expected_snapshot_frequency(snapshot, expected_role)}
            ),
            observed_role=observed_role,
        )

    def _assert_staged_control_snapshot(
        self,
        snapshot: ScheduleLinkageSnapshot,
        state: DeviceState,
        expected_role: LinkageRole,
        *,
        observed_role: Literal["master", "slave"] | None = None,
    ) -> None:
        """Allow only the owned schedule's A/B live control pair while a role is linked."""

        after_frequency = snapshot.expectation.after_frequency
        if (
            not self._owned_staged_auto_transition_observation
            or expected_role is LinkageRole.INDEPENDENT
            or after_frequency is None
        ):
            raise ScheduleLinkageApplyError(
                "staged live control evidence is outside the owned linked monitor"
            )
        self._assert_snapshot_control_sets(
            snapshot,
            state,
            expected_role,
            allowed_modes=frozenset(
                {snapshot.mode, snapshot.expectation.after_mode}
            ),
            allowed_frequencies=frozenset(
                {
                    self._expected_snapshot_frequency(snapshot, expected_role),
                    after_frequency,
                }
            ),
            observed_role=observed_role,
        )

    def _assert_staged_recovery_control_snapshot(
        self,
        record: ScheduleLinkageRecord,
        snapshot: ScheduleLinkageSnapshot,
        state: DeviceState,
        expected_role: LinkageRole,
    ) -> None:
        """Authorize Linkage-only detach despite a known decoded live Mode anomaly.

        Monitor acceptance remains limited to the participant's token-bound A/B pair.  This
        broader gate is recovery-only: physical binding, durable role intent, fixed controls,
        schedule fingerprint and token-bound Auto evidence are still proved, and the first
        independent read after the inverse role write must match the exact snapshot.
        """

        if (
            not self._owned_staged_auto_transition_observation
            or expected_role is LinkageRole.INDEPENDENT
            or state.mode not in _KNOWN_PRO_MODES
        ):
            raise ScheduleLinkageApplyError(
                "staged live control evidence is unsafe for role recovery"
            )
        evidence = _observed_auto(snapshot.device_id, state)
        if not (
            evidence == snapshot.expectation.before
            or self._matches_after_evidence(record, snapshot, evidence)
            or self._is_safe_staged_transitional_auto(record, snapshot, evidence)
        ):
            raise ScheduleLinkageApplyError(
                "staged Auto evidence is outside the owned recovery transition"
            )
        if state.frequency is None:
            raise ScheduleLinkageApplyError(
                "staged live Frequency is unavailable for role recovery"
            )
        self._assert_snapshot_control_sets(
            snapshot,
            state,
            expected_role,
            allowed_modes=frozenset({state.mode}),
            allowed_frequencies=frozenset({state.frequency}),
            observed_role=None,
        )

    def _expected_snapshot_frequency(
        self,
        snapshot: ScheduleLinkageSnapshot,
        expected_role: LinkageRole,
    ) -> int:
        return (
            self._staged_role_frequency_pins[snapshot.device_id]
            if self._owned_staged_auto_transition_observation
            and expected_role is not LinkageRole.INDEPENDENT
            and snapshot.device_id in self._staged_role_frequency_pins
            else snapshot.frequency
        )

    def _assert_snapshot_control_sets(
        self,
        snapshot: ScheduleLinkageSnapshot,
        state: DeviceState,
        expected_role: LinkageRole,
        *,
        allowed_modes: frozenset[str],
        allowed_frequencies: frozenset[int],
        observed_role: Literal["master", "slave"] | None,
    ) -> None:
        """Keep every non-schedule-controlled dimension exact and report only its name."""

        dimensions: set[ScheduleLinkageDriftDimension] = set()
        if not state.online:
            dimensions.add(ScheduleLinkageDriftDimension.ONLINE)
        if state.error:
            dimensions.add(ScheduleLinkageDriftDimension.ERROR)
        for dimension, actual_value, expected_value in (
            (
                ScheduleLinkageDriftDimension.ENABLED,
                state.enabled,
                snapshot.enabled,
            ),
            (
                ScheduleLinkageDriftDimension.POWER,
                state.power,
                snapshot.power,
            ),
            (
                ScheduleLinkageDriftDimension.TIMER_ENABLED,
                state.timer_enabled,
                True,
            ),
            (
                ScheduleLinkageDriftDimension.LINKAGE,
                state.linkage,
                expected_role,
            ),
        ):
            if actual_value != expected_value:
                dimensions.add(dimension)
        if state.mode not in allowed_modes:
            dimensions.add(ScheduleLinkageDriftDimension.MODE)
        if state.frequency not in allowed_frequencies:
            dimensions.add(ScheduleLinkageDriftDimension.FREQUENCY)
        schedule_mismatch = (
            schedule_structure_fingerprint(state.schedule) != snapshot.schedule_fingerprint
        )
        if schedule_mismatch:
            dimensions.add(ScheduleLinkageDriftDimension.SCHEDULE_FINGERPRINT)
        canonical_dimensions = tuple(
            dimension
            for dimension in ScheduleLinkageDriftDimension
            if dimension in dimensions
        )
        if dimensions:
            self._set_pair_verification_failure(
                "state",
                dimensions=canonical_dimensions,
                observed_role=observed_role,
            )

        self._assert_healthy(snapshot.device_id, state)
        fixed_actual = (
            state.enabled,
            state.power,
            state.timer_enabled,
            state.linkage,
        )
        fixed_expected = (snapshot.enabled, snapshot.power, True, expected_role)
        if (
            fixed_actual != fixed_expected
            or state.mode not in allowed_modes
            or state.frequency not in allowed_frequencies
        ):
            raise ScheduleLinkageApplyError(
                f"device {snapshot.device_id!r} changed outside Linkage"
            )
        if schedule_mismatch:
            raise ScheduleLinkageApplyError(
                f"device {snapshot.device_id!r} schedule fingerprint changed"
            )

    def _assert_externally_disarmed_proof(
        self,
        snapshot: ScheduleLinkageSnapshot,
        state: ScheduleLinkageExternalDisarmState,
    ) -> None:
        actual = (
            state.enabled,
            state.power,
            state.mode,
            state.frequency,
            state.timer_enabled,
            state.linkage,
        )
        expected = (
            snapshot.enabled,
            snapshot.power,
            snapshot.mode,
            snapshot.frequency,
            False,
            LinkageRole.INDEPENDENT,
        )
        if actual != expected:
            raise ScheduleLinkageRollbackError(
                f"device {snapshot.device_id!r} external disarm is not exact"
            )
        if state.schedule_fingerprint != snapshot.schedule_fingerprint:
            raise ScheduleLinkageRollbackError(
                f"device {snapshot.device_id!r} schedule changed before external disarm closure"
            )

    @staticmethod
    def _assert_healthy(device_id: str, state: DeviceState) -> None:
        if not state.online or state.error:
            raise ScheduleLinkagePreflightError(
                f"device {device_id!r} is offline or in error",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CONTROL_BASELINE,
            )

    @staticmethod
    def _state_clock_for(device_id: str, state: DeviceState) -> datetime:
        schedule = state.schedule
        if schedule is None or schedule.device_local_time is None:
            raise ScheduleLinkageApplyError(
                f"device {device_id!r} lost its fresh schedule clock"
            )
        clock = schedule.device_local_time
        if clock.tzinfo is not None:
            raise ScheduleLinkageApplyError(
                f"device {device_id!r} schedule clock is not device-local naive time"
            )
        return clock

    def _validate_capabilities(
        self,
        device: JebaoDevice,
        role: LinkageRole,
        *,
        permit_disconnected: bool = False,
    ) -> None:
        if not permit_disconnected and not device.connected:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} is disconnected",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CAPABILITY,
            )
        capabilities = device.capabilities
        if (
            Capability.LINKAGE not in capabilities.writable
            or role not in capabilities.linkage_roles
        ):
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} lacks guarded role support",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CAPABILITY,
            )
        if capabilities.product_key is None:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} has no known product key",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CAPABILITY,
            )

    def _validate_recovery_bindings(
        self,
        record: ScheduleLinkageRecord,
        *,
        permit_disconnected: bool = False,
    ) -> None:
        for snapshot in record.snapshots:
            device = self._get_device(snapshot.device_id)
            if (
                (not permit_disconnected and not device.connected)
                or device.physical_binding != snapshot.physical_binding
            ):
                raise ScheduleLinkagePreflightError(
                    f"device {snapshot.device_id!r} no longer matches its recovery binding"
                )
            if Capability.LINKAGE not in device.capabilities.writable:
                raise ScheduleLinkagePreflightError(
                    f"device {snapshot.device_id!r} cannot perform role-only recovery"
                )

    def _forward_write_allowed(self) -> bool:
        deadline = self._forward_deadline
        return (
            self._active_operation_id is not None
            and self._safety_epoch is not None
            and self._safety_interlock.permitted
            and self._safety_interlock.epoch == self._safety_epoch
            and deadline is not None
            and self._monotonic() < deadline
            and not self._stop_requested()
        )

    def _active_observation_allowed(self) -> bool:
        """Keep observing after the pre-boundary write window has intentionally closed."""

        return (
            self._active_operation_id is not None
            and self._safety_epoch is not None
            and self._safety_interlock.permitted
            and self._safety_interlock.epoch == self._safety_epoch
            and self._observation_deadline is not None
            and self._monotonic() <= self._observation_deadline
            and not self._stop_requested()
        )

    def _stop_requested(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    @staticmethod
    def _record_now(record: ScheduleLinkageRecord) -> datetime:
        return max(record.updated_at, datetime.now(UTC))

    def _monotonic(self) -> float:
        if self._monotonic_clock is not None:
            return self._monotonic_clock()
        return asyncio.get_running_loop().time()

    def _require_observation_deadline(self) -> float:
        if self._observation_deadline is None:
            raise ScheduleLinkageApplyError("schedule-linkage has no observation deadline")
        return self._observation_deadline

    def _assert_observation_deadline(self, sampled_at: float | None = None) -> None:
        checked_at = self._monotonic() if sampled_at is None else sampled_at
        if checked_at > self._require_observation_deadline():
            raise ScheduleLinkageApplyError("schedule-linkage observation deadline expired")

    def _clock_anchor(
        self,
        states: Mapping[str, DeviceState],
        sampled_at: float,
    ) -> _ClockAnchor:
        return _ClockAnchor(
            clocks={
                device_id: self._state_clock_for(device_id, state)
                for device_id, state in states.items()
            },
            sampled_at_monotonic=sampled_at,
        )

    def _assert_after_within_immediate_slot(
        self,
        record: ScheduleLinkageRecord,
        states: Mapping[str, DeviceState],
    ) -> None:
        for snapshot in record.snapshots:
            clock = self._state_clock_for(snapshot.device_id, states[snapshot.device_id])
            expectation = snapshot.expectation
            if not expectation.boundary_at < clock < expectation.after_valid_until:
                raise ScheduleLinkageApplyError(
                    f"device {snapshot.device_id!r} left the immediate next schedule entry"
                )

    def _assert_pair_clock_skew(
        self,
        spec: ScheduleLinkageSpec,
        states: Mapping[str, DeviceState],
    ) -> None:
        clocks = [self._state_clock_for(device_id, state) for device_id, state in states.items()]
        if max(clocks) - min(clocks) > timedelta(seconds=spec.maximum_clock_skew_seconds):
            raise ScheduleLinkagePreflightError(
                "device-local schedule clocks exceed the allowed pair skew",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CLOCK,
            )

    def _assert_clock_continuity(
        self,
        spec: ScheduleLinkageSpec,
        states: Mapping[str, DeviceState],
        *,
        previous_clocks: Mapping[str, datetime],
        elapsed_monotonic: float,
    ) -> None:
        self._assert_pair_clock_skew(spec, states)
        if elapsed_monotonic < 0:
            raise ScheduleLinkageApplyError("monotonic observation clock regressed")
        maximum_advance = elapsed_monotonic + spec.clock_advance_tolerance_seconds
        for device_id, state in states.items():
            current = self._state_clock_for(device_id, state)
            advance = (current - previous_clocks[device_id]).total_seconds()
            if advance < 0:
                raise ScheduleLinkageApplyError(
                    f"device {device_id!r} schedule clock regressed"
                )
            if advance > maximum_advance:
                raise ScheduleLinkageApplyError(
                    f"device {device_id!r} schedule clock advanced implausibly"
                )

    def _get_device(self, device_id: str) -> JebaoDevice:
        try:
            return self._devices[device_id]
        except KeyError as error:
            raise ScheduleLinkagePreflightError(
                f"device {device_id!r} is not registered",
                failure=ScheduleLinkageRunFailure.PREFLIGHT_CAPABILITY,
            ) from error

    @staticmethod
    def _snapshot(
        record: ScheduleLinkageRecord,
        device_id: str,
    ) -> ScheduleLinkageSnapshot:
        return next(snapshot for snapshot in record.snapshots if snapshot.device_id == device_id)


def _constant_time_equal(left: str, right: str) -> bool:
    # Both values are fixed-length SHA-256 hex strings, but compare their bytes without a
    # content-dependent early return because this token is the attended write authorization.
    return hmac.compare_digest(left, right)


__all__ = [
    "PrerequisiteAuthorizer",
    "ScheduleActiveLinkageController",
    "ScheduleAutoEvidence",
    "ScheduleBoundaryExpectation",
    "ScheduleLinkageApplyError",
    "ScheduleLinkageBusyError",
    "ScheduleLinkageDriftDimension",
    "ScheduleLinkageError",
    "ScheduleLinkageExternalDisarmProof",
    "ScheduleLinkageExternalDisarmState",
    "ScheduleLinkageJournalClaimError",
    "ScheduleLinkageJournalStore",
    "ScheduleLinkagePhase",
    "ScheduleLinkagePreflight",
    "ScheduleLinkagePreflightError",
    "ScheduleLinkageRecord",
    "ScheduleLinkageResult",
    "ScheduleLinkageRollbackError",
    "ScheduleLinkageRunFailure",
    "ScheduleLinkageRunProgressEvent",
    "ScheduleLinkageRunProgressKind",
    "ScheduleLinkageRunProgressObserver",
    "ScheduleLinkageSample",
    "ScheduleLinkageSnapshot",
    "ScheduleLinkageSpec",
    "ScheduleLinkageStopReason",
    "schedule_linkage_confirmation_token",
    "schedule_linkage_run_progress_rank",
]
