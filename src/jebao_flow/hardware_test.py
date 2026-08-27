"""Fail-closed, one-shot hardware harness for native Jebao linkage tests.

This module is intentionally separate from the read-only ``jebao-flowctl`` command.  It is only
for a short, attended aquarium-side test after the normal daemon and every other controller have
been stopped.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import hmac
import json
import os
import signal
import stat
import sys
import tempfile
from collections.abc import Awaitable, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jebao_flow.config import AppConfig, DeviceConfig, DeviceType, RuntimeMode, load_config
from jebao_flow.devices.base import (
    ControlAckFailureKind,
    ControlAckResolutionStage,
    ControlAckResolutionState,
    JebaoDevice,
)
from jebao_flow.devices.factory import create_lan_device, create_read_only_lan_device
from jebao_flow.devices.identity import (
    PhysicalDeviceBinding,
    configuration_fingerprint,
    physical_identity_key,
)
from jebao_flow.devices.linkage import (
    DeviceControlSnapshot,
    LinkageDiagnosticEvent,
    LinkageDiagnosticEventKind,
    LinkageForwardFailureCategory,
    LinkageJournalClaimError,
    LinkageJournalStore,
    LinkageLiveSlavePowerVerificationError,
    LinkageRecoveryAuthority,
    LinkageRecoveryReason,
    LinkageRollbackError,
    LinkageRollbackFailure,
    LinkageSafetyInterlock,
    LinkageStopReason,
    LinkageTestSpec,
    LinkageTransactionPhase,
    LinkageTransactionRecord,
    TemporaryLinkageController,
)
from jebao_flow.devices.observer import ResolvedDevice, resolve_device_bindings
from jebao_flow.devices.schedule_flow_experiment import (
    SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT,
    SCHEDULE_FLOW_STAGE_EVENT_LIMIT,
    ScheduleFlowExperimentSpec,
    ScheduleFlowOutcome,
    ScheduleFlowStage,
    ScheduleFlowStageEvent,
    classify_schedule_flow_sample,
    schedule_flow_stage_rank,
)
from jebao_flow.devices.schedule_linkage import (
    ScheduleLinkageRunProgressEvent,
    ScheduleLinkageSample,
    schedule_linkage_run_progress_rank,
)
from jebao_flow.hardware_guard import DeploymentHardwareGuard
from jebao_flow.hardware_safety import (
    HardwareSafetyRootError,
    emergency_stop_latch_path,
    native_linkage_intent_path,
    native_linkage_journal_path,
    physical_lock_directory,
    qualification_directory,
    schedule_linkage_intent_path,
    schedule_linkage_journal_path,
    temporary_schedule_journal_path,
    validate_hardware_safety_root,
    verification_intent_path,
    verification_journal_path,
)
from jebao_flow.logging import configure_logging
from jebao_flow.persistence import (
    DeviceQualificationReceipt,
    JsonLinkageJournalStore,
    JsonQualificationStore,
    LinkageJournalError,
)
from jebao_flow.protocol.discovery import GizwitsDiscovery
from jebao_flow.protocol.models import DeviceTarget, LinkageRole
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO
from jebao_flow.schedule_intent_validation import (
    TerminalScheduleIntentError,
    validate_terminal_schedule_intent_payload,
)

_TOKEN_VERSION = 1
_MAX_ATTENDED_POWER = 45
_MAX_ATTENDED_DURATION_SECONDS = 10
_MAX_SCHEDULE_BOOTSTRAP_DURATION_SECONDS = 600
_MAX_ATTENDED_COMMAND_INTERVAL_MS = 2000
_MAX_ATTENDED_READBACK_DELAY_MS = 1000
_MAX_ATTENDED_READBACK_ATTEMPTS = 3
_MAX_ATTENDED_DISCOVERY_TIMEOUT_SECONDS = 5
_MAX_AUTOMATIC_RECOVERY_GRACE_SECONDS = 30
_RECOVERY_ATTEMPTS = 3
_RECOVERY_RETRY_SECONDS = _MAX_ATTENDED_COMMAND_INTERVAL_MS / 1000
_RECOVERY_LATCH_POLL_SECONDS = 0.1
_LATE_EMERGENCY_STOP_TIMEOUT_SECONDS = 35.0
_MAX_SAFETY_ARTIFACT_BYTES = 1024 * 1024
_AUDITED_SNAPSHOT_MODES = frozenset({"constant", "pulse", "sine"})
TERMINAL_SCHEDULE_FLOW_OUTCOMES = frozenset(
    {
        *(outcome.value for outcome in ScheduleFlowOutcome),
        "armed_preview_cancelled",
        "crashed_before_first_write",
        "experiment_failed_restored",
        "recovered",
        "restored",
        "wire_qualified",
    }
)
_WIRE_QUALIFICATION_REQUIRED_STAGES = (
    ScheduleFlowStage.SENTINEL_VERIFIED,
    ScheduleFlowStage.SENTINEL_RESTORED,
    ScheduleFlowStage.OUTER_RESTORED,
)
_WIRE_QUALIFICATION_FORBIDDEN_STAGES = frozenset(
    {
        ScheduleFlowStage.FIELD_SNAPSHOT_STARTED,
        ScheduleFlowStage.FIELD_SNAPSHOT_COMPLETED,
        ScheduleFlowStage.FIELD_WRITE_STARTED,
        ScheduleFlowStage.FIELD_VERIFIED,
        ScheduleFlowStage.TIMER_ON_ARM_STARTED,
        ScheduleFlowStage.TIMER_ON_ARMED,
        ScheduleFlowStage.ROLE_PREFLIGHT_STARTED,
        ScheduleFlowStage.ROLE_PREFLIGHT_COMPLETED,
        ScheduleFlowStage.ROLE_OBSERVATION_STARTED,
        ScheduleFlowStage.ROLE_OBSERVATION_COMPLETED,
        ScheduleFlowStage.ROLE_DISARM_STARTED,
        ScheduleFlowStage.ROLE_DISARMED,
        ScheduleFlowStage.FIELD_RESTORE_STARTED,
        ScheduleFlowStage.FIELD_RESTORED,
    }
)


class HardwareTestError(RuntimeError):
    """A fail-closed harness validation or lifecycle error."""


class ConfirmationMismatchError(HardwareTestError):
    """The confirmed preview is no longer identical to the controller's fresh snapshot."""


def _require_private_regular_metadata(metadata: os.stat_result, *, label: str) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise HardwareTestError(f"{label} has unsafe metadata")


def _validate_open_private_file(descriptor: int, path: Path, *, label: str) -> None:
    try:
        opened = os.fstat(descriptor)
        current = os.stat(path, follow_symlinks=False)
    except OSError as error:
        raise HardwareTestError(f"{label} changed while opening") from error
    _require_private_regular_metadata(opened, label=label)
    _require_private_regular_metadata(current, label=label)
    if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
        raise HardwareTestError(f"{label} changed while opening")


def _open_existing_private_file(
    path: Path,
    *,
    label: str,
    allow_absent: bool,
) -> int | None:
    if not hasattr(os, "O_NOFOLLOW"):
        raise HardwareTestError("O_NOFOLLOW is required for hardware safety files")
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        if allow_absent:
            return None
        raise HardwareTestError(f"{label} disappeared") from None
    except OSError as error:
        raise HardwareTestError(f"{label} metadata is unavailable") from error
    _require_private_regular_metadata(metadata, label=label)

    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        _validate_open_private_file(descriptor, path, label=label)
        return descriptor
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


class HardwareTestIntentPhase(StrEnum):
    ARMED = "armed"
    STARTED = "started"
    RECOVERY_REQUIRED = "recovery_required"
    TERMINAL = "terminal"


class HardwareTestPrimaryFailure(StrEnum):
    """Redacted forward-test failures that must survive a later restore failure."""

    SLAVE_POWER_CHANGE_NOT_VERIFIED = "slave_power_change_not_verified"


class HardwareTestVerifiedSample(BaseModel):
    """Allow-listed state proven by a complete two-device read after the live step."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    verified_at: datetime
    master_power: int = Field(ge=0, le=100)
    slave_power: int = Field(ge=0, le=100)
    slave_linkage: LinkageRole


class HardwareTestEvidence(BaseModel):
    """Crash-durable, privacy-preserving progress for one attended native-linkage run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    active_entered_at: datetime | None = None
    live_slave_write_attempted_at: datetime | None = None
    live_slave_ack_unconfirmed_at: datetime | None = None
    live_slave_ack_failure_kind: ControlAckFailureKind | None = None
    live_slave_ack_resolution_started_at: datetime | None = None
    live_slave_ack_resolution_updated_at: datetime | None = None
    live_slave_ack_resolution_stage: ControlAckResolutionStage | None = None
    live_slave_ack_resolution_state: ControlAckResolutionState | None = None
    live_slave_ack_resolution_attempts: int | None = Field(default=None, ge=0, le=8)
    live_slave_adapter_verified_at: datetime | None = None
    live_slave_state_verified_without_ack_at: datetime | None = None
    live_slave_full_state_verified_at: datetime | None = None
    verified_sample_count: int = Field(default=0, ge=0)
    first_verified_sample: HardwareTestVerifiedSample | None = None
    last_verified_sample: HardwareTestVerifiedSample | None = None
    forward_failure: LinkageForwardFailureCategory | None = None
    rollback_started_at: datetime | None = None
    rollback_completed_at: datetime | None = None
    rollback_recovery_reasons: tuple[LinkageRecoveryReason, ...] = ()
    rollback_failures: tuple[LinkageRollbackFailure, ...] = ()

    @model_validator(mode="after")
    def validate_progress(self) -> HardwareTestEvidence:
        attempted = self.live_slave_write_attempted_at
        ack_unconfirmed = self.live_slave_ack_unconfirmed_at
        ack_failure_kind = self.live_slave_ack_failure_kind
        resolution_started = self.live_slave_ack_resolution_started_at
        resolution_updated = self.live_slave_ack_resolution_updated_at
        resolution_stage = self.live_slave_ack_resolution_stage
        resolution_state = self.live_slave_ack_resolution_state
        resolution_attempts = self.live_slave_ack_resolution_attempts
        adapter = self.live_slave_adapter_verified_at
        without_ack = self.live_slave_state_verified_without_ack_at
        full_state = self.live_slave_full_state_verified_at
        if attempted is not None and (
            self.active_entered_at is None or attempted < self.active_entered_at
        ):
            raise ValueError("live slave write attempt must follow ACTIVE entry")
        if adapter is not None and (attempted is None or adapter < attempted):
            raise ValueError("adapter verification must follow a live slave write attempt")
        if ack_unconfirmed is not None and (
            attempted is None or ack_unconfirmed < attempted
        ):
            raise ValueError("ACK loss must follow a live slave write attempt")
        if ack_failure_kind is not None and ack_unconfirmed is None:
            raise ValueError("an ACK failure kind requires recorded ACK loss")
        resolution_fields = (
            resolution_started,
            resolution_updated,
            resolution_stage,
            resolution_state,
            resolution_attempts,
        )
        if any(value is not None for value in resolution_fields):
            if any(value is None for value in resolution_fields):
                raise ValueError("ACK resolution progress must be complete")
            if ack_unconfirmed is None:
                raise ValueError("ACK resolution progress requires recorded ACK loss")
            assert resolution_started is not None
            assert resolution_updated is not None
            if resolution_started < ack_unconfirmed:
                raise ValueError("ACK resolution must follow recorded ACK loss")
            if resolution_updated < resolution_started:
                raise ValueError("ACK resolution update cannot precede its start")
        if adapter is not None and ack_unconfirmed is not None:
            raise ValueError("a live slave write cannot both confirm and lose its ACK")
        if without_ack is not None and (
            ack_unconfirmed is None or without_ack < ack_unconfirmed
        ):
            raise ValueError("ACK-less state verification must follow recorded ACK loss")
        if (
            without_ack is not None
            and resolution_state is not None
            and resolution_state is not ControlAckResolutionState.SUCCEEDED
        ):
            raise ValueError("ACK-less state verification requires successful resolution")
        ack_terminal_failures = {
            LinkageForwardFailureCategory.CONTROL_ACK_NOT_CONFIRMED,
            LinkageForwardFailureCategory.CONTROL_ACK_READBACK_UNAVAILABLE,
            LinkageForwardFailureCategory.CONTROL_ACK_QUARANTINE_FAILED,
            LinkageForwardFailureCategory.CONTROL_ACK_CONNECT_FAILED,
            LinkageForwardFailureCategory.CONTROL_ACK_AUTHENTICATE_FAILED,
            LinkageForwardFailureCategory.CONTROL_ACK_QUERY_FAILED,
            LinkageForwardFailureCategory.CONTROL_ACK_DECODE_FAILED,
            LinkageForwardFailureCategory.CONTROL_ACK_STATE_MISMATCH,
            LinkageForwardFailureCategory.CONTROL_ACK_POWER_MISMATCH,
        }
        if (
            adapter is not None or without_ack is not None
        ) and self.forward_failure in ack_terminal_failures:
            raise ValueError("verified live state cannot also have a terminal ACK failure")
        if full_state is not None and (attempted is None or full_state < attempted):
            raise ValueError("full-state verification must follow a live slave write attempt")
        if adapter is not None and full_state is not None and full_state < adapter:
            raise ValueError("full-state verification cannot precede adapter verification")
        if without_ack is not None and full_state is not None and full_state < without_ack:
            raise ValueError("full-state verification cannot precede ACK-less state verification")
        if self.verified_sample_count == 0:
            if self.first_verified_sample is not None or self.last_verified_sample is not None:
                raise ValueError("zero verified samples cannot include sample evidence")
            if full_state is not None:
                raise ValueError("full-state verification must count as a verified sample")
        else:
            if self.first_verified_sample is None or self.last_verified_sample is None:
                raise ValueError("verified samples require first and last evidence")
            if full_state is None:
                raise ValueError("verified samples require initial full-state verification")
            if self.first_verified_sample.verified_at != full_state:
                raise ValueError("first verified sample must be the full-state verification")
            if self.last_verified_sample.verified_at < self.first_verified_sample.verified_at:
                raise ValueError("last verified sample cannot precede the first sample")
        if self.rollback_completed_at is not None:
            if (
                self.rollback_started_at is None
                or self.rollback_completed_at < self.rollback_started_at
            ):
                raise ValueError("rollback completion must follow rollback start")
        if (
            self.rollback_failures or self.rollback_recovery_reasons
        ) and self.rollback_started_at is None:
            raise ValueError("rollback diagnostics require a rollback start timestamp")
        if len(set(self.rollback_recovery_reasons)) != len(
            self.rollback_recovery_reasons
        ):
            raise ValueError("rollback recovery reasons must not contain duplicates")
        return self


class HardwareTestScheduleImageDigest(BaseModel):
    """A byte-exact schedule binding without persisting the private 432-byte image."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9_.-]+$",
    )
    physical_binding: PhysicalDeviceBinding
    image_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class HardwareTestIntent(BaseModel):
    """Durable one-shot intent that prevents a service restart from replaying a test."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(default=1, ge=1, le=3)
    instance_id: str
    operation_id: str
    phase: HardwareTestIntentPhase
    confirmation_token: str
    spec: LinkageTestSpec
    snapshots: tuple[DeviceControlSnapshot, ...] = Field(min_length=2, max_length=2)
    created_at: datetime
    updated_at: datetime
    outcome: str | None = None
    primary_failure: HardwareTestPrimaryFailure | None = None
    evidence: HardwareTestEvidence | None = None
    schedule_flow_spec: ScheduleFlowExperimentSpec | None = None
    schedule_image_digests: tuple[HardwareTestScheduleImageDigest, ...] = Field(
        default=(),
        max_length=2,
    )
    schedule_flow_outcome: ScheduleFlowOutcome | None = None
    schedule_flow_sample: ScheduleLinkageSample | None = None
    schedule_transition_verified: bool | None = None
    stable_slave_tuple_observed: bool | None = None
    stable_observation_seconds: float | None = Field(default=None, ge=0, le=300)
    schedule_flow_stage_events: tuple[ScheduleFlowStageEvent, ...] = Field(
        default=(),
        max_length=SCHEDULE_FLOW_STAGE_EVENT_LIMIT,
    )

    @property
    def has_diagnostic_progress(self) -> bool:
        """Whether durable intent fields prove execution moved beyond an untouched preview."""

        return (
            self.outcome is not None
            or self.primary_failure is not None
            or self.schedule_flow_outcome is not None
            or self.schedule_flow_sample is not None
            or self.schedule_transition_verified is not None
            or self.stable_slave_tuple_observed is not None
            or self.stable_observation_seconds is not None
            or bool(self.schedule_flow_stage_events)
            or (
                self.version in {2, 3}
                and self.evidence is not None
                and self.evidence != HardwareTestEvidence()
            )
        )

    @model_validator(mode="after")
    def validate_versioned_evidence(self) -> HardwareTestIntent:
        if self.operation_id != self.spec.operation_id:
            raise ValueError("intent operation_id must match the confirmed test spec")
        expected_snapshot_ids = (
            self.spec.master_device_id,
            self.spec.slave_device_id,
        )
        if tuple(snapshot.device_id for snapshot in self.snapshots) != expected_snapshot_ids:
            # Preview tokens intentionally sort snapshots for deterministic v1/v2 compatibility.
            # The durable intent must nevertheless retain the controller's canonical master/slave
            # order: otherwise a token-preserving tuple reorder can authorize a fresh run whose
            # crash journal no longer compares equal to its owning intent.
            raise ValueError("intent snapshots must be ordered master then slave")
        if (
            self.created_at.tzinfo is None
            or self.created_at.utcoffset() is None
            or self.updated_at.tzinfo is None
            or self.updated_at.utcoffset() is None
            or self.updated_at < self.created_at
        ):
            raise ValueError("intent timestamps must be timezone-aware and monotonic")
        if self.version == 1 and self.evidence is not None:
            raise ValueError("version-one intents cannot contain diagnostic evidence")
        if self.version in {2, 3} and self.evidence is None:
            raise ValueError("version-two and version-three intents require diagnostic evidence")
        has_schedule_extension = bool(
            self.schedule_flow_spec is not None
            or self.schedule_image_digests
            or self.schedule_flow_outcome is not None
            or self.schedule_flow_sample is not None
            or self.schedule_transition_verified is not None
            or self.stable_slave_tuple_observed is not None
            or self.stable_observation_seconds is not None
            or self.schedule_flow_stage_events
        )
        if self.version < 3 and has_schedule_extension:
            raise ValueError("schedule-flow evidence requires a version-three intent")
        if self.outcome == "wire_qualified" and self.version != 3:
            raise ValueError("wire qualification requires a version-three intent")
        if self.version == 3:
            flow_spec = self.schedule_flow_spec
            if flow_spec is None or len(self.schedule_image_digests) != 2:
                raise ValueError("version-three intents require schedule-flow spec and digests")
            if self.spec != flow_spec.outer_linkage_spec():
                raise ValueError("schedule-flow intent and outer linkage spec disagree")
            expected_ids = (flow_spec.master_device_id, flow_spec.slave_device_id)
            if tuple(value.device_id for value in self.schedule_image_digests) != expected_ids:
                raise ValueError("schedule-flow digest order must match the selected pair")
            snapshots_by_id = {value.device_id: value for value in self.snapshots}
            if tuple(snapshots_by_id) != expected_ids:
                raise ValueError("schedule-flow snapshot order must match the selected pair")
            if any(
                digest.physical_binding != snapshots_by_id[digest.device_id].physical_binding
                for digest in self.schedule_image_digests
            ):
                raise ValueError("schedule-flow schedule and control bindings disagree")
            result_metadata = (
                self.schedule_flow_outcome,
                self.schedule_flow_sample,
                self.schedule_transition_verified,
                self.stable_slave_tuple_observed,
                self.stable_observation_seconds,
            )
            classified_outcomes = frozenset(outcome.value for outcome in ScheduleFlowOutcome)
            if flow_spec.sentinel_only:
                if any(value is not None for value in result_metadata):
                    raise ValueError(
                        "sentinel-only intents cannot contain field result metadata"
                    )
                if (
                    self.phase is HardwareTestIntentPhase.TERMINAL
                    and self.outcome in classified_outcomes
                ):
                    raise ValueError(
                        "sentinel-only intents cannot use a schedule-flow classification"
                    )
            previous_event: ScheduleFlowStageEvent | None = None
            previous_role_progress: ScheduleLinkageRunProgressEvent | None = None
            for event in self.schedule_flow_stage_events:
                if event.occurred_at < self.created_at:
                    raise ValueError("schedule-flow stage cannot precede the confirmed intent")
                if previous_event is not None:
                    if event.occurred_at < previous_event.occurred_at:
                        raise ValueError("schedule-flow stage timestamps must be monotonic")
                    current_rank = schedule_flow_stage_rank(event.stage)
                    previous_rank = schedule_flow_stage_rank(previous_event.stage)
                    if current_rank < previous_rank:
                        raise ValueError("schedule-flow stages must be monotonic")
                    if (
                        current_rank == previous_rank
                        and event.completed_participants is not None
                        and previous_event.completed_participants is not None
                        and event.completed_participants
                        < previous_event.completed_participants
                    ):
                        raise ValueError(
                            "schedule-flow participant progress must be monotonic"
                        )
                previous_event = event
                if event.role_progress is not None:
                    if previous_role_progress is not None and (
                        schedule_linkage_run_progress_rank(event.role_progress.kind)
                        < schedule_linkage_run_progress_rank(
                            previous_role_progress.kind
                        )
                    ):
                        raise ValueError("schedule-linkage role progress must be monotonic")
                    previous_role_progress = event.role_progress
            if len(self.schedule_flow_stage_events) > SCHEDULE_FLOW_PROGRESS_EVENT_LIMIT:
                terminal_event = self.schedule_flow_stage_events[-1]
                if (
                    terminal_event.stage is not ScheduleFlowStage.OUTER_RESTORED
                    or terminal_event.temporary_error_code is not None
                    or terminal_event.failure_category is not None
                ):
                    raise ValueError(
                        "the reserved schedule-flow event slot requires OUTER_RESTORED"
                    )
            if self.outcome == "wire_qualified":
                if self.phase is not HardwareTestIntentPhase.TERMINAL:
                    raise ValueError("wire qualification must be terminal")
                if not flow_spec.sentinel_only:
                    raise ValueError("wire qualification requires a sentinel-only spec")
                stages = tuple(event.stage for event in self.schedule_flow_stage_events)
                if any(
                    stage in _WIRE_QUALIFICATION_FORBIDDEN_STAGES for stage in stages
                ):
                    raise ValueError("wire qualification cannot contain field or role stages")
                if any(
                    event.temporary_error_code is not None
                    or event.failure_category is not None
                    for event in self.schedule_flow_stage_events
                ):
                    raise ValueError("wire qualification cannot contain failure evidence")
                cursor = -1
                for required_stage in _WIRE_QUALIFICATION_REQUIRED_STAGES:
                    try:
                        cursor = stages.index(required_stage, cursor + 1)
                    except ValueError:
                        raise ValueError(
                            "wire qualification lacks ordered durable stage evidence"
                        ) from None
                    required_event = self.schedule_flow_stage_events[cursor]
                    if required_stage in {
                        ScheduleFlowStage.SENTINEL_VERIFIED,
                        ScheduleFlowStage.SENTINEL_RESTORED,
                    } and required_event.completed_participants != 2:
                        raise ValueError(
                            "wire qualification requires both sentinel participants"
                        )
                if stages[-1] is not ScheduleFlowStage.OUTER_RESTORED:
                    raise ValueError("wire qualification must end with outer restoration")
            if self.schedule_flow_sample is not None:
                sample = self.schedule_flow_sample
                if (
                    sample.master_linkage is not LinkageRole.MASTER
                    or sample.slave_linkage is not LinkageRole.ASYNC_SLAVE
                ):
                    raise ValueError("schedule-flow sample has an invalid role topology")
            if self.schedule_flow_outcome is not None and self.schedule_flow_sample is None:
                raise ValueError("schedule-flow outcome requires durable sample evidence")
            classification_metadata = (
                self.schedule_transition_verified,
                self.stable_slave_tuple_observed,
                self.stable_observation_seconds,
            )
            if self.schedule_flow_outcome is None and any(
                value is not None for value in classification_metadata
            ):
                raise ValueError("schedule-flow result metadata requires an outcome")
            if self.schedule_flow_outcome is not None and any(
                value is None for value in classification_metadata
            ):
                raise ValueError("schedule-flow outcome requires complete result metadata")
            if self.schedule_flow_outcome is not None:
                sample = self.schedule_flow_sample
                if sample is None or sample.phase != "after":
                    raise ValueError("schedule-flow outcome requires an after-boundary sample")
                expected_outcome = classify_schedule_flow_sample(flow_spec, sample)
                if self.schedule_flow_outcome is not expected_outcome:
                    raise ValueError("schedule-flow outcome disagrees with durable sample")
                expected_transition = (
                    expected_outcome is ScheduleFlowOutcome.PER_SLOT_POWER_VERIFIED
                )
                if self.schedule_transition_verified is not expected_transition:
                    raise ValueError("schedule-flow transition flag disagrees with outcome")
                if self.stable_slave_tuple_observed is not True:
                    raise ValueError("schedule-flow outcome requires a stable slave tuple")
                if self.stable_observation_seconds != flow_spec.post_boundary_stability_seconds:
                    raise ValueError("schedule-flow stable duration disagrees with full spec")
            if (
                not flow_spec.sentinel_only
                and self.phase is HardwareTestIntentPhase.TERMINAL
                and self.outcome in classified_outcomes
                and (
                    self.schedule_flow_outcome is None
                    or self.outcome != self.schedule_flow_outcome.value
                )
            ):
                raise ValueError(
                    "terminal schedule-flow outcome disagrees with classified evidence"
                )
        evidence = self.evidence
        if evidence is None:
            return self
        if self.phase is HardwareTestIntentPhase.ARMED and self.has_diagnostic_progress:
            raise ValueError("armed intents cannot contain execution progress")
        has_live_evidence = any(
            value is not None
            for value in (
                evidence.live_slave_write_attempted_at,
                evidence.live_slave_ack_unconfirmed_at,
                evidence.live_slave_ack_failure_kind,
                evidence.live_slave_ack_resolution_started_at,
                evidence.live_slave_ack_resolution_updated_at,
                evidence.live_slave_ack_resolution_stage,
                evidence.live_slave_ack_resolution_state,
                evidence.live_slave_ack_resolution_attempts,
                evidence.live_slave_adapter_verified_at,
                evidence.live_slave_state_verified_without_ack_at,
                evidence.live_slave_full_state_verified_at,
                evidence.first_verified_sample,
                evidence.last_verified_sample,
            )
        ) or evidence.verified_sample_count > 0
        if self.spec.slave_power_after is None and has_live_evidence:
            raise ValueError("live-slave evidence requires a configured live power step")
        if self.spec.slave_power_after is not None:
            for sample in (
                evidence.first_verified_sample,
                evidence.last_verified_sample,
            ):
                if sample is None:
                    continue
                if (
                    sample.master_power != self.spec.master_power
                    or sample.slave_power != self.spec.slave_power_after
                    or sample.slave_linkage is not self.spec.slave_role
                ):
                    raise ValueError("verified sample does not match the confirmed test spec")
        return self


class JsonHardwareTestIntentStore:
    """Small atomic JSON store with a process-wide one-shot lease."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")

    def load(self) -> HardwareTestIntent | None:
        descriptor = _open_existing_private_file(
            self.path,
            label="hardware-test intent",
            allow_absent=True,
        )
        if descriptor is None:
            return None
        try:
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                descriptor = -1
                payload = stream.read(_MAX_SAFETY_ARTIFACT_BYTES + 1)
            if len(payload.encode()) > _MAX_SAFETY_ARTIFACT_BYTES:
                raise HardwareTestError("the hardware-test intent is too large")
            return HardwareTestIntent.model_validate_json(payload)
        except HardwareTestError:
            raise
        except (OSError, ValidationError, ValueError) as error:
            raise HardwareTestError(
                "the hardware-test intent is unreadable; refusing to proceed"
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def save(self, intent: HardwareTestIntent) -> None:
        temporary_path: Path | None = None
        try:
            # ``model_copy(update=...)`` deliberately skips Pydantic validation. Revalidate every
            # v3 durable successor because terminal experiment evidence is itself authorization
            # to clear all nested recovery journals. The legacy v1/v2 path has sub-second test
            # deadlines, so keep its durable ownership check constant-time instead of recursively
            # rebuilding every evidence model on each sample callback.
            if intent.version == 3:
                intent = HardwareTestIntent.model_validate(intent.model_dump(mode="python"))
            elif (
                intent.operation_id != intent.spec.operation_id
                or tuple(snapshot.device_id for snapshot in intent.snapshots)
                != (intent.spec.master_device_id, intent.spec.slave_device_id)
                or intent.created_at.tzinfo is None
                or intent.created_at.utcoffset() is None
                or intent.updated_at.tzinfo is None
                or intent.updated_at.utcoffset() is None
                or intent.updated_at < intent.created_at
            ):
                raise ValueError("hardware-test intent durable ownership is invalid")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            existing = _open_existing_private_file(
                self.path,
                label="hardware-test intent",
                allow_absent=True,
            )
            if existing is not None:
                os.close(existing)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(intent.model_dump_json(indent=2))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(self.path)
            self._fsync_parent()
        except (OSError, ValidationError, ValueError) as error:
            raise HardwareTestError("cannot persist the hardware-test one-shot intent") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    @contextmanager
    def lease(self) -> Iterator[None]:
        descriptor = -1
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not hasattr(os, "O_NOFOLLOW"):
                raise HardwareTestError("O_NOFOLLOW is required for hardware safety files")
            flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
            descriptor = os.open(self.lock_path, flags, 0o600)
            _validate_open_private_file(
                descriptor,
                self.lock_path,
                label="hardware-test one-shot lease",
            )
        except HardwareTestError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as error:
            if descriptor >= 0:
                os.close(descriptor)
            raise HardwareTestError("cannot open the hardware-test one-shot lease") from error
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise HardwareTestError(
                    "another hardware-test process is already running"
                ) from error
            _validate_open_private_file(
                descriptor,
                self.lock_path,
                label="hardware-test one-shot lease",
            )
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def _fsync_parent(self) -> None:
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


class _DiagnosticTemporaryLinkageController(TemporaryLinkageController):
    """Persist a typed live-change failure before the controller enters rollback.

    The core controller deliberately gives rollback failure precedence because an exact restore is
    the immediate safety concern.  The attended harness still needs a durable, redacted record of
    the forward diagnostic that triggered that rollback.  Recording it at the monitor boundary
    avoids depending on exception text or on a rollback exception chain that intentionally omits
    the earlier failure.
    """

    def __init__(
        self,
        devices: Mapping[str, JebaoDevice],
        store: LinkageJournalStore,
        *,
        safety_interlock: LinkageSafetyInterlock,
        on_primary_failure: Callable[[HardwareTestPrimaryFailure], None],
        on_diagnostic_event: Callable[[LinkageDiagnosticEvent], None],
    ) -> None:
        super().__init__(devices, store, safety_interlock=safety_interlock)
        self._on_primary_failure = on_primary_failure
        self._diagnostic_event_sink = on_diagnostic_event

    def _on_diagnostic_event(self, event: LinkageDiagnosticEvent) -> None:
        self._diagnostic_event_sink(event)

    async def _monitor_until_stop(
        self,
        record: LinkageTransactionRecord,
    ) -> tuple[LinkageStopReason, bool]:
        try:
            return await super()._monitor_until_stop(record)
        except LinkageLiveSlavePowerVerificationError:
            self._on_primary_failure(
                HardwareTestPrimaryFailure.SLAVE_POWER_CHANGE_NOT_VERIFIED
            )
            raise


class PhysicalDeviceLease:
    """Cross-instance, privacy-preserving lease over the exact two physical pumps."""

    def __init__(self, directory: Path, lock_keys: Sequence[str]) -> None:
        self.directory = directory
        self._lock_keys = tuple(sorted(lock_keys))

    @classmethod
    def from_selected(
        cls,
        config: AppConfig,
        selected: Mapping[str, DeviceConfig],
    ) -> PhysicalDeviceLease:
        lock_keys = tuple(_physical_lock_key(device) for device in selected.values())
        if len(lock_keys) != 2 or len(set(lock_keys)) != 2:
            raise HardwareTestError("selected stable physical identities must be distinct")
        return cls(canonical_hardware_lock_directory(config), lock_keys)

    @contextmanager
    def acquire(self) -> Iterator[None]:
        descriptors: list[int] = []
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            metadata = self.directory.lstat()
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or self.directory.is_symlink()
                or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o700
            ):
                raise HardwareTestError("physical-device safety lease directory is unsafe")
        except OSError as error:
            raise HardwareTestError("cannot open the physical-device safety lease") from error
        try:
            for lock_key in self._lock_keys:
                path = self.directory / f"{lock_key}.lock"
                descriptor = -1
                try:
                    if not hasattr(os, "O_NOFOLLOW"):
                        raise HardwareTestError(
                            "O_NOFOLLOW is required for hardware safety files"
                        )
                    flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
                    descriptor = os.open(path, flags, 0o600)
                    _validate_open_private_file(
                        descriptor,
                        path,
                        label="physical-device safety lease",
                    )
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    _validate_open_private_file(
                        descriptor,
                        path,
                        label="physical-device safety lease",
                    )
                except HardwareTestError:
                    if descriptor >= 0:
                        os.close(descriptor)
                    raise
                except BlockingIOError as error:
                    os.close(descriptor)
                    raise HardwareTestError(
                        "a selected physical device is owned by another hardware test"
                    ) from error
                except OSError as error:
                    if descriptor >= 0:
                        os.close(descriptor)
                    raise HardwareTestError(
                        "cannot open the physical-device safety lease"
                    ) from error
                descriptors.append(descriptor)
            yield
        finally:
            for descriptor in reversed(descriptors):
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)


class PersistentSafetyInterlock(DeploymentHardwareGuard):
    """In-memory core interlock additionally gated by a canonical persistent e-stop marker."""

    def __init__(self, latch_path: Path) -> None:
        super().__init__(latch_path=latch_path)


class ConfirmingLinkageJournalStore:
    """Reject a fresh controller snapshot unless it exactly matches the armed preview token."""

    def __init__(
        self,
        delegate: JsonLinkageJournalStore,
        *,
        instance_id: str,
        expected_token: str,
        qualification_store: JsonQualificationStore | None = None,
        before_clear: Callable[[], None] | None = None,
        before_load: Callable[[], None] = lambda: None,
        expected_loaded_record: LinkageTransactionRecord | None = None,
        require_loaded_record_match: bool = False,
        confirmation_token_factory: Callable[
            [str, LinkageTestSpec, Sequence[DeviceControlSnapshot]], str
        ]
        | None = None,
    ) -> None:
        self._delegate = delegate
        self._instance_id = instance_id
        self._expected_token = expected_token
        self._qualification_store = qualification_store
        self._before_clear = before_clear
        self._before_load = before_load
        self._expected_loaded_record = expected_loaded_record
        self._require_loaded_record_match = require_loaded_record_match
        self._confirmation_token_factory = confirmation_token_factory
        self.created_record: LinkageTransactionRecord | None = None

    def _assert_expected_record_unchanged(self) -> None:
        if (
            self._require_loaded_record_match
            and self._delegate.load() != self._expected_loaded_record
        ):
            raise ConfirmationMismatchError(
                "recovery journal changed after confirmation; no restore frame was sent"
            )

    def load(self) -> LinkageTransactionRecord | None:
        self._before_load()
        record = self._delegate.load()
        if self._require_loaded_record_match and record != self._expected_loaded_record:
            raise ConfirmationMismatchError(
                "recovery journal changed after confirmation; no restore frame was sent"
            )
        return record

    def lease(self):
        return self._delegate.lease()

    def create(self, record: LinkageTransactionRecord) -> None:
        token_factory = self._confirmation_token_factory or preview_confirmation_token
        actual = token_factory(self._instance_id, record.spec, record.snapshots)
        if not hmac.compare_digest(actual, self._expected_token):
            raise ConfirmationMismatchError(
                "device state changed after preflight; no control frame was sent"
            )
        if self._qualification_store is not None:
            _require_current_qualifications(self._qualification_store, record.snapshots)
        _assert_no_verification_conflict()
        self._delegate.create(record)
        self.created_record = record

    def save(self, record: LinkageTransactionRecord) -> None:
        # An attended recovery may need several bounded attempts. Accept only the exact
        # successor durably written through this wrapper; a journal changed by any other
        # writer still fails the next comparison against that successor.
        self._before_load()
        self._assert_expected_record_unchanged()
        try:
            self._delegate.save(record)
        except BaseException:
            # Atomic replace can complete before a later fsync/error is reported. Track the
            # successor only when the durable file is already byte-semantically that record,
            # then preserve the original failure for the bounded retry loop.
            if self._require_loaded_record_match and self._delegate.load() == record:
                self._expected_loaded_record = record
            raise
        if self._require_loaded_record_match:
            self._expected_loaded_record = record

    def clear(self) -> None:
        self._before_load()
        self._assert_expected_record_unchanged()
        if self._before_clear is not None:
            # Persist terminal intent before removing the only proof that writes happened.  A
            # STARTED intent without a journal then unambiguously means a pre-first-write crash.
            self._before_clear()
        self._delegate.clear()
        if self._require_loaded_record_match:
            self._expected_loaded_record = None


def canonical_journal_path(config: AppConfig) -> Path:
    del config
    return native_linkage_journal_path()


def canonical_intent_path(config: AppConfig) -> Path:
    del config
    return native_linkage_intent_path()


def canonical_safety_latch_path(config: AppConfig) -> Path:
    del config
    return emergency_stop_latch_path()


def canonical_hardware_lock_directory(config: AppConfig) -> Path:
    del config
    return physical_lock_directory()


def canonical_qualification_directory(config: AppConfig) -> Path:
    del config
    return qualification_directory()


def _require_current_qualifications(
    store: JsonQualificationStore,
    snapshots: Sequence[DeviceControlSnapshot],
) -> None:
    now = datetime.now(UTC)
    for snapshot in snapshots:
        receipt = store.load(snapshot.physical_binding)
        if receipt is None or not receipt.is_valid_for(snapshot.physical_binding, now=now):
            raise HardwareTestError(
                "both selected controllers require a current single-device qualification"
            )


def _assert_no_verification_conflict(
    *,
    allow_temporary_schedule: bool = False,
    allow_schedule_linkage_journal: bool = False,
) -> None:
    _assert_no_schedule_linkage_conflict(allow_journal=allow_schedule_linkage_journal)
    if not allow_temporary_schedule and os.path.lexists(temporary_schedule_journal_path()):
        raise HardwareTestError(
            "unfinished temporary schedule recovery blocks native linkage"
        )
    journal_path = verification_journal_path()
    if os.path.lexists(journal_path):
        if journal_path.is_symlink():
            raise HardwareTestError("device-verification recovery state is unsafe")
        raise HardwareTestError(
            "unfinished device verification exists; recover it before native linkage"
        )

    intent_path = verification_intent_path()
    if not os.path.lexists(intent_path):
        return
    if intent_path.is_symlink():
        raise HardwareTestError("device-verification intent is unsafe")
    descriptor = -1
    try:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(intent_path, flags)
        metadata = os.fstat(descriptor)
        current = os.stat(intent_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or metadata.st_uid != os.geteuid()
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or stat.S_IMODE(current.st_mode) != 0o600
            or metadata.st_nlink != 1
            or current.st_nlink != 1
            or (metadata.st_dev, metadata.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise HardwareTestError("device-verification intent has unsafe metadata")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            encoded = stream.read(_MAX_SAFETY_ARTIFACT_BYTES + 1)
        if len(encoded.encode()) > _MAX_SAFETY_ARTIFACT_BYTES:
            raise HardwareTestError("device-verification intent is too large")
        payload = json.loads(encoded)
    except HardwareTestError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise HardwareTestError("device-verification intent is unreadable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    required_fields = {
        "version",
        "instance_id",
        "operation_id",
        "device_id",
        "phase",
        "confirmation_token",
        "spec",
        "snapshot",
        "created_at",
        "updated_at",
        "outcome",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != required_fields
        or payload.get("version") != 1
        or payload.get("phase") != "terminal"
        or not isinstance(payload.get("outcome"), str)
        or not isinstance(payload.get("confirmation_token"), str)
        or not payload["confirmation_token"].startswith("JFV-")
    ):
        raise HardwareTestError(
            "nonterminal device verification exists; close it before native linkage"
        )


def _assert_no_schedule_linkage_conflict(*, allow_journal: bool = False) -> None:
    """Fail closed when the separate TimerON linkage-only workflow is unfinished."""

    journal_path = schedule_linkage_journal_path()
    if not allow_journal and os.path.lexists(journal_path):
        descriptor = _open_existing_private_file(
            journal_path,
            label="schedule-linkage recovery state",
            allow_absent=False,
        )
        if descriptor is not None:
            os.close(descriptor)
        raise HardwareTestError(
            "unfinished schedule-linkage operation blocks native linkage"
        )

    intent_path = schedule_linkage_intent_path()
    descriptor = _open_existing_private_file(
        intent_path,
        label="schedule-linkage intent",
        allow_absent=True,
    )
    if descriptor is None:
        return
    try:
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            encoded = stream.read(_MAX_SAFETY_ARTIFACT_BYTES + 1)
        if len(encoded.encode()) > _MAX_SAFETY_ARTIFACT_BYTES:
            raise HardwareTestError("schedule-linkage intent is too large")
        payload = json.loads(encoded)
    except (OSError, TypeError, ValueError) as error:
        raise HardwareTestError("schedule-linkage intent is unreadable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        validate_terminal_schedule_intent_payload(payload)
    except TerminalScheduleIntentError as error:
        raise HardwareTestError(
            "nonterminal schedule-linkage intent blocks native linkage"
        ) from error


def _physical_lock_key(config: DeviceConfig) -> str:
    identity = config.identity
    if identity is None or identity.device_id is None or identity.mac_address is None:
        raise HardwareTestError("selected devices require vendor ID and MAC identity selectors")
    binding = PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=identity.device_id,
        mac_address=identity.mac_address,
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        config_fingerprint=configuration_fingerprint({"scope": "native-linkage-hardware-lock-v1"}),
    )
    return physical_identity_key(binding)


def _safety_latch_present(path: Path) -> bool:
    # lexists is fail-closed for a broken symlink as well as a regular marker file.
    return os.path.lexists(path)


def activate_persistent_safety_latch(path: Path) -> None:
    """Atomically persist an attended e-stop marker; never clears an existing latch."""

    descriptor = -1
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, b"emergency_stop\n")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        parent_descriptor = os.open(path.parent, flags)
        try:
            os.fsync(parent_descriptor)
        finally:
            os.close(parent_descriptor)
    except OSError as error:
        raise HardwareTestError("cannot persist the emergency-stop safety latch") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def preview_confirmation_token(
    instance_id: str,
    spec: LinkageTestSpec,
    snapshots: Sequence[DeviceControlSnapshot],
) -> str:
    canonical = {
        "version": _TOKEN_VERSION,
        "instance_id": instance_id,
        "spec": spec.model_dump(mode="json"),
        "snapshots": [
            snapshot.model_dump(mode="json")
            for snapshot in sorted(snapshots, key=lambda value: value.device_id)
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return f"JFL-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def schedule_flow_confirmation_token(
    instance_id: str,
    spec: ScheduleFlowExperimentSpec,
    snapshots: Sequence[DeviceControlSnapshot],
    schedule_image_digests: Sequence[HardwareTestScheduleImageDigest],
) -> str:
    """Bind the attended experiment to controls, the full plan, and both exact images."""

    schedule_flow_spec = spec.model_dump(mode="json")
    if not spec.sentinel_only:
        # Preserve confirmation and recovery authority for v3 intents armed before the
        # sentinel-only field existed. A true value remains explicit and token-bound.
        schedule_flow_spec.pop("sentinel_only")
    canonical = {
        "version": 1,
        "instance_id": instance_id,
        "schedule_flow_spec": schedule_flow_spec,
        "snapshots": [
            snapshot.model_dump(mode="json")
            for snapshot in sorted(snapshots, key=lambda value: value.device_id)
        ],
        "schedule_image_digests": [
            digest.model_dump(mode="json")
            for digest in sorted(schedule_image_digests, key=lambda value: value.device_id)
        ],
    }
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return f"JFE-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def hardware_test_intent_confirmation_token(intent: HardwareTestIntent) -> str:
    """Return the authentic preview token for either legacy or schedule-flow intents."""

    if intent.version == 3:
        if intent.schedule_flow_spec is None:
            raise HardwareTestError("schedule-flow intent is incomplete")
        return schedule_flow_confirmation_token(
            intent.instance_id,
            intent.schedule_flow_spec,
            intent.snapshots,
            intent.schedule_image_digests,
        )
    return preview_confirmation_token(intent.instance_id, intent.spec, intent.snapshots)


def recovery_confirmation_token(
    instance_id: str,
    spec: LinkageTestSpec,
    snapshots: Sequence[DeviceControlSnapshot],
    revision: HardwareTestIntent | LinkageTransactionRecord,
) -> str:
    preview = preview_confirmation_token(instance_id, spec, snapshots)
    if isinstance(revision, LinkageTransactionRecord):
        revision_data = {
            "kind": "journal",
            "version": revision.version,
            "operation_id": revision.operation_id,
            "phase": revision.phase.value,
            "recovery_reason": (
                revision.recovery_reason.value if revision.recovery_reason is not None else None
            ),
            "error": revision.error,
            "created_at": revision.created_at.isoformat(),
            "updated_at": revision.updated_at.isoformat(),
            "expires_at": revision.expires_at.isoformat(),
            "failed_device_ids": list(revision.failed_device_ids),
            "restored_device_ids": list(revision.restored_device_ids),
            "rollback_failures": [
                failure.model_dump(mode="json") for failure in revision.rollback_failures
            ],
        }
    else:
        revision_data = {
            "kind": "intent",
            "version": revision.version,
            "operation_id": revision.operation_id,
            "phase": revision.phase.value,
            "created_at": revision.created_at.isoformat(),
            "updated_at": revision.updated_at.isoformat(),
            "outcome": revision.outcome,
            "primary_failure": (
                revision.primary_failure.value
                if revision.primary_failure is not None
                else None
            ),
        }
    canonical = json.dumps(revision_data, sort_keys=True, separators=(",", ":"))
    encoded = f"recover:{preview}:{canonical}".encode()
    return f"JFR-{hashlib.sha256(encoded).hexdigest()[:20].upper()}"


def _add_spec_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--master", required=True, help="configured logical master name")
    parser.add_argument("--slave", required=True, help="configured logical slave name")
    parser.add_argument(
        "--slave-role",
        required=True,
        choices=(LinkageRole.SYNC_SLAVE.value, LinkageRole.ASYNC_SLAVE.value),
    )
    parser.add_argument("--mode", required=True, choices=("constant", "pulse", "sine"))
    parser.add_argument("--master-power", required=True, type=int)
    parser.add_argument("--slave-power", required=True, type=int)
    parser.add_argument("--frequency", required=True, type=int)
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--verification-interval", type=float, default=1)
    parser.add_argument(
        "--bootstrap-active-schedule",
        action="store_true",
        help="journal, pause, qualify and restore an already-active local schedule",
    )
    parser.add_argument(
        "--slave-power-after",
        type=int,
        help="change only the active async slave to this power during monitoring",
    )
    parser.add_argument(
        "--power-change-after",
        type=float,
        help="seconds after ACTIVE before applying --slave-power-after",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jebao-flow-hwtest")
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight", help="read and arm an exact no-write preview")
    _add_spec_arguments(preflight)

    run = subparsers.add_parser(
        "run-native-linkage",
        help="execute one previously armed and confirmed native-linkage test",
    )
    _add_spec_arguments(run)
    run.add_argument("--confirm", required=True, help="token printed by preflight")

    recover = subparsers.add_parser(
        "recover-linkage",
        help="preview or confirm exact recovery of an unfinished one-shot test",
    )
    recovery_mode = recover.add_mutually_exclusive_group()
    recovery_mode.add_argument("--confirm", help="recovery token printed without this option")
    recovery_mode.add_argument(
        "--recovery-first",
        action="store_true",
        help="startup-safe automatic recovery when no persistent safety latch is active",
    )
    subparsers.add_parser("status", help="show sanitized one-shot and recovery state")
    return parser


def _spec_from_args(args: argparse.Namespace) -> LinkageTestSpec:
    spec = LinkageTestSpec(
        operation_id=args.operation_id,
        master_device_id=args.master,
        slave_device_id=args.slave,
        slave_role=LinkageRole(args.slave_role),
        mode=args.mode,
        master_power=args.master_power,
        slave_power=args.slave_power,
        frequency=args.frequency,
        duration_seconds=args.duration,
        verification_interval_seconds=args.verification_interval,
        bootstrap_active_schedule=args.bootstrap_active_schedule,
        slave_power_after=args.slave_power_after,
        power_change_after_seconds=args.power_change_after,
    )
    requested_powers = [spec.master_power, spec.slave_power]
    if spec.slave_power_after is not None:
        requested_powers.append(spec.slave_power_after)
    if max(requested_powers) > _MAX_ATTENDED_POWER:
        raise HardwareTestError(f"attended linkage targets are capped at {_MAX_ATTENDED_POWER}%")
    duration_cap = (
        _MAX_SCHEDULE_BOOTSTRAP_DURATION_SECONDS
        if spec.bootstrap_active_schedule
        else _MAX_ATTENDED_DURATION_SECONDS
    )
    if spec.duration_seconds > duration_cap:
        raise HardwareTestError(
            f"attended linkage tests are capped at {duration_cap} seconds"
        )
    return spec


def _validate_config(
    config: AppConfig,
    selected_ids: frozenset[str],
) -> dict[str, DeviceConfig]:
    if config.runtime.mode is not RuntimeMode.CONTROL:
        raise HardwareTestError("hardware test requires runtime.mode=control")
    if config.runtime.dry_run:
        raise HardwareTestError("hardware test requires runtime.dry_run=false")
    if config.observer.enabled:
        raise HardwareTestError("hardware test requires observer.enabled=false")
    if len(selected_ids) != 2:
        raise HardwareTestError("hardware test requires exactly two distinct devices")

    by_id = {device.id: device for device in config.devices}
    if not selected_ids.issubset(by_id):
        raise HardwareTestError("selected devices are not present in the private configuration")
    write_enabled = {device.id for device in config.devices if device.control.allow_hardware_writes}
    if write_enabled != selected_ids:
        raise HardwareTestError(
            "hardware writes must be enabled for exactly the selected two devices"
        )

    selected = {device_id: by_id[device_id] for device_id in selected_ids}
    for device in selected.values():
        if not device.enabled or device.type is not DeviceType.WAVEMAKER:
            raise HardwareTestError("selected devices must be enabled wavemakers")
        if (
            device.identity is None
            or device.identity.device_id is None
            or device.identity.mac_address is None
        ):
            raise HardwareTestError(
                "selected devices require both vendor ID and MAC identity selectors"
            )
        if device.product_key is not None and device.product_key != LOCAL_WAVEMAKER_PRO.product_key:
            raise HardwareTestError("selected devices must be Local Wavemaker Pro controllers")
        control = device.control
        if control.minimum_command_interval_ms > _MAX_ATTENDED_COMMAND_INTERVAL_MS:
            raise HardwareTestError("hardware-test command interval exceeds the audited maximum")
        if control.readback_delay_ms > _MAX_ATTENDED_READBACK_DELAY_MS:
            raise HardwareTestError("hardware-test read-back delay exceeds the audited maximum")
        if control.readback_attempts > _MAX_ATTENDED_READBACK_ATTEMPTS:
            raise HardwareTestError("hardware-test read-back attempts exceed the audited maximum")
    if config.observer.discovery_timeout_seconds > _MAX_ATTENDED_DISCOVERY_TIMEOUT_SECONDS:
        raise HardwareTestError("hardware-test discovery timeout exceeds the audited maximum")
    if not config.runtime.state_path.is_absolute():
        raise HardwareTestError("runtime.state_path must be an absolute persistent path")
    return selected


async def _resolve_selected(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
) -> dict[str, ResolvedDevice]:
    discovery = GizwitsDiscovery(
        targets=config.observer.targets,
        bind_address=config.observer.bind_address,
    )
    try:
        discovered = await discovery.discover(
            timeout_seconds=config.observer.discovery_timeout_seconds
        )
    except Exception as error:
        raise HardwareTestError("stable-identity discovery failed") from error
    resolved = resolve_device_bindings(tuple(selected.values()), discovered)
    if set(resolved) != set(selected):
        raise HardwareTestError("the selected stable identities did not resolve uniquely")
    if any(
        endpoint.product_key != LOCAL_WAVEMAKER_PRO.product_key for endpoint in resolved.values()
    ):
        raise HardwareTestError("both selected devices must resolve as Local Wavemaker Pro")
    if len({endpoint.address for endpoint in resolved.values()}) != 2:
        raise HardwareTestError("the selected identities resolved to the same endpoint")
    return resolved


async def _build_devices(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
    *,
    writable: bool,
) -> dict[str, JebaoDevice]:
    resolved = await _resolve_selected(config, selected)
    devices: dict[str, JebaoDevice] = {}
    for device_id, device_config in selected.items():
        endpoint = resolved[device_id]
        if writable:
            resolved_values = device_config.model_dump(mode="python")
            resolved_values.update(
                {
                    "address": endpoint.address,
                    "product_key": endpoint.product_key,
                    "discovery": None,
                }
            )
            # Re-validate the resolved location instead of bypassing DeviceConfig validators via
            # model_copy(update=...).  This is the final config object allowed to create a writer.
            resolved_config = DeviceConfig.model_validate(resolved_values)
            devices[device_id] = create_lan_device(resolved_config, config.runtime)
        else:
            devices[device_id] = create_read_only_lan_device(
                device_config,
                endpoint.address,
                endpoint.product_key,
            )
    return devices


@asynccontextmanager
async def _connected(devices: Mapping[str, JebaoDevice]):
    connected: list[JebaoDevice] = []
    try:
        for device in devices.values():
            await device.connect()
            connected.append(device)
        yield
    finally:
        for device in reversed(connected):
            try:
                await device.disconnect()
            except Exception:
                pass


def _safe_power(device: JebaoDevice) -> int:
    capabilities = device.capabilities
    minimum = capabilities.power_limits.min_power
    step = capabilities.power_step
    safe = ((minimum + step - 1) // step) * step
    if safe > min(capabilities.power_limits.max_power, _MAX_ATTENDED_POWER):
        raise HardwareTestError("a selected device has no safe attended-test power")
    return safe


async def _capture_preview(
    devices: Mapping[str, JebaoDevice],
    spec: LinkageTestSpec,
) -> tuple[DeviceControlSnapshot, ...]:
    roles = {
        spec.master_device_id: LinkageRole.MASTER,
        spec.slave_device_id: spec.slave_role,
    }
    powers = {
        spec.master_device_id: spec.master_power,
        spec.slave_device_id: spec.slave_power,
    }
    snapshots: list[DeviceControlSnapshot] = []
    for device_id in (spec.master_device_id, spec.slave_device_id):
        device = devices[device_id]
        state = await device.get_state()
        if not state.online or state.error:
            raise HardwareTestError("both selected devices must be online and error-free")
        if spec.bootstrap_active_schedule and state.timer_enabled is not True:
            raise HardwareTestError(
                "schedule bootstrap requires TimerON with a decoded active schedule"
            )
        if not spec.bootstrap_active_schedule and state.timer_enabled is not False:
            raise HardwareTestError(
                "disable TimerON in the vendor app before attended hardware testing"
            )
        physical_binding = device.physical_binding
        if physical_binding is None:
            raise HardwareTestError("a selected device has no exact stable physical binding")
        snapshot = DeviceControlSnapshot.from_state(
            device_id,
            state,
            physical_binding=physical_binding,
        )
        if not spec.bootstrap_active_schedule and snapshot.mode not in _AUDITED_SNAPSHOT_MODES:
            raise HardwareTestError("current mode is outside the audited exact-restore modes")
        if not spec.bootstrap_active_schedule and snapshot.power > _MAX_ATTENDED_POWER:
            raise HardwareTestError(
                f"current outputs must be at or below {_MAX_ATTENDED_POWER}% before preflight"
            )
        snapshots.append(snapshot)

        preview_target = getattr(device, "preview_target", None)
        if callable(preview_target):
            if spec.bootstrap_active_schedule:
                qualification, stepped = (
                    TemporaryLinkageController._bootstrap_qualification_levels(device)
                )
                qualification_target = DeviceTarget(
                    enabled=True,
                    power=qualification,
                    mode="constant",
                    frequency=spec.frequency,
                    linkage=LinkageRole.INDEPENDENT,
                    timer_enabled=False,
                )
                preview_target(qualification_target)
                preview_target(qualification_target.model_copy(update={"power": stepped}))
                preview_target(qualification_target)
            else:
                preview_target(
                    DeviceTarget(
                        enabled=True,
                        power=_safe_power(device),
                        mode="constant",
                        frequency=spec.frequency,
                        linkage=LinkageRole.INDEPENDENT,
                        timer_enabled=False,
                    )
                )
            preview_target(
                DeviceTarget(
                    enabled=True,
                    power=powers[device_id],
                    mode=spec.mode,
                    frequency=spec.frequency,
                    linkage=roles[device_id],
                    timer_enabled=False,
                )
            )
            if device_id == spec.slave_device_id and spec.slave_power_after is not None:
                preview_target(
                    DeviceTarget(
                        enabled=True,
                        power=spec.slave_power_after,
                        mode=spec.mode,
                        frequency=spec.frequency,
                        linkage=spec.slave_role,
                        timer_enabled=False,
                    )
                )
            preview_target(
                DeviceTarget(
                    enabled=True,
                    power=_safe_power(device),
                    mode="constant",
                    frequency=spec.frequency,
                    linkage=LinkageRole.INDEPENDENT,
                    timer_enabled=False,
                )
            )
            preview_target(
                DeviceTarget(
                    enabled=snapshot.enabled,
                    power=snapshot.power,
                    mode=snapshot.mode,
                    frequency=snapshot.frequency,
                    linkage=snapshot.linkage,
                    timer_enabled=snapshot.timer_enabled,
                )
            )
    return tuple(snapshots)


def _print_preview(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
    spec: LinkageTestSpec,
    snapshots: Sequence[DeviceControlSnapshot],
    token: str,
) -> None:
    by_id = {snapshot.device_id: snapshot for snapshot in snapshots}
    print("Native-linkage preflight passed; no control frame was sent.")
    for label, device_id, target_power in (
        ("Master", spec.master_device_id, spec.master_power),
        ("Slave", spec.slave_device_id, spec.slave_power),
    ):
        snapshot = by_id[device_id]
        print(f"{label}: {selected[device_id].name}")
        print(
            "  current="
            f"{snapshot.mode}/{snapshot.power}% timer={'on' if snapshot.timer_enabled else 'off'}; "
            f"test={spec.mode}/{target_power}%"
        )
    print(f"Duration: {spec.duration_seconds:g}s")
    if spec.bootstrap_active_schedule:
        print("Schedule bootstrap: active TimerON will be paused and exactly restored.")
    if spec.slave_power_after is not None:
        print(
            "Async slave live power check: "
            f"{spec.slave_power}% -> {spec.slave_power_after}% after "
            f"{spec.power_change_after_seconds:g}s"
        )
    print(f"Confirmation token: {token}")
    print(f"Journal directory: {canonical_journal_path(config).parent}")


def _updated_intent(
    intent: HardwareTestIntent,
    phase: HardwareTestIntentPhase,
    outcome: str | None,
) -> HardwareTestIntent:
    updated_at = max(datetime.now(UTC), intent.created_at, intent.updated_at)
    return intent.model_copy(
        update={
            "phase": phase,
            "updated_at": updated_at,
            "outcome": outcome,
        }
    )


def _evidence_after_event(
    evidence: HardwareTestEvidence,
    event: LinkageDiagnosticEvent,
    spec: LinkageTestSpec,
) -> HardwareTestEvidence:
    """Apply one controller event monotonically without persisting raw adapter data."""

    update: dict[str, object] = {}
    if event.kind is LinkageDiagnosticEventKind.ACTIVE_ENTERED:
        if evidence.active_entered_at is None:
            update["active_entered_at"] = event.occurred_at
    elif event.kind is LinkageDiagnosticEventKind.LIVE_SLAVE_WRITE_ATTEMPTED:
        if evidence.live_slave_write_attempted_at is None:
            update["live_slave_write_attempted_at"] = event.occurred_at
    elif event.kind is LinkageDiagnosticEventKind.LIVE_SLAVE_ACK_UNCONFIRMED:
        if evidence.live_slave_ack_unconfirmed_at is None:
            update["live_slave_ack_unconfirmed_at"] = event.occurred_at
        if evidence.live_slave_ack_failure_kind is None:
            update["live_slave_ack_failure_kind"] = event.ack_failure_kind
    elif event.kind is LinkageDiagnosticEventKind.LIVE_SLAVE_ACK_RESOLUTION:
        if evidence.live_slave_ack_unconfirmed_at is None:
            raise HardwareTestError("ACK resolution has no recorded ACK loss")
        if event.ack_resolution_attempt is None:
            raise AssertionError("validated ACK-resolution event has no attempt")
        if evidence.live_slave_ack_resolution_started_at is None:
            update["live_slave_ack_resolution_started_at"] = event.occurred_at
        update.update(
            {
                "live_slave_ack_resolution_updated_at": event.occurred_at,
                "live_slave_ack_resolution_stage": event.ack_resolution_stage,
                "live_slave_ack_resolution_state": event.ack_resolution_state,
                "live_slave_ack_resolution_attempts": max(
                    evidence.live_slave_ack_resolution_attempts or 0,
                    event.ack_resolution_attempt,
                ),
            }
        )
    elif event.kind is LinkageDiagnosticEventKind.LIVE_SLAVE_ADAPTER_VERIFIED:
        if evidence.live_slave_adapter_verified_at is None:
            update["live_slave_adapter_verified_at"] = event.occurred_at
    elif event.kind is LinkageDiagnosticEventKind.LIVE_SLAVE_STATE_VERIFIED_WITHOUT_ACK:
        if evidence.live_slave_state_verified_without_ack_at is None:
            update["live_slave_state_verified_without_ack_at"] = event.occurred_at
    elif event.kind in {
        LinkageDiagnosticEventKind.LIVE_SLAVE_FULL_STATE_VERIFIED,
        LinkageDiagnosticEventKind.LIVE_SLAVE_SAMPLE_VERIFIED,
    }:
        if spec.slave_power_after is None:
            raise HardwareTestError("live-slave evidence has no configured target")
        sample = HardwareTestVerifiedSample(
            verified_at=event.occurred_at,
            master_power=spec.master_power,
            slave_power=spec.slave_power_after,
            slave_linkage=spec.slave_role,
        )
        update["verified_sample_count"] = evidence.verified_sample_count + 1
        update["last_verified_sample"] = sample
        if evidence.first_verified_sample is None:
            update["first_verified_sample"] = sample
        if evidence.live_slave_full_state_verified_at is None:
            update["live_slave_full_state_verified_at"] = event.occurred_at
    elif event.kind is LinkageDiagnosticEventKind.FORWARD_FAILED:
        if evidence.forward_failure is None:
            update["forward_failure"] = event.forward_failure
    elif event.kind is LinkageDiagnosticEventKind.ROLLBACK_STARTED:
        if evidence.rollback_started_at is None:
            update["rollback_started_at"] = event.occurred_at
    else:  # pragma: no cover - enum exhaustiveness guard
        raise AssertionError(f"unsupported diagnostic event: {event.kind}")
    if not update:
        return evidence
    payload = evidence.model_dump(mode="python")
    payload.update(update)
    return HardwareTestEvidence.model_validate(payload)


def _evidence_with_rollback_failures(
    evidence: HardwareTestEvidence,
    record: LinkageTransactionRecord,
    *,
    observed_at: datetime | None = None,
) -> HardwareTestEvidence:
    if not record.rollback_failures and record.recovery_reason is None:
        return evidence
    existing = {
        (failure.participant, failure.stage, failure.category)
        for failure in evidence.rollback_failures
    }
    merged = list(evidence.rollback_failures)
    for failure in record.rollback_failures:
        key = (failure.participant, failure.stage, failure.category)
        if key not in existing:
            existing.add(key)
            merged.append(failure)
    payload = evidence.model_dump(mode="python")
    reasons = list(evidence.rollback_recovery_reasons)
    if record.recovery_reason is not None and record.recovery_reason not in reasons:
        reasons.append(record.recovery_reason)
    payload.update(
        {
            "rollback_started_at": evidence.rollback_started_at
            or observed_at
            or record.updated_at,
            "rollback_recovery_reasons": tuple(reasons),
            "rollback_failures": tuple(merged),
        }
    )
    return HardwareTestEvidence.model_validate(payload)


def _evidence_with_rollback_completed(
    evidence: HardwareTestEvidence,
    *,
    completed_at: datetime,
) -> HardwareTestEvidence:
    if evidence.rollback_started_at is None:
        # A PREPARED journal may be cleared before the first physical write. Do not describe
        # that no-compensation closure as a completed rollback.
        return evidence
    payload = evidence.model_dump(mode="python")
    payload.update(
        {
            "rollback_completed_at": completed_at,
        }
    )
    return HardwareTestEvidence.model_validate(payload)


async def _preflight(
    config: AppConfig,
    spec: LinkageTestSpec,
    intent_store: JsonHardwareTestIntentStore,
    journal_store: JsonLinkageJournalStore,
    qualification_store: JsonQualificationStore,
) -> int:
    _assert_no_verification_conflict()
    selected_ids = frozenset({spec.master_device_id, spec.slave_device_id})
    selected = _validate_config(config, selected_ids)
    with PhysicalDeviceLease.from_selected(config, selected).acquire():
        if _safety_latch_present(canonical_safety_latch_path(config)):
            raise HardwareTestError("persistent safety latch is active")
        if journal_store.load() is not None:
            raise HardwareTestError("unfinished linkage recovery exists; run recover-linkage")
        existing = intent_store.load()
        if existing is not None:
            if (
                existing.instance_id != config.instance.id
                and existing.phase is not HardwareTestIntentPhase.TERMINAL
            ):
                raise HardwareTestError(
                    "another instance owns the deployment-wide hardware-test intent"
                )
            if existing.phase in {
                HardwareTestIntentPhase.STARTED,
                HardwareTestIntentPhase.RECOVERY_REQUIRED,
            }:
                raise HardwareTestError("unfinished one-shot intent requires recover-linkage")
            if (
                existing.operation_id != spec.operation_id
                and existing.phase is not HardwareTestIntentPhase.TERMINAL
            ):
                raise HardwareTestError("another preflight is already armed")
            if (
                existing.operation_id == spec.operation_id
                and existing.phase is HardwareTestIntentPhase.TERMINAL
            ):
                raise HardwareTestError(
                    "terminal operation IDs cannot be replayed; choose a new ID"
                )

        devices = await _build_devices(config, selected, writable=False)
        async with _connected(devices):
            snapshots = await _capture_preview(devices, spec)
        if not spec.bootstrap_active_schedule:
            _require_current_qualifications(qualification_store, snapshots)
        token = preview_confirmation_token(config.instance.id, spec, snapshots)
        now = datetime.now(UTC)
        intent_store.save(
            HardwareTestIntent(
                version=2,
                instance_id=config.instance.id,
                operation_id=spec.operation_id,
                phase=HardwareTestIntentPhase.ARMED,
                confirmation_token=token,
                spec=spec,
                snapshots=snapshots,
                created_at=existing.created_at if existing is not None else now,
                updated_at=now,
                evidence=HardwareTestEvidence(),
            )
        )
    _print_preview(config, selected, spec, snapshots, token)
    return 0


async def _run_with_sigint(
    controller: TemporaryLinkageController,
    spec: LinkageTestSpec,
    *,
    interrupt_event: asyncio.Event | None = None,
    emergency_event: asyncio.Event | None = None,
    safety_interlock: LinkageSafetyInterlock | None = None,
    safety_latch_path: Path | None = None,
    late_emergency_cleanup: Callable[[], Awaitable[None]] | None = None,
) -> Any:
    loop = asyncio.get_running_loop()
    local_event = interrupt_event or asyncio.Event()
    installed_handlers: list[signal.Signals] = []
    signal_count = 0
    latch_errors: list[HardwareTestError] = []
    emergency_requested = False

    def emergency_stop() -> None:
        nonlocal emergency_requested
        emergency_requested = True
        if safety_interlock is None or safety_latch_path is None:
            return
        try:
            activate_persistent_safety_latch(safety_latch_path)
        except HardwareTestError as error:
            latch_errors.append(error)
        finally:
            # Even if durable storage failed, stop normal ON-state rollback in this process.
            safety_interlock.trip()

    def handle_stop_signal() -> None:
        nonlocal signal_count
        signal_count += 1
        if signal_count == 1:
            local_event.set()
        else:
            emergency_stop()

    if interrupt_event is None:
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(stop_signal, handle_stop_signal)
                installed_handlers.append(stop_signal)
            except (NotImplementedError, RuntimeError):  # pragma: no cover - platform fallback
                break

    async def monitor_emergency_event() -> None:
        if emergency_event is not None:
            await emergency_event.wait()
            emergency_stop()

    run_task = asyncio.create_task(controller.run(spec), name="native-linkage-hardware-test")
    signal_task = asyncio.create_task(local_event.wait(), name="native-linkage-stop-signal")
    emergency_task = asyncio.create_task(
        monitor_emergency_event(),
        name="native-linkage-emergency-signal",
    )
    try:
        done, _ = await asyncio.wait(
            {run_task, signal_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if signal_task in done and not run_task.done():
            while (  # noqa: ASYNC110 - controller exposes state, not an activation event
                controller.active_operation_id is None and not run_task.done()
            ):
                await asyncio.sleep(0)
            if not run_task.done():
                await controller.stop(spec.operation_id)
        return await run_task
    finally:
        # Stop accepting harness-owned signals before the final emergency check. Any callback
        # already queued gets a chance to run at the gather await below; a late callback can no
        # longer appear after cleanup and leave an ON device behind a newly-created latch.
        for stop_signal in installed_handlers:
            loop.remove_signal_handler(stop_signal)
        if emergency_event is not None and emergency_event.is_set() and not emergency_requested:
            emergency_stop()
        for waiter in (signal_task, emergency_task):
            if not waiter.done():
                waiter.cancel()
        await asyncio.gather(signal_task, emergency_task, return_exceptions=True)
        if safety_latch_path is not None and _safety_latch_present(safety_latch_path):
            emergency_requested = True
        if emergency_requested and late_emergency_cleanup is not None:
            async with asyncio.timeout(_LATE_EMERGENCY_STOP_TIMEOUT_SECONDS):
                await late_emergency_cleanup()
        if (
            latch_errors
            and safety_latch_path is not None
            and not _safety_latch_present(safety_latch_path)
        ):
            raise latch_errors[0]


async def _run_native_linkage(
    config: AppConfig,
    spec: LinkageTestSpec,
    confirmation: str,
    intent_store: JsonHardwareTestIntentStore,
    journal_store: JsonLinkageJournalStore,
    qualification_store: JsonQualificationStore,
    interlock: PersistentSafetyInterlock,
) -> int:
    _assert_no_verification_conflict()
    selected = _validate_config(
        config,
        frozenset({spec.master_device_id, spec.slave_device_id}),
    )
    with PhysicalDeviceLease.from_selected(config, selected).acquire():
        if _safety_latch_present(canonical_safety_latch_path(config)):
            raise HardwareTestError("persistent safety latch is active")
        if journal_store.load() is not None:
            raise HardwareTestError("unfinished linkage recovery exists; run recover-linkage")
        intent = intent_store.load()
        if intent is None or intent.phase is not HardwareTestIntentPhase.ARMED:
            raise HardwareTestError("run requires an armed preflight")
        if intent.version == 3:
            raise HardwareTestError(
                "schedule-flow intents require jebao-flow-schedule-flow-test"
            )
        if intent.instance_id != config.instance.id or intent.spec != spec:
            raise HardwareTestError("run arguments do not match the armed preflight")
        if not hmac.compare_digest(confirmation, intent.confirmation_token):
            raise ConfirmationMismatchError(
                "confirmation token does not match; no control frame was sent"
            )
        if intent.version != 2 or intent.evidence is None:
            raise HardwareTestError(
                "legacy armed preflight has no durable diagnostics; cancel it and preflight "
                "again before any control frame is sent"
            )

        def persist_intent_successor(successor: HardwareTestIntent) -> None:
            """Track an atomic replace even when a trailing fsync reports an error."""

            nonlocal intent
            try:
                intent_store.save(successor)
            except BaseException:
                try:
                    persisted = intent_store.load()
                except BaseException:
                    raise
                if persisted == successor:
                    intent = successor
                raise
            intent = successor

        devices = await _build_devices(config, selected, writable=True)
        persist_intent_successor(
            _updated_intent(intent, HardwareTestIntentPhase.STARTED, None)
        )

        def persist_primary_failure(failure: HardwareTestPrimaryFailure) -> None:
            if intent.primary_failure is not None:
                return
            persist_intent_successor(
                intent.model_copy(
                    update={
                        "primary_failure": failure,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )

        def persist_diagnostic_event(event: LinkageDiagnosticEvent) -> None:
            evidence = intent.evidence
            if evidence is None:
                raise HardwareTestError("diagnostic intent evidence is unavailable")
            successor_evidence = _evidence_after_event(evidence, event, spec)
            if successor_evidence == evidence:
                return
            persist_intent_successor(
                intent.model_copy(
                    update={
                        "evidence": successor_evidence,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )

        def mark_terminal_before_clear() -> None:
            evidence = intent.evidence
            if evidence is None:
                raise HardwareTestError("diagnostic intent evidence is unavailable")
            completed_at = datetime.now(UTC)
            successor = _updated_intent(
                intent.model_copy(
                    update={
                        "evidence": _evidence_with_rollback_completed(
                            evidence,
                            completed_at=completed_at,
                        )
                    }
                ),
                HardwareTestIntentPhase.TERMINAL,
                "restored",
            )
            persist_intent_successor(successor)

        confirming_store = ConfirmingLinkageJournalStore(
            journal_store,
            instance_id=config.instance.id,
            expected_token=intent.confirmation_token,
            qualification_store=(
                None if spec.bootstrap_active_schedule else qualification_store
            ),
            before_clear=mark_terminal_before_clear,
        )
        controller = _DiagnosticTemporaryLinkageController(
            devices,
            confirming_store,
            safety_interlock=interlock,
            on_primary_failure=persist_primary_failure,
            on_diagnostic_event=persist_diagnostic_event,
        )
        fallback_now = datetime.now(UTC)
        fallback_record = LinkageTransactionRecord(
            operation_id=intent.operation_id,
            phase=LinkageTransactionPhase.PREPARED,
            spec=intent.spec,
            snapshots=intent.snapshots,
            created_at=fallback_now,
            updated_at=fallback_now,
            expires_at=fallback_now + timedelta(seconds=intent.spec.duration_seconds),
        )

        async def enforce_late_emergency_stop() -> None:
            await controller.enforce_safety_stop(fallback_record)

        async with _connected(devices):
            interlock.clear()
            try:
                result = await _run_with_sigint(
                    controller,
                    spec,
                    safety_interlock=interlock,
                    safety_latch_path=canonical_safety_latch_path(config),
                    late_emergency_cleanup=enforce_late_emergency_stop,
                )
            except BaseException:
                pending = journal_store.load()
                current_intent = intent_store.load()
                if (
                    current_intent is not None
                    and current_intent.instance_id == config.instance.id
                    and current_intent.operation_id == spec.operation_id
                ):
                    intent = current_intent
                if pending is not None:
                    evidence = intent.evidence
                    if evidence is None:
                        raise HardwareTestError(
                            "diagnostic intent evidence is unavailable"
                        ) from None
                    persist_intent_successor(
                        _updated_intent(
                            intent.model_copy(
                                update={
                                    "evidence": _evidence_with_rollback_failures(
                                        evidence,
                                        pending,
                                    )
                                }
                            ),
                            HardwareTestIntentPhase.RECOVERY_REQUIRED,
                            "recovery_required",
                        )
                    )
                elif (
                    current_intent is None
                    or current_intent.phase is not HardwareTestIntentPhase.TERMINAL
                ):
                    persist_intent_successor(
                        _updated_intent(
                            intent,
                            HardwareTestIntentPhase.TERMINAL,
                            "stopped_before_first_write",
                        )
                    )
                raise
            finally:
                interlock.trip()

        if journal_store.load() is not None:
            intent_store.save(
                _updated_intent(
                    intent,
                    HardwareTestIntentPhase.RECOVERY_REQUIRED,
                    "recovery_required",
                )
            )
            raise HardwareTestError("linkage journal remains after run; recovery is required")
        current_intent = intent_store.load()
        if current_intent is None or current_intent.phase is not HardwareTestIntentPhase.TERMINAL:
            # This is normally already durable via the journal wrapper's before-clear hook.
            intent_store.save(_updated_intent(intent, HardwareTestIntentPhase.TERMINAL, "restored"))
        else:
            intent = current_intent
        if spec.bootstrap_active_schedule:
            created = confirming_store.created_record
            if created is None or created.snapshots != intent.snapshots:
                raise HardwareTestError("schedule-bootstrap qualification snapshot is unavailable")
            expected_qualified = {snapshot.device_id for snapshot in created.snapshots}
            if set(result.bootstrap_qualified_device_ids) != expected_qualified:
                raise HardwareTestError(
                    "schedule-bootstrap qualification did not complete for both devices"
                )
            if (
                spec.slave_power_after is not None
                and result.slave_power_change_verified is not True
            ):
                persist_primary_failure(
                    HardwareTestPrimaryFailure.SLAVE_POWER_CHANGE_NOT_VERIFIED
                )
                raise HardwareTestError(
                    "async slave live power change was not verified; "
                    "no qualification receipts were issued"
                )
            for snapshot in created.snapshots:
                qualification_power, stepped_power = (
                    TemporaryLinkageController._bootstrap_qualification_levels(
                        devices[snapshot.device_id]
                    )
                )
                qualification_store.save(
                    DeviceQualificationReceipt(
                        operation_id=spec.operation_id,
                        device_id=snapshot.device_id,
                        physical_binding=snapshot.physical_binding,
                        original_power=qualification_power,
                        step_power=stepped_power,
                        completed_at=result.completed_at,
                        valid_until=result.completed_at + timedelta(hours=24),
                    )
                )
    print(
        "Native-linkage test completed and the saved state was restored "
        f"({result.stop_reason.value})."
    )
    return 0


def _recovery_source(
    config: AppConfig,
    intent: HardwareTestIntent | None,
    record: LinkageTransactionRecord | None,
) -> tuple[
    LinkageTestSpec,
    tuple[DeviceControlSnapshot, ...],
    HardwareTestIntent | LinkageTransactionRecord,
]:
    if record is not None:
        if intent is not None and (
            intent.instance_id != config.instance.id
            or intent.operation_id != record.operation_id
            or intent.spec != record.spec
            or intent.snapshots != record.snapshots
        ):
            raise HardwareTestError("one-shot intent and recovery journal disagree")
        return record.spec, record.snapshots, record
    if intent is None or intent.phase is HardwareTestIntentPhase.TERMINAL:
        raise HardwareTestError("there is no unfinished native-linkage operation")
    if intent.instance_id != config.instance.id:
        raise HardwareTestError("one-shot intent belongs to another instance")
    return intent.spec, intent.snapshots, intent


def _automatic_recovery_blockers(
    record: LinkageTransactionRecord,
    *,
    now: datetime | None = None,
) -> tuple[str, ...]:
    """Return fixed, sanitized reasons why recovery-first must not touch this journal."""

    if record.phase is LinkageTransactionPhase.PREPARED:
        # PREPARED is durably written before the first frame and therefore needs no compensation.
        return ()

    blockers: list[str] = []
    if record.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK:
        blockers.append("safety_interlock")
    elif record.recovery_reason is LinkageRecoveryReason.SCHEDULE_CHANGED:
        blockers.append("schedule_changed")
    if any(snapshot.timer_enabled for snapshot in record.snapshots):
        blockers.append("timer_on_snapshot")

    checked_at = now or datetime.now(UTC)
    try:
        automatic_deadline = record.expires_at + timedelta(
            seconds=_MAX_AUTOMATIC_RECOVERY_GRACE_SECONDS
        )
        stale = (
            checked_at.tzinfo is None
            or checked_at.utcoffset() is None
            or record.created_at.tzinfo is None
            or record.created_at.utcoffset() is None
            or record.updated_at.tzinfo is None
            or record.updated_at.utcoffset() is None
            or record.expires_at.tzinfo is None
            or record.expires_at.utcoffset() is None
            or checked_at < record.created_at
            or checked_at < record.updated_at
            or checked_at > automatic_deadline
        )
    except (OverflowError, TypeError):
        stale = True
    if stale:
        blockers.append("stale_or_clock_invalid")
    return tuple(blockers)


def _status(
    config: AppConfig,
    intent_store: JsonHardwareTestIntentStore,
    journal_store: JsonLinkageJournalStore,
) -> int:
    if not config.runtime.state_path.is_absolute():
        raise HardwareTestError("runtime.state_path must be an absolute persistent path")
    intent = intent_store.load()
    record = journal_store.load()

    if intent is not None and intent.version == 3:
        raise HardwareTestError(
            "schedule-flow recovery requires jebao-flow-schedule-flow-test recover"
        )
    recovery_details = None
    if record is not None or (
        intent is not None and intent.phase is not HardwareTestIntentPhase.TERMINAL
    ):
        # Validate the two durable authorities before printing an actionable status. A partial
        # status followed by a mismatch refusal can otherwise advertise an unsafe next command.
        recovery_details = _recovery_source(config, intent, record)

    intent_status = intent.phase.value if intent is not None else "none"
    journal_status = record.phase.value if record is not None else "none"
    recovery_reason = (
        record.recovery_reason.value
        if record is not None and record.recovery_reason is not None
        else "none"
    )
    primary_failure = (
        intent.primary_failure.value
        if intent is not None and intent.primary_failure is not None
        else "none"
    )
    automatic_blockers = _automatic_recovery_blockers(record) if record is not None else ()
    latch_active = _safety_latch_present(canonical_safety_latch_path(config))
    if record is not None and record.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK:
        next_action = (
            "clear the persistent safety latch, then use attended confirmed recovery"
            if latch_active
            else "use attended recover-linkage confirmation (automatic recovery is blocked)"
        )
    elif record is not None and record.recovery_reason is LinkageRecoveryReason.SCHEDULE_CHANGED:
        next_action = (
            "clear the persistent safety latch outside this harness"
            if latch_active
            else "inspect the schedule, then use a new attended recover-linkage confirmation"
        )
    elif record is not None:
        next_action = (
            "clear the persistent safety latch outside this harness"
            if latch_active
            else (
                "use attended recover-linkage confirmation (automatic recovery is blocked)"
                if automatic_blockers
                else "recover-linkage --recovery-first"
            )
        )
    elif intent is not None and intent.phase is HardwareTestIntentPhase.STARTED:
        next_action = "recover-linkage (closes proven no-write crash state)"
    elif intent is not None and intent.phase is HardwareTestIntentPhase.RECOVERY_REQUIRED:
        next_action = "manual inspection (recovery journal is missing)"
    elif intent is not None and intent.phase is HardwareTestIntentPhase.ARMED:
        next_action = "run-native-linkage or confirmed preview cancellation"
    else:
        next_action = "preflight with a new operation ID"
    print(f"One-shot intent: {intent_status}")
    print(f"Recovery journal: {journal_status}")
    print(f"Recovery reason: {recovery_reason}")
    print(f"Primary failure: {primary_failure}")
    evidence = intent.evidence if intent is not None else None
    if evidence is None:
        print("Diagnostic evidence: unknown (legacy or absent)")
    else:
        forward_failure = (
            evidence.forward_failure.value
            if evidence.forward_failure is not None
            else "none"
        )
        ack_failure = (
            evidence.live_slave_ack_failure_kind.value
            if evidence.live_slave_ack_failure_kind is not None
            else "unknown"
            if evidence.live_slave_ack_unconfirmed_at is not None
            else "none"
        )
        ack_resolution = (
            f"{evidence.live_slave_ack_resolution_stage.value}/"
            f"{evidence.live_slave_ack_resolution_state.value}/"
            f"attempt_{evidence.live_slave_ack_resolution_attempts}"
            if evidence.live_slave_ack_resolution_stage is not None
            and evidence.live_slave_ack_resolution_state is not None
            and evidence.live_slave_ack_resolution_attempts is not None
            else "none"
        )
        ack_resolution_duration = (
            evidence.live_slave_ack_resolution_updated_at
            - evidence.live_slave_ack_resolution_started_at
            if evidence.live_slave_ack_resolution_started_at is not None
            and evidence.live_slave_ack_resolution_updated_at is not None
            else None
        )
        ack_resolution_duration_text = (
            f"{ack_resolution_duration.total_seconds():.1f}s"
            if ack_resolution_duration is not None
            else "none"
        )
        verified_span = (
            evidence.last_verified_sample.verified_at
            - evidence.first_verified_sample.verified_at
            if evidence.first_verified_sample is not None
            and evidence.last_verified_sample is not None
            else None
        )
        verified_span_text = (
            f"{verified_span.total_seconds():.1f}s" if verified_span is not None else "none"
        )
        rollback_state = (
            "completed"
            if evidence.rollback_completed_at is not None
            else "started"
            if evidence.rollback_started_at is not None
            else "not_started"
        )
        rollback_diagnostics = ", ".join(
            f"{failure.participant.value}/{failure.stage.value}/{failure.category.value}"
            for failure in evidence.rollback_failures
        )
        print(
            "Diagnostic evidence: "
            f"active={'yes' if evidence.active_entered_at is not None else 'no'}, "
            f"live_requested={'yes' if intent.spec.slave_power_after is not None else 'no'}, "
            "write_attempted="
            f"{'yes' if evidence.live_slave_write_attempted_at is not None else 'no'}, "
            "adapter_verified="
            f"{'yes' if evidence.live_slave_adapter_verified_at is not None else 'no'}, "
            "ack_unconfirmed="
            f"{'yes' if evidence.live_slave_ack_unconfirmed_at is not None else 'no'}, "
            f"ack_failure={ack_failure}, "
            f"ack_resolution={ack_resolution}, "
            f"ack_resolution_duration={ack_resolution_duration_text}, "
            "state_verified_without_ack="
            f"{'yes' if evidence.live_slave_state_verified_without_ack_at is not None else 'no'}, "
            "full_state_verified="
            f"{'yes' if evidence.live_slave_full_state_verified_at is not None else 'no'}, "
            f"samples={evidence.verified_sample_count}, "
            f"verified_span={verified_span_text}, "
            f"forward_failure={forward_failure}"
        )
        print(
            "Rollback evidence: "
            f"state={rollback_state}, "
            "reasons="
            + (
                ",".join(reason.value for reason in evidence.rollback_recovery_reasons)
                if evidence.rollback_recovery_reasons
                else "none"
            )
            + ", failures="
            + (rollback_diagnostics or "none")
        )
    print(
        "Automatic recovery blockers: "
        + (", ".join(automatic_blockers) if automatic_blockers else "none")
    )
    print(f"Persistent safety latch: {'active' if latch_active else 'clear'}")
    print(f"Next action: {next_action}")
    if recovery_details is not None:
        spec, snapshots, revision = recovery_details
        print(
            "Recovery confirmation token: "
            + recovery_confirmation_token(
                config.instance.id,
                spec,
                snapshots,
                revision,
            )
        )
    return 0


async def _recover_once(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
    journal_store: JsonLinkageJournalStore | ConfirmingLinkageJournalStore,
    authority: LinkageRecoveryAuthority,
    interlock: PersistentSafetyInterlock,
) -> bool:
    if _safety_latch_present(canonical_safety_latch_path(config)):
        raise HardwareTestError("persistent safety latch is active")
    devices = await _build_devices(config, selected, writable=True)
    controller = TemporaryLinkageController(
        devices,
        journal_store,
        safety_interlock=interlock,
    )
    async with _connected(devices):
        interlock.clear()
        try:
            return await controller.recover_pending(authority=authority)
        finally:
            interlock.trip()


def _persist_recovery_safety_interlock(
    journal_store: JsonLinkageJournalStore,
    fallback_record: LinkageTransactionRecord,
) -> LinkageTransactionRecord:
    """Durably invalidate recovery authority after observing a safety interlock."""

    current = journal_store.load() or fallback_record
    successor = current.model_copy(
        update={
            "phase": LinkageTransactionPhase.RECOVERY_REQUIRED,
            "recovery_reason": LinkageRecoveryReason.SAFETY_INTERLOCK,
            "updated_at": datetime.now(UTC),
            "error": "recovery deferred by persistent safety interlock",
            "failed_device_ids": tuple(
                sorted(snapshot.device_id for snapshot in current.snapshots)
            ),
            "restored_device_ids": (),
        }
    )
    try:
        journal_store.save(successor)
    except BaseException as save_error:
        # Atomic replacement can finish before a trailing file/directory fsync reports failure.
        # Continue to the physical safe stop only when a fresh reload proves that the exact typed
        # successor is already the durable authority. Any absent, stale or unreadable record must
        # preserve the original exception and emit no device command.
        try:
            persisted = journal_store.load()
        except BaseException:
            raise save_error from None
        if persisted != successor:
            raise
    return successor


async def _enforce_outer_recovery_safety_stop(
    config: AppConfig,
    selected: Mapping[str, DeviceConfig],
    journal_store: JsonLinkageJournalStore,
    interlock: PersistentSafetyInterlock,
    fallback_record: LinkageTransactionRecord,
) -> LinkageTransactionRecord:
    """Durably latch an outer-loop safety observation, then stop both devices.

    ``TemporaryLinkageController`` normally observes the deployment guard while it still owns its
    device sessions. There remains a narrow outer-loop window after an attempt has returned (and
    may already have cleared the journal), plus the audited retry dwell. A persistent e-stop or a
    durable typed safety transition seen there must do more than invalidate the JFR token: an
    already-restored TimerON master must be physically stopped as well. The fixed safety record is
    therefore fsynced before device construction or any OFF frame.
    """

    safety_record = _persist_recovery_safety_interlock(journal_store, fallback_record)
    devices: Mapping[str, JebaoDevice] = {}
    unexpected_stop_failure = False
    try:
        devices = await _build_devices(config, selected, writable=True)
        controller = TemporaryLinkageController(
            devices,
            journal_store,
            safety_interlock=interlock,
        )
        try:
            # This method always raises LinkageRollbackError after its bounded OFF compensation.
            # Its durable journal is the result; the exception text is intentionally not exposed.
            await controller.enforce_safety_stop(safety_record)
        except LinkageRollbackError:
            pass
        except Exception:
            unexpected_stop_failure = True
    except Exception:
        unexpected_stop_failure = True
    finally:
        if devices:
            try:
                async with asyncio.timeout(_MAX_ATTENDED_DISCOVERY_TIMEOUT_SECONDS):
                    await asyncio.gather(
                        *(device.disconnect() for device in devices.values()),
                        return_exceptions=True,
                    )
            except TimeoutError:
                # The safety journal already precedes the OFF attempt. A stuck local close must
                # not revive recovery authority or delay the fail-closed outer-loop exit.
                unexpected_stop_failure = True
            if any(device.connected for device in devices.values()):
                unexpected_stop_failure = True

    current = journal_store.load()
    expected_ids = tuple(sorted(snapshot.device_id for snapshot in safety_record.snapshots))
    if (
        current is None
        or current.operation_id != safety_record.operation_id
        or current.spec != safety_record.spec
        or current.snapshots != safety_record.snapshots
        or current.phase is not LinkageTransactionPhase.RECOVERY_REQUIRED
        or current.recovery_reason is not LinkageRecoveryReason.SAFETY_INTERLOCK
        or current.failed_device_ids != expected_ids
        or current.restored_device_ids
    ):
        # Do not issue another physical command when durable authority is inconsistent.
        raise HardwareTestError("outer safety stop did not remain durably latched")

    safe_stop_failed = unexpected_stop_failure or "safe_stop_failed" in (current.error or "")
    normalized_error = "recovery deferred by persistent safety interlock"
    if safe_stop_failed:
        normalized_error = f"{normalized_error}; safe_stop_failed"
    if current.error != normalized_error:
        current = current.model_copy(
            update={
                "updated_at": datetime.now(UTC),
                # Never copy an adapter/discovery exception or physical identifier into this
                # outer authority record. Preserve only the fixed typed stop-failure category.
                "error": normalized_error,
            }
        )
        journal_store.save(current)
    return current


async def _wait_for_recovery_retry_or_latch(config: AppConfig) -> bool:
    """Keep the audited retry dwell while polling the persistent safety authority."""

    loop = asyncio.get_running_loop()
    deadline = loop.time() + _RECOVERY_RETRY_SECONDS
    latch_path = canonical_safety_latch_path(config)
    while True:  # noqa: ASYNC110 - a filesystem latch has no event source to await
        if _safety_latch_present(latch_path):
            return True
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(_RECOVERY_LATCH_POLL_SECONDS, remaining))


async def _recover_linkage(
    config: AppConfig,
    confirmation: str | None,
    recovery_first: bool,
    intent_store: JsonHardwareTestIntentStore,
    journal_store: JsonLinkageJournalStore,
    interlock: PersistentSafetyInterlock,
) -> int:
    _assert_no_verification_conflict()
    intent = intent_store.load()
    record = journal_store.load()
    if intent is not None and intent.version == 3:
        raise HardwareTestError(
            "schedule-flow recovery requires jebao-flow-schedule-flow-test recover"
        )

    if (
        recovery_first
        and record is None
        and (intent is None or intent.phase is HardwareTestIntentPhase.TERMINAL)
    ):
        print("No unfinished native-linkage operation needs startup recovery.")
        return 0

    if record is None and intent is not None:
        if intent.instance_id != config.instance.id:
            raise HardwareTestError("one-shot intent belongs to another instance")
        if intent.phase is HardwareTestIntentPhase.STARTED:
            if intent.has_diagnostic_progress:
                intent_store.save(
                    _updated_intent(
                        intent,
                        HardwareTestIntentPhase.RECOVERY_REQUIRED,
                        "recovery_authority_missing",
                    )
                )
                raise HardwareTestError(
                    "diagnostic progress exists but the recovery journal is missing; "
                    "refusing to declare a no-write crash"
                )
            # STARTED precedes connect/controller.run; the core journal precedes its first write;
            # terminal intent precedes journal removal.  This state therefore proves zero writes.
            intent_store.save(
                _updated_intent(
                    intent,
                    HardwareTestIntentPhase.TERMINAL,
                    "crashed_before_first_write",
                )
            )
            print("The interrupted operation was closed as proven no-write; no frame was sent.")
            return 0
        if intent.phase is HardwareTestIntentPhase.RECOVERY_REQUIRED:
            raise HardwareTestError(
                "recovery-required intent has no journal; refusing synthetic hardware writes"
            )

    spec, snapshots, revision = _recovery_source(config, intent, record)
    if (
        recovery_first
        and record is not None
        and record.phase is not LinkageTransactionPhase.PREPARED
    ):
        if any(snapshot.timer_enabled for snapshot in record.snapshots):
            raise HardwareTestError(
                "automatic recovery of a TimerON snapshot is blocked; "
                "use attended confirmed recovery"
            )
        now = datetime.now(UTC)
        automatic_deadline = record.expires_at + timedelta(
            seconds=_MAX_AUTOMATIC_RECOVERY_GRACE_SECONDS
        )
        if now < record.created_at or now < record.updated_at or now > automatic_deadline:
            raise HardwareTestError(
                "automatic recovery window expired or the wall clock moved; "
                "use attended confirmed recovery"
            )
    token = recovery_confirmation_token(config.instance.id, spec, snapshots, revision)
    selected = _validate_config(
        config,
        frozenset({spec.master_device_id, spec.slave_device_id}),
    )
    with PhysicalDeviceLease.from_selected(config, selected).acquire():
        # Re-read both stores while owning both physical identities, before any connection/write.
        if journal_store.load() != record or intent_store.load() != intent:
            raise HardwareTestError("recovery state changed; request a new status/preview")

        if record is None:
            if recovery_first:
                print("No written transaction needs startup recovery; no frame was sent.")
                return 0
            if confirmation is None:
                print("Preview cancellation is fail-closed; no control frame was sent.")
                print(f"Recovery confirmation token: {token}")
                return 0
            if not hmac.compare_digest(confirmation, token):
                raise ConfirmationMismatchError(
                    "recovery confirmation token does not match; no control frame was sent"
                )
            if intent is None or intent.phase is not HardwareTestIntentPhase.ARMED:
                raise HardwareTestError("recovery journal is missing; no writes are permitted")
            intent_store.save(
                _updated_intent(
                    intent,
                    HardwareTestIntentPhase.TERMINAL,
                    "armed_preview_cancelled",
                )
            )
            print("The armed preview was closed; no control frame was sent.")
            return 0

        if recovery_first and record.recovery_reason is LinkageRecoveryReason.SAFETY_INTERLOCK:
            raise HardwareTestError(
                "safety-interlock recovery requires an attended confirmation; "
                "automatic ON-state recovery is blocked"
            )
        if recovery_first and record.recovery_reason is LinkageRecoveryReason.SCHEDULE_CHANGED:
            raise HardwareTestError(
                "schedule-changed recovery requires a new attended confirmation; "
                "automatic recovery is blocked"
            )
        if _safety_latch_present(canonical_safety_latch_path(config)):
            raise HardwareTestError(
                "persistent safety latch is active; exact ON-state recovery is blocked"
            )
        if not recovery_first:
            if confirmation is None:
                print("Recovery is fail-closed; no control frame was sent.")
                print(f"Recovery confirmation token: {token}")
                return 0
            if not hmac.compare_digest(confirmation, token):
                raise ConfirmationMismatchError(
                    "recovery confirmation token does not match; no control frame was sent"
                )

        if intent is None:
            recovery_started_at = datetime.now(UTC)
            evidence = HardwareTestEvidence(
                rollback_started_at=(
                    recovery_started_at
                    if record.phase is not LinkageTransactionPhase.PREPARED
                    else None
                )
            )
            evidence = _evidence_with_rollback_failures(evidence, record)
            intent = HardwareTestIntent(
                version=2,
                instance_id=config.instance.id,
                operation_id=spec.operation_id,
                phase=HardwareTestIntentPhase.RECOVERY_REQUIRED,
                confirmation_token=preview_confirmation_token(
                    config.instance.id,
                    spec,
                    snapshots,
                ),
                spec=spec,
                snapshots=snapshots,
                created_at=record.created_at,
                updated_at=recovery_started_at,
                outcome="recovery_started",
                evidence=evidence,
            )
        else:
            if intent.version == 2:
                evidence = intent.evidence
                if evidence is None:
                    raise HardwareTestError("diagnostic intent evidence is unavailable")
                intent = intent.model_copy(
                    update={
                        "evidence": _evidence_with_rollback_failures(
                            evidence,
                            record,
                        )
                    }
                )
        intent = _updated_intent(
            intent,
            HardwareTestIntentPhase.RECOVERY_REQUIRED,
            "recovery_started",
        )
        intent_store.save(intent)

        before_clear: Callable[[], None] | None = None

        def mark_recovery_terminal_before_clear() -> None:
            nonlocal intent
            successor = intent
            if successor.version == 2:
                evidence = successor.evidence
                if evidence is None:
                    raise HardwareTestError("diagnostic intent evidence is unavailable")
                latest = journal_store.load()
                if latest is not None:
                    evidence = _evidence_with_rollback_failures(evidence, latest)
                evidence = _evidence_with_rollback_completed(
                    evidence,
                    completed_at=datetime.now(UTC),
                )
                successor = successor.model_copy(update={"evidence": evidence})
            successor = _updated_intent(
                successor,
                HardwareTestIntentPhase.TERMINAL,
                "recovered",
            )
            intent_store.save(successor)
            intent = successor

        before_clear = mark_recovery_terminal_before_clear

        recovery_store = ConfirmingLinkageJournalStore(
            journal_store,
            instance_id=config.instance.id,
            expected_token=preview_confirmation_token(
                config.instance.id,
                spec,
                snapshots,
            ),
            before_clear=before_clear,
            before_load=_assert_no_verification_conflict,
            expected_loaded_record=record,
            require_loaded_record_match=True,
        )

        recovered = False
        schedule_change_detected = False
        safety_interlock_detected = False
        for attempt in range(1, _RECOVERY_ATTEMPTS + 1):
            if _safety_latch_present(canonical_safety_latch_path(config)):
                await _enforce_outer_recovery_safety_stop(
                    config,
                    selected,
                    journal_store,
                    interlock,
                    journal_store.load() or record,
                )
                safety_interlock_detected = True
                break
            try:
                recovered = await _recover_once(
                    config,
                    selected,
                    recovery_store,
                    (
                        LinkageRecoveryAuthority.AUTOMATIC
                        if recovery_first
                        else LinkageRecoveryAuthority.ATTENDED
                    ),
                    interlock,
                )
            except Exception:
                recovered = False
            pending_after_attempt = journal_store.load()
            if intent.version == 2 and pending_after_attempt is not None:
                evidence = intent.evidence
                if evidence is None:
                    raise HardwareTestError("diagnostic intent evidence is unavailable")
                merged_evidence = _evidence_with_rollback_failures(
                    evidence,
                    pending_after_attempt,
                )
                if merged_evidence != evidence:
                    # Remember each durable recovery reason before a later controller attempt
                    # transitions the journal back to ROLLING_BACK. Diagnostic persistence must
                    # never block the physical restore, so a store failure is retried by the
                    # terminal-before-clear hook while the in-memory successor stays available.
                    intent = intent.model_copy(
                        update={
                            "evidence": merged_evidence,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                    try:
                        intent_store.save(intent)
                    except BaseException:
                        pass
            if _safety_latch_present(canonical_safety_latch_path(config)):
                await _enforce_outer_recovery_safety_stop(
                    config,
                    selected,
                    journal_store,
                    interlock,
                    pending_after_attempt or record,
                )
                recovered = False
                safety_interlock_detected = True
                break
            if recovered and pending_after_attempt is None:
                break
            if (
                pending_after_attempt is not None
                and pending_after_attempt.recovery_reason
                is LinkageRecoveryReason.SAFETY_INTERLOCK
            ):
                # A safety transition invalidates the confirmation that authorized this loop.
                # A journal save can become durable and still report a late fsync failure before
                # the controller reaches its OFF compensation. Re-enforce the bounded safe stop;
                # never clear it or reconnect for ON restore under the same JFR token.
                await _enforce_outer_recovery_safety_stop(
                    config,
                    selected,
                    journal_store,
                    interlock,
                    pending_after_attempt,
                )
                safety_interlock_detected = True
                break
            if (
                pending_after_attempt is not None
                and pending_after_attempt.recovery_reason
                is LinkageRecoveryReason.SCHEDULE_CHANGED
            ):
                # A complete decoded state proved the schedule changed. Never let another
                # controller/reconnect attempt in the same confirmation erase that observation.
                schedule_change_detected = True
                break
            if attempt < _RECOVERY_ATTEMPTS:
                if await _wait_for_recovery_retry_or_latch(config):
                    await _enforce_outer_recovery_safety_stop(
                        config,
                        selected,
                        journal_store,
                        interlock,
                        pending_after_attempt or record,
                    )
                    safety_interlock_detected = True
                    break

        if not recovered or journal_store.load() is not None:
            if intent is not None:
                pending = journal_store.load()
                if intent.version == 2 and pending is not None:
                    evidence = intent.evidence
                    if evidence is None:
                        raise HardwareTestError("diagnostic intent evidence is unavailable")
                    intent = intent.model_copy(
                        update={
                            "evidence": _evidence_with_rollback_failures(
                                evidence,
                                pending,
                            )
                        }
                    )
                intent = _updated_intent(
                    intent,
                    HardwareTestIntentPhase.RECOVERY_REQUIRED,
                    "recovery_required",
                )
                intent_store.save(
                    intent
                )
            if schedule_change_detected:
                raise HardwareTestError(
                    "schedule changed during recovery; inspect it and start a new attended "
                    "confirmed recovery"
                )
            if safety_interlock_detected:
                raise HardwareTestError(
                    "safety interlock changed during recovery; inspect the aquarium, then "
                    "request a new status and attended recovery token"
                )
            raise HardwareTestError(
                f"exact recovery did not complete after {_RECOVERY_ATTEMPTS} bounded attempts"
            )

    intent = _updated_intent(intent, HardwareTestIntentPhase.TERMINAL, "recovered")
    intent_store.save(intent)
    print("The unfinished native-linkage operation was restored and closed.")
    return 0


async def _dispatch(config: AppConfig, args: argparse.Namespace) -> int:
    validate_hardware_safety_root()
    journal_store = JsonLinkageJournalStore(canonical_journal_path(config))
    intent_store = JsonHardwareTestIntentStore(canonical_intent_path(config))
    qualification_store = JsonQualificationStore(canonical_qualification_directory(config))
    with intent_store.lease():
        if args.command == "status":
            return _status(config, intent_store, journal_store)
        interlock = PersistentSafetyInterlock(canonical_safety_latch_path(config))
        with interlock.lease():
            if args.command == "preflight":
                return await _preflight(
                    config,
                    _spec_from_args(args),
                    intent_store,
                    journal_store,
                    qualification_store,
                )
            if args.command == "run-native-linkage":
                return await _run_native_linkage(
                    config,
                    _spec_from_args(args),
                    args.confirm,
                    intent_store,
                    journal_store,
                    qualification_store,
                    interlock,
                )
            if args.command == "recover-linkage":
                return await _recover_linkage(
                    config,
                    args.confirm,
                    args.recovery_first,
                    intent_store,
                    journal_store,
                    interlock,
                )
    raise AssertionError(f"unhandled command: {args.command}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging("DEBUG" if args.verbose else "WARNING")
    try:
        config = load_config(args.config)
        return asyncio.run(_dispatch(config, args))
    except HardwareTestError as error:
        print(f"hardware test refused: {error}", file=sys.stderr)
        return 2
    except HardwareSafetyRootError as error:
        print(f"hardware test refused: {error}", file=sys.stderr)
        return 2
    except (LinkageJournalError, LinkageJournalClaimError):
        print(
            "hardware test refused: recovery state is unavailable or already owned",
            file=sys.stderr,
        )
        return 2
    except (OSError, RuntimeError, ValueError, ValidationError) as error:
        # Do not echo protocol objects or discovery identities from lower layers.
        print(f"hardware test failed safely ({type(error).__name__})", file=sys.stderr)
        return 2
    except KeyboardInterrupt:  # pragma: no cover - platform fallback without signal handlers
        print("hardware test interrupted after rollback", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
