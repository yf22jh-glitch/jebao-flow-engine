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

from jebao_flow.devices.base import JebaoDevice
from jebao_flow.devices.identity import PhysicalDeviceBinding, physical_identity_key
from jebao_flow.devices.linkage import LinkageSafetyInterlock, schedule_structure_fingerprint
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
    MANUAL = "manual"


class ScheduleLinkageRunProgressKind(StrEnum):
    """Allow-listed, identity-free milestones for one role activation run."""

    FRESH_CAPTURE_STARTED = "fresh_capture_started"
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
    SLAVE_PAIR_VERIFIED = "slave_pair_verified"
    MONITOR_STARTED = "monitor_started"
    MONITOR_COMPLETED = "monitor_completed"
    FAILED = "failed"


class ScheduleLinkageRunFailure(StrEnum):
    """Allow-listed failure locations safe to persist outside the role transaction."""

    FRESH_CAPTURE = "fresh_capture"
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
    SLAVE_PAIR_STATE = "slave_pair_state"
    SLAVE_PAIR_MASTER_STATE = "slave_pair_master_state"
    SLAVE_PAIR_SLAVE_STATE = "slave_pair_slave_state"
    SLAVE_PAIR_AUTO = "slave_pair_auto"
    SLAVE_PAIR_MASTER_AUTO = "slave_pair_master_auto"
    SLAVE_PAIR_SLAVE_AUTO = "slave_pair_slave_auto"
    MONITOR = "monitor"
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
        pair_auto_failures = {
            ScheduleLinkageRunFailure.MASTER_PAIR_AUTO,
            ScheduleLinkageRunFailure.MASTER_PAIR_MASTER_AUTO,
            ScheduleLinkageRunFailure.MASTER_PAIR_SLAVE_AUTO,
            ScheduleLinkageRunFailure.SLAVE_PAIR_AUTO,
            ScheduleLinkageRunFailure.SLAVE_PAIR_MASTER_AUTO,
            ScheduleLinkageRunFailure.SLAVE_PAIR_SLAVE_AUTO,
        }
        dimensional_failures = {
            ScheduleLinkageRunFailure.CONFIRMATION_MISMATCH,
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
        if self.failure in pair_state_failures and any(
            dimension not in pair_state_dimensions for dimension in self.drift_dimensions
        ):
            raise ValueError("pair state failure contains a non-state dimension")
        if self.failure in pair_auto_failures and self.drift_dimensions != (
            ScheduleLinkageDriftDimension.AUTO_EVIDENCE,
        ):
            raise ValueError("pair Auto failure requires only the Auto evidence dimension")
        return self


def schedule_linkage_run_progress_rank(kind: ScheduleLinkageRunProgressKind) -> int:
    """Return the canonical monotonic rank used by durable outer-stage validation."""

    return tuple(ScheduleLinkageRunProgressKind).index(kind)


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
    observation_window_seconds: float = Field(default=180, gt=0, le=600)
    verification_interval_seconds: float = Field(default=1, gt=0, le=10)
    minimum_lead_seconds: float = Field(default=45, ge=10, le=180)
    ambiguous_band_seconds: float = Field(default=1, ge=0.1, le=5)
    post_boundary_stability_seconds: float = Field(default=0, ge=0, le=300)
    # The dedicated schedule-flow experiment needs to observe firmware behavior, including a
    # slave that remains on its prior tuple or follows the master.  Keep the ordinary role-only
    # diagnostic strict unless this journaled, opt-in evidence mode is explicitly selected.
    observe_slave_after_tuple_variance: bool = False
    maximum_clock_skew_seconds: float = Field(default=2, ge=0.1, le=10)
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
    hour_text, minute_text = value.split(":", maxsplit=1)
    hour = int(hour_text)
    minute = int(minute_text)
    if hour == 24 and minute == 0:
        return _DAY_SECONDS
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ScheduleLinkagePreflightError("decoded schedule has an invalid wall time")
    return hour * 60 * 60 + minute * 60


def _decoded_values(entry: ScheduleEntry) -> tuple[str, int, int, int | None]:
    mode = entry.mode
    if mode not in _KNOWN_PRO_MODES:
        raise ScheduleLinkagePreflightError("decoded schedule has an unknown mode")
    flow = entry.parameters.get("flow")
    frequency = entry.parameters.get("frequency")
    for label, value in (("flow", flow), ("frequency", frequency)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ScheduleLinkagePreflightError(
                f"decoded schedule has an invalid {label} value"
            )
    feed_time: int | None = None
    if mode == "feed":
        value = entry.parameters.get("feed_time")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 60:
            raise ScheduleLinkagePreflightError("decoded feed entry has an invalid feed time")
        feed_time = value
    return mode, flow, frequency, feed_time


def _validated_entries(schedule: DeviceSchedule) -> tuple[ScheduleEntry, ...]:
    if schedule.invalid_slots:
        raise ScheduleLinkagePreflightError("decoded schedule contains invalid slots")
    if len(schedule.entries) < 2:
        raise ScheduleLinkagePreflightError("at least two decoded schedule entries are required")
    entries = tuple(sorted(schedule.entries, key=lambda entry: _wall_seconds(entry.start)))
    starts = tuple(_wall_seconds(entry.start) for entry in entries)
    if len(set(starts)) != len(starts):
        raise ScheduleLinkagePreflightError("decoded schedule has duplicate entry starts")
    for index, entry in enumerate(entries):
        start = starts[index]
        end = _wall_seconds(entry.end) % _DAY_SECONDS
        duration = (end - start) % _DAY_SECONDS
        if duration == 0:
            raise ScheduleLinkagePreflightError("decoded schedule has a zero-length entry")
        next_start = starts[(index + 1) % len(entries)]
        if index + 1 == len(entries):
            next_start += _DAY_SECONDS
        if start + duration > next_start:
            raise ScheduleLinkagePreflightError("decoded schedule entries overlap")
        _decoded_values(entry)
    return entries


def _transition_plan(device_id: str, state: DeviceState) -> _TransitionPlan:
    schedule = state.schedule
    if schedule is None or schedule.device_local_time is None:
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} has no decoded device-local schedule clock"
        )
    if state.timer_enabled is not True or schedule.enabled is not True:
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} schedule-active test requires TimerON"
        )
    if schedule.device_local_time.tzinfo is not None:
        raise ScheduleLinkagePreflightError("device-local schedule clock must be timezone-naive")
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
            f"device {device_id!r} clock does not select exactly one schedule entry"
        )
    index, current, remaining = active[0]
    next_entry = entries[(index + 1) % len(entries)]
    if _wall_seconds(current.end) % _DAY_SECONDS != _wall_seconds(next_entry.start):
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} current boundary is not contiguous with its next entry"
        )
    current_mode = _decoded_values(current)[0]
    next_mode = _decoded_values(next_entry)[0]
    if current_mode not in _CURRENT_MODES or next_mode not in _NEXT_MODES:
        raise ScheduleLinkagePreflightError(
            "current/next schedule modes are outside the first audited boundary set"
        )
    return _TransitionPlan(current=current, next=next_entry, seconds_until_boundary=remaining)


def _observed_auto(device_id: str, state: DeviceState) -> ScheduleAutoEvidence:
    values = state.observed_attributes
    mode = values.get("AutoMode")
    flow = values.get("AutoFlow")
    frequency = values.get("AutoFreq")
    if not isinstance(mode, str) or mode not in _CURRENT_MODES:
        raise ScheduleLinkagePreflightError(f"device {device_id!r} reported invalid AutoMode")
    for label, value in (("AutoFlow", flow), ("AutoFreq", frequency)):
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ScheduleLinkagePreflightError(
                f"device {device_id!r} reported invalid {label}"
            )
    feed_time: int | None = None
    if mode == "feed":
        value = values.get("AutoFeedTime")
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 60:
            raise ScheduleLinkagePreflightError(
                f"device {device_id!r} reported invalid AutoFeedTime"
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
            f"device {device_id!r} AutoMode disagrees with its active entry"
        )
    if mode == "feed":
        # Captured Pro firmware reports effective defaults (30/5) while feed encodes 0/0.
        if evidence.feed_time != feed_time:
            raise ScheduleLinkagePreflightError(
                f"device {device_id!r} AutoFeedTime disagrees with its feed entry"
            )
    elif mode == "constant":
        # Constant frequency is likewise an ignored encoded zero; only Mode+Flow are semantic.
        if evidence.flow != flow:
            raise ScheduleLinkagePreflightError(
                f"device {device_id!r} AutoFlow disagrees with its constant entry"
            )
    elif evidence.flow != flow or evidence.frequency != frequency:
        raise ScheduleLinkagePreflightError(
            f"device {device_id!r} AutoFlow/AutoFreq disagree with its active entry"
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
        self._run_lock = asyncio.Lock()
        self._active_operation_id: str | None = None
        self._safety_epoch: int | None = None
        self._stop_event: asyncio.Event | None = None
        self._forward_deadline: float | None = None
        self._observation_deadline: float | None = None
        self._run_failure: ScheduleLinkageRunFailure | None = None
        self._run_drift_dimensions: tuple[ScheduleLinkageDriftDimension, ...] = ()
        self._run_pair_participant: Literal["master", "slave"] | None = None

    @property
    def active_operation_id(self) -> str | None:
        return self._active_operation_id

    async def preflight(self, spec: ScheduleLinkageSpec) -> ScheduleLinkagePreflight:
        """Capture an attended, write-free authorization bound to an absolute boundary."""

        if self._store.load() is not None:
            raise ScheduleLinkageBusyError("unfinished schedule-linkage recovery exists")
        if not self._safety_interlock.permitted:
            raise ScheduleLinkagePreflightError("schedule-linkage is blocked by the safety latch")
        snapshots = await self._capture_pair(spec)
        self._authorize(spec, snapshots)
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
                lease.__exit__(None, None, None)

    async def finalize_externally_disarmed(self, operation_id: str) -> bool:
        """Clear a role journal only after both controls prove independent and TimerOFF.

        The composed schedule-flow transaction owns the compensating full-control write.  Once it
        has stopped TimerON on both devices, ordinary role recovery can no longer require the
        immutable TimerON snapshot.  This no-write closure keeps that exception narrow and bound
        to the exact role operation while the temporary schedule fingerprint is still installed.
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
                    self._validate_recovery_bindings(
                        record,
                        permit_disconnected=self._refresh_sessions_before_critical_reads,
                    )
                    # The composed disarm proof may have consumed a paired 0x04 report while
                    # leaving its older 0x03 reply queued.  Close the role journal only from a
                    # newly authenticated stream, just like every other critical role read.
                    await self._refresh_pair_sessions_if_enabled(record.spec)
                    self._validate_recovery_bindings(record)
                    states = await self._read_pair(record.spec)
                    for snapshot in record.snapshots:
                        self._assert_externally_disarmed_snapshot(
                            snapshot,
                            states[snapshot.device_id],
                        )
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
            await self._refresh_pair_sessions_if_enabled(spec)
            fresh = await self._capture_pair(spec)
            self._assert_observation_deadline()
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
            self._run_failure = None
            self._run_drift_dimensions = ()
            self._run_pair_participant = None

    async def _capture_pair(
        self,
        spec: ScheduleLinkageSpec,
    ) -> tuple[ScheduleLinkageSnapshot, ...]:
        master = self._get_device(spec.master_device_id)
        slave = self._get_device(spec.slave_device_id)
        self._validate_capabilities(master, LinkageRole.MASTER)
        self._validate_capabilities(slave, LinkageRole.ASYNC_SLAVE)
        if master.capabilities.product_key != slave.capabilities.product_key:
            raise ScheduleLinkagePreflightError(
                "schedule-linkage requires the same qualified product family"
            )
        states = await self._read_pair(spec)
        self._assert_pair_clock_skew(spec, states)
        snapshots = self._snapshots_from_states(spec, states)
        return snapshots

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
                "master and slave physical bindings must be distinct"
            )
        if (
            master_expectation.boundary_at != slave_expectation.boundary_at
            or master_expectation.before.mode != slave_expectation.before.mode
            or master_expectation.after_mode != slave_expectation.after_mode
        ):
            raise ScheduleLinkagePreflightError(
                "both devices must authorize the same absolute schedule boundary"
            )
        if slave_expectation.before.flow == slave_expectation.after_flow:
            raise ScheduleLinkagePreflightError(
                "slave boundary must change AutoFlow to prove its own schedule advanced"
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
                "slave next Auto tuple must differ from master to prove its own schedule"
            )
        return snapshots

    def _snapshot_from_state(
        self,
        device: JebaoDevice,
        state: DeviceState,
        spec: ScheduleLinkageSpec,
    ) -> ScheduleLinkageSnapshot:
        self._assert_healthy(device.device_id, state)
        if state.enabled is not True:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} must be enabled before role-only testing"
            )
        if state.linkage is not LinkageRole.INDEPENDENT:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} must start independent"
            )
        if state.frequency is None:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} did not report manual frequency"
            )
        binding = device.physical_binding
        if binding is None or binding.product_key != device.capabilities.product_key:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} has no exact physical binding"
            )
        fingerprint = schedule_structure_fingerprint(state.schedule)
        if fingerprint is None:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} has no decoded schedule fingerprint"
            )
        plan, expectation = _expectation_from_state(device.device_id, state)
        remaining = plan.seconds_until_boundary
        if remaining < spec.minimum_lead_seconds:
            raise ScheduleLinkagePreflightError(
                "schedule boundary is too close for guarded role setup"
            )
        if remaining > spec.observation_window_seconds:
            raise ScheduleLinkagePreflightError(
                "next schedule boundary is outside the observation window"
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
                "observation window lacks post-boundary verification and rollback reserve"
            )
        limits = device.capabilities.power_limits
        if not limits.min_power <= state.power <= limits.max_power:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} manual fallback Flow is outside limits"
            )
        current_flow = expectation.before.flow
        if not limits.min_power <= current_flow <= limits.max_power:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} current effective AutoFlow is outside limits"
            )
        if not limits.min_power <= expectation.after_flow <= limits.max_power:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} next AutoFlow is outside limits"
            )
        guarded_maximum = min(limits.max_power, _SCHEDULE_LINKAGE_TEST_MAX_POWER)
        if state.power > guarded_maximum:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} manual fallback Flow exceeds "
                f"the guarded schedule-linkage maximum of {guarded_maximum}"
            )
        for boundary_side, flow in (
            ("current", current_flow),
            ("next", expectation.after_flow),
        ):
            if flow > guarded_maximum:
                raise ScheduleLinkagePreflightError(
                    f"device {device.device_id!r} {boundary_side} AutoFlow exceeds "
                    f"the guarded schedule-linkage maximum of {guarded_maximum}"
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
        states = await asyncio.gather(
            *(self._get_device(device_id).get_explicit_state() for device_id in ids)
        )
        return dict(zip(ids, states, strict=True))

    async def _read_pair_explicit_states_guarded(
        self,
        spec: ScheduleLinkageSpec,
    ) -> dict[str, DeviceState]:
        """Cancel a read promptly if stop or safety authority changes after refresh."""

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
                    "schedule-linkage stop was requested during frequency convergence"
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

    async def _assert_first_write_gate(
        self,
        record: ScheduleLinkageRecord,
    ) -> _ClockAnchor:
        await self._refresh_pair_sessions_if_enabled(record.spec)
        states = await self._read_pair(record.spec)
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
        states = await self._read_pair_states(record.spec)
        self._set_pair_verification_failure("clock")
        self._assert_pair_clock_skew(record.spec, states)
        sampled_at = self._monotonic()
        self._set_pair_verification_failure("deadline")
        self._assert_observation_deadline(sampled_at)
        self._set_pair_verification_failure("clock")
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
                initial_anchor=self._clock_anchor(states, sampled_at),
                initial_error=error,
                pair_failure=pair_failure,
            )
        self._run_failure = pair_failure
        self._run_drift_dimensions = ()
        linked = (*record.linked_device_ids, device_id)
        record = record.model_copy(
            update={"linked_device_ids": linked, "updated_at": self._record_now(record)}
        )
        self._store.save(record)
        self._emit_progress_best_effort(pair_verified)
        self._run_pair_participant = None
        return record, self._clock_anchor(states, sampled_at)

    async def _converge_role_frequency(
        self,
        record: ScheduleLinkageRecord,
        expected_roles: Mapping[str, LinkageRole],
        *,
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
        interval = min(
            spec.verification_interval_seconds,
            _ROLE_FREQUENCY_CONVERGENCE_MAX_INTERVAL_SECONDS,
        )
        exact_reads = 0
        previous_anchor = initial_anchor
        last_frequency_error = initial_error
        last_frequency_failure = self._run_failure
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
            self._set_pair_verification_failure("clock")
            self._assert_pair_clock_skew(spec, states)
            sampled_at = self._monotonic()
            self._set_pair_verification_failure("deadline")
            self._assert_observation_deadline(sampled_at)
            self._set_pair_verification_failure("clock")
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
            else:
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
            previous_anchor = self._clock_anchor(states, sampled_at)
            if convergence_expired:
                break
        self._run_failure = last_frequency_failure
        self._run_drift_dimensions = (ScheduleLinkageDriftDimension.FREQUENCY,)
        raise last_frequency_error

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
        previous_after: tuple[tuple[str, int, int], ...] | None = None
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

    def _assert_pair_sample(
        self,
        record: ScheduleLinkageRecord,
        states: Mapping[str, DeviceState],
        expected_roles: Mapping[str, LinkageRole],
        *,
        phase: Literal["before", "ambiguous", "after"],
        emit_sample: bool = True,
    ) -> tuple[tuple[str, int, int], ...]:
        effective: list[tuple[str, int, int]] = []
        observed: dict[str, ScheduleAutoEvidence] = {}
        for snapshot in record.snapshots:
            state = states[snapshot.device_id]
            observed_role: Literal["master", "slave"] = (
                "master"
                if snapshot.device_id == record.spec.master_device_id
                else "slave"
            )
            self._set_pair_verification_checkpoint()
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
            else:
                expectation = snapshot.expectation
                slave_variance = (
                    record.spec.observe_slave_after_tuple_variance
                    and snapshot.device_id == record.spec.slave_device_id
                )
                if not slave_variance and (
                    evidence.mode != expectation.after_mode
                    or evidence.flow != expectation.after_flow
                ):
                    raise ScheduleLinkageApplyError(
                        f"device {snapshot.device_id!r} did not enter its next schedule entry"
                    )
                if not slave_variance and (
                    expectation.after_frequency is not None
                    and evidence.frequency != expectation.after_frequency
                ):
                    raise ScheduleLinkageApplyError(
                        f"device {snapshot.device_id!r} next AutoFreq did not match"
                    )
                if slave_variance:
                    capabilities = self._get_device(snapshot.device_id).capabilities
                    limits = capabilities.power_limits
                    guarded_maximum = min(
                        limits.max_power,
                        _SCHEDULE_LINKAGE_TEST_MAX_POWER,
                    )
                    if (
                        not limits.min_power <= evidence.flow <= guarded_maximum
                        or evidence.flow % capabilities.power_step
                    ):
                        raise ScheduleLinkageApplyError(
                            f"device {snapshot.device_id!r} observed unsafe AutoFlow"
                        )
            effective.append((evidence.mode, evidence.flow, evidence.frequency))
        return tuple(effective)

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
            return
        failure_by_stage = {
            ("master", "session_refresh"): (
                ScheduleLinkageRunFailure.MASTER_PAIR_SESSION_REFRESH
            ),
            ("master", "state_read"): ScheduleLinkageRunFailure.MASTER_PAIR_STATE_READ,
            ("master", "deadline"): ScheduleLinkageRunFailure.MASTER_PAIR_DEADLINE,
            ("master", "clock"): ScheduleLinkageRunFailure.MASTER_PAIR_CLOCK,
            ("master", "state"): ScheduleLinkageRunFailure.MASTER_PAIR_STATE,
            ("master", "auto"): ScheduleLinkageRunFailure.MASTER_PAIR_AUTO,
            ("slave", "session_refresh"): (
                ScheduleLinkageRunFailure.SLAVE_PAIR_SESSION_REFRESH
            ),
            ("slave", "state_read"): ScheduleLinkageRunFailure.SLAVE_PAIR_STATE_READ,
            ("slave", "deadline"): ScheduleLinkageRunFailure.SLAVE_PAIR_DEADLINE,
            ("slave", "clock"): ScheduleLinkageRunFailure.SLAVE_PAIR_CLOCK,
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
            for snapshot, state in zip(record.snapshots, states, strict=True):
                allowed = {LinkageRole.INDEPENDENT}
                if snapshot.device_id in intended and snapshot.device_id not in detached:
                    allowed.add(role_by_device[snapshot.device_id])
                if state.linkage not in allowed:
                    raise ScheduleLinkageApplyError(
                        f"device {snapshot.device_id!r} role is outside durable intent"
                    )
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
                ScheduleLinkageDriftDimension.MODE,
                state.mode,
                snapshot.mode,
            ),
            (
                ScheduleLinkageDriftDimension.FREQUENCY,
                state.frequency,
                snapshot.frequency,
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
            True,
            expected_role,
        )
        if actual != expected:
            raise ScheduleLinkageApplyError(
                f"device {snapshot.device_id!r} changed outside Linkage"
            )
        if schedule_mismatch:
            raise ScheduleLinkageApplyError(
                f"device {snapshot.device_id!r} schedule fingerprint changed"
            )

    def _assert_externally_disarmed_snapshot(
        self,
        snapshot: ScheduleLinkageSnapshot,
        state: DeviceState,
    ) -> None:
        self._assert_healthy(snapshot.device_id, state)
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
        if schedule_structure_fingerprint(state.schedule) != snapshot.schedule_fingerprint:
            raise ScheduleLinkageRollbackError(
                f"device {snapshot.device_id!r} schedule changed before external disarm closure"
            )

    @staticmethod
    def _assert_healthy(device_id: str, state: DeviceState) -> None:
        if not state.online or state.error:
            raise ScheduleLinkagePreflightError(f"device {device_id!r} is offline or in error")

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

    def _validate_capabilities(self, device: JebaoDevice, role: LinkageRole) -> None:
        if not device.connected:
            raise ScheduleLinkagePreflightError(f"device {device.device_id!r} is disconnected")
        capabilities = device.capabilities
        if (
            Capability.LINKAGE not in capabilities.writable
            or role not in capabilities.linkage_roles
        ):
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} lacks guarded role support"
            )
        if capabilities.product_key is None:
            raise ScheduleLinkagePreflightError(
                f"device {device.device_id!r} has no known product key"
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
                "device-local schedule clocks exceed the allowed pair skew"
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
                f"device {device_id!r} is not registered"
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
