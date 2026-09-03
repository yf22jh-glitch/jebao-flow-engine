"""Precommitted, write-free verification for one app-driven ASYNC observation.

The Jebao application remains the only actor that changes schedule or linkage state in this
workflow.  This module owns neither an application driver nor a device writer.  It binds four
already-prepared read-only collector plans, re-derives every observation from preserved raw
frames, and writes one owner-only offline receipt using an app-specific outcome vocabulary.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from jebao_flow.protocol.codec import GizwitsCommand, decode_frame
from jebao_flow.protocol.models import ScheduleEntry
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import (
    LOCAL_WAVEMAKER_PRO_MODES,
    LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
    decode_schedule,
)
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE,
    LOCAL_WAVEMAKER_PRO_SLOT_COUNT,
    LOCAL_WAVEMAKER_PRO_SLOT_SIZE,
    LOCAL_WAVEMAKER_PRO_UNUSED_EE,
    LOCAL_WAVEMAKER_PRO_UNUSED_ZERO,
    LocalWavemakerProScheduleSnapshot,
)
from jebao_flow.protocol.session import STATE_REPLY_ACTION, STATE_REPORT_ACTION
from jebao_flow.q2_epoch import ExactRestoreEvidence
from jebao_flow.read_only_collector import (
    CaptureTransportPolicy,
    PilotPlanReference,
    PilotSeriesStore,
    VerifiedPilotPairArtifact,
    capture_transport_policy_from_acquisition,
)

APP_ASYNC_MANIFEST_SCHEMA_VERSION = 1
APP_ASYNC_RECEIPT_SCHEMA_VERSION = 1
APP_ASYNC_ACTION_ATTESTATION_VERSION = 1
APP_ASYNC_PHYSICAL_CLAIM_SCOPE = "protocol_report_only_no_physical_flow_waveform_or_phase_claim"
_MANIFEST_PREFIX = "AAM"
_RECEIPT_PREFIX = "AAR"
_SHA256_CHARS = frozenset("0123456789abcdef")
_SAFE_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_BOUNDARY_OFFSET = timedelta(minutes=5)
_BOUNDARY_MINUTE = timedelta(minutes=1)
_MAX_PRIVATE_JSON_BYTES = 2 * 1024 * 1024
_MAX_BOUND_PAIR_COUNT = 512
_MINIMUM_OBSERVATION_SPAN_NS = 7 * 60 * 1_000_000_000

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class AppAsyncObservationError(RuntimeError):
    """Privacy-safe refusal raised by the app observation verifier."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class AppAsyncStage(StrEnum):
    SCHEDULES_ARMED = "schedules_armed"
    SYNC_SELECTED = "sync_selected"
    ASYNC_SELECTED = "async_selected"
    OBSERVATION = "observation"


class AppAsyncWindow(StrEnum):
    PRE = "pre"
    MIXED = "mixed"
    POST = "post"
    EXCLUDED = "excluded"


class AppAsyncAttribution(StrEnum):
    MASTER_CONTENT = "master_content"
    SLAVE_SIDE_CONTENT_OR_ROLE_ENTRY_MIRROR = "slave_side_content_or_role_entry_mirror"
    SLAVE_CONTENT = "slave_content"
    MASTER_TIMING_MASTER_CONTENT = "master_timing_master_content"
    MASTER_TIMING_SLAVE_CONTENT = "master_timing_slave_content"
    SLAVE_TIMING_MASTER_CONTENT = "slave_timing_master_content"
    SLAVE_TIMING_SLAVE_SIDE_OR_ROLE_ENTRY_MIRROR = "slave_timing_slave_side_or_role_entry_mirror"
    MANUAL_ECHO = "manual_echo"
    FIELD_SPLIT = "field_split"
    UNEXPECTED_EFFECTIVE_STATE = "unexpected_effective_state"


class AppAsyncConclusion(StrEnum):
    MASTER_TIMING_MASTER_CONTENT = "master_timing_master_content"
    MASTER_TIMING_SLAVE_CONTENT = "master_timing_slave_content"
    SLAVE_TIMING_MASTER_CONTENT = "slave_timing_master_content"
    SLAVE_TIMING_SLAVE_SIDE_OR_ROLE_ENTRY_MIRROR = "slave_timing_slave_side_or_role_entry_mirror"
    UNKNOWN = "unknown"


class AppReportedTuple(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: str = Field(min_length=1)
    flow: int = Field(ge=0, le=100)
    frequency: int = Field(ge=0, le=100)


class AppScheduleExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    participant: Literal["master", "slave"]
    boundary_local: datetime
    before: AppReportedTuple
    after: AppReportedTuple
    predicted_ee_image_sha256: Sha256Digest

    @field_validator("boundary_local")
    @classmethod
    def require_naive_exact_minute(cls, value: datetime) -> datetime:
        if value.tzinfo is not None or value.second != 0 or value.microsecond != 0:
            raise ValueError("boundary must be a naive device-local exact minute")
        return value


class AppCollectorPlanBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stage: AppAsyncStage
    plan_artifact_id: str = Field(min_length=1, max_length=80)
    plan_sha256: Sha256Digest
    series_id: str = Field(min_length=1, max_length=80)
    planned_pair_count: int = Field(ge=1, le=_MAX_BOUND_PAIR_COUNT)
    requested_cadence_ns: int = Field(gt=0)
    transport_policy: CaptureTransportPolicy
    collector_commit_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    collector_runtime_source_digest_sha256: Sha256Digest


class AppUiActionExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    ordinal: int = Field(ge=0)
    participant: Literal["master", "slave", "pair"]
    action_name: str = Field(min_length=1, max_length=80)
    expected_effect: str = Field(min_length=1, max_length=240)
    checkpoint_after: AppAsyncStage | None = None


class AppEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_artifact_id: str = Field(min_length=1, max_length=80)
    series_artifact_id: str = Field(min_length=1, max_length=80)
    pair_ordinal: int = Field(ge=0)
    pair_manifest_sha256: Sha256Digest


class AppBaselineDeviceClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    participant: Literal["master", "slave"]
    logical_id: str = Field(min_length=1, max_length=128)
    identity_binding_sha256: Sha256Digest
    manual: AppReportedTuple
    schedule_image_sha256: Sha256Digest


class AppBaselineClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_artifact_id: str = Field(min_length=1, max_length=80)
    baseline_sha256: Sha256Digest
    primary_evidence: AppEvidenceReference
    corroborating_evidence: AppEvidenceReference
    devices: tuple[AppBaselineDeviceClaim, AppBaselineDeviceClaim]

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        if tuple(device.participant for device in self.devices) != ("master", "slave"):
            raise ValueError("baseline devices must be ordered master then slave")
        if len({device.identity_binding_sha256 for device in self.devices}) != 2:
            raise ValueError("baseline device bindings must be distinct")
        if (
            self.primary_evidence.plan_artifact_id == self.corroborating_evidence.plan_artifact_id
            or self.primary_evidence.series_artifact_id
            == self.corroborating_evidence.series_artifact_id
            or self.primary_evidence.pair_manifest_sha256
            == self.corroborating_evidence.pair_manifest_sha256
        ):
            raise ValueError("baseline evidence must be independent")
        return self


class AppRestoreClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseline_artifact_id: str = Field(min_length=1, max_length=80)
    baseline_sha256: Sha256Digest
    sentinel_receipt_id: str = Field(min_length=1, max_length=80)
    sentinel_receipt_sha256: Sha256Digest
    baseline_receipt_id: str = Field(min_length=1, max_length=80)
    baseline_receipt_sha256: Sha256Digest
    final_restore_operation_id: str = Field(min_length=1, max_length=128)
    final_restore_record_sha256: Sha256Digest
    expected_identity_bindings_sha256: tuple[Sha256Digest, Sha256Digest]


class AppAsyncManifest(BaseModel):
    """Canonical private contract committed before the application is opened."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = APP_ASYNC_MANIFEST_SCHEMA_VERSION
    kind: Literal["app_async_observation_manifest"] = "app_async_observation_manifest"
    operation_nonce: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]
    created_utc: datetime
    verifier_commit_sha: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$")]
    verifier_runtime_source_digest_sha256: Sha256Digest
    expected_identity_bindings_sha256: tuple[Sha256Digest, Sha256Digest]
    schedules: tuple[AppScheduleExpectation, AppScheduleExpectation]
    collector_plans: tuple[
        AppCollectorPlanBinding,
        AppCollectorPlanBinding,
        AppCollectorPlanBinding,
        AppCollectorPlanBinding,
    ]
    ui_actions: tuple[AppUiActionExpectation, ...]
    baseline: AppBaselineClaim
    exact_restore: AppRestoreClaim
    minimum_qualifying_pairs_per_window: Literal[1] = 1
    conflicting_pairs_allowed: Literal[0] = 0
    stability_duration_claimed: Literal[False] = False
    exact_boundary_minute_excluded: Literal[True] = True
    schedule_gate: Literal[
        "decoded_content_exact_unused_zero_or_ee_recorded_then_role_writes_byte_exact"
    ] = "decoded_content_exact_unused_zero_or_ee_recorded_then_role_writes_byte_exact"
    physical_claim_scope: Literal[
        "protocol_report_only_no_physical_flow_waveform_or_phase_claim"
    ] = APP_ASYNC_PHYSICAL_CLAIM_SCOPE

    @field_validator("created_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("manifest creation time must be UTC")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if len(set(self.expected_identity_bindings_sha256)) != 2:
            raise ValueError("manifest bindings must be distinct")
        if tuple(schedule.participant for schedule in self.schedules) != (
            "master",
            "slave",
        ):
            raise ValueError("schedule participants must be ordered master then slave")
        master, slave = self.schedules
        if (
            slave.boundary_local - master.boundary_local != _BOUNDARY_OFFSET
            or master.boundary_local.date() != slave.boundary_local.date()
            or master.boundary_local.time() == time(0, 0)
            or slave.boundary_local.time() == time(0, 0)
        ):
            raise ValueError("schedule boundaries must be T and same-day T+5 minutes")
        fixed = (
            master.before,
            slave.before,
            master.after,
            slave.after,
        )
        if fixed != (
            AppReportedTuple(mode="sine", flow=35, frequency=30),
            AppReportedTuple(mode="constant", flow=32, frequency=0),
            AppReportedTuple(mode="constant", flow=30, frequency=0),
            AppReportedTuple(mode="sine", flow=40, frequency=30),
        ):
            raise ValueError("app schedule signature differs from the reviewed plan")
        for schedule in self.schedules:
            expected = build_expected_schedule_image(
                schedule.participant,
                schedule.boundary_local,
            )
            if hashlib.sha256(expected).hexdigest() != schedule.predicted_ee_image_sha256:
                raise ValueError("predicted schedule digest mismatch")
        if tuple(plan.stage for plan in self.collector_plans) != tuple(AppAsyncStage):
            raise ValueError("collector plans must cover each stage in order")
        for plan in self.collector_plans:
            expected_policy = (
                CaptureTransportPolicy.REPLY_OR_REPORT
                if plan.stage is AppAsyncStage.OBSERVATION
                else CaptureTransportPolicy.EXPLICIT_ONLY
            )
            if plan.transport_policy is not expected_policy:
                raise ValueError("collector transport policy does not match its stage")
            if plan.collector_commit_sha != self.verifier_commit_sha:
                raise ValueError("collector and verifier commits must match")
        observation_plan = self.collector_plans[-1]
        if (
            observation_plan.planned_pair_count - 1
        ) * observation_plan.requested_cadence_ns < _MINIMUM_OBSERVATION_SPAN_NS:
            raise ValueError("observation plan cannot cover all three windows")
        if (
            len({plan.plan_artifact_id for plan in self.collector_plans}) != 4
            or len({plan.series_id for plan in self.collector_plans}) != 4
        ):
            raise ValueError("collector plans must be independent")
        if self.ui_actions != expected_ui_actions():
            raise ValueError("UI action contract differs from the reviewed plan")
        if (
            tuple(device.identity_binding_sha256 for device in self.baseline.devices)
            != self.expected_identity_bindings_sha256
            or self.exact_restore.expected_identity_bindings_sha256
            != self.expected_identity_bindings_sha256
            or self.exact_restore.baseline_artifact_id != self.baseline.baseline_artifact_id
            or self.exact_restore.baseline_sha256 != self.baseline.baseline_sha256
        ):
            raise ValueError("baseline, restore and collector bindings differ")
        return self

    @property
    def manifest_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.model_dump(mode="json"))).hexdigest()

    @property
    def operation_id(self) -> str:
        digest = hashlib.sha256(
            b"jebao-flow/app-async-observation/v1\0" + bytes.fromhex(self.manifest_sha256)
        ).hexdigest()
        return f"app-async-{digest}"


class AppUiActionAttestation(BaseModel):
    """Grade-(c) operator statement; device effects are verified separately from raw."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = APP_ASYNC_ACTION_ATTESTATION_VERSION
    manifest_id: str = Field(min_length=1, max_length=80)
    manifest_sha256: Sha256Digest
    observed_utc: datetime
    completed_ordinals: tuple[int, ...]
    each_action_performed_exactly_once: Literal[True] = True
    evidence_grade: Literal["c"] = "c"
    statement: Literal["operator_attests_precommitted_ui_actions_in_order_without_repeat"] = (
        "operator_attests_precommitted_ui_actions_in_order_without_repeat"
    )

    @field_validator("observed_utc")
    @classmethod
    def require_aware_observation(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("action attestation time must be timezone-aware")
        return value.astimezone(UTC)

    @property
    def attestation_sha256(self) -> str:
        return hashlib.sha256(_canonical_json(self.model_dump(mode="json"))).hexdigest()


@dataclass(frozen=True, slots=True)
class AppAsyncManifestReference:
    manifest_id: str
    manifest_sha256: str
    operation_id: str
    directory: Path


@dataclass(frozen=True, slots=True)
class AppAsyncReceiptReference:
    receipt_id: str
    receipt_sha256: str
    manifest_id: str
    manifest_sha256: str
    operation_id: str
    conclusion: AppAsyncConclusion
    directory: Path


@dataclass(frozen=True, slots=True)
class _DecodedSample:
    participant: str
    identity_binding_sha256: str
    action: str
    read_started_ns: int
    read_completed_ns: int
    device_time: datetime
    enabled: bool
    timer_on: bool
    linkage: str
    manual: AppReportedTuple
    auto: AppReportedTuple
    schedule_image: bytes
    schedule_image_sha256: str
    schedule_invalid_slots: tuple[int, ...]
    active_problems: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _DecodedPair:
    ordinal: int
    pair_manifest_sha256: str
    pair_gap_ns: int
    master: _DecodedSample
    slave: _DecodedSample


class _VerifiedSeriesProvider(Protocol):
    def load(self, series_id: str) -> PilotPlanReference: ...

    def verify_plan(self, reference: PilotPlanReference) -> dict[str, Any]: ...

    def verify_completed_series(
        self,
        reference: PilotPlanReference,
        *,
        expected_series_sha256: str,
    ) -> dict[str, Any]: ...

    def extract_verified_accepted_pair(
        self,
        reference: PilotPlanReference,
        *,
        expected_series_sha256: str,
        ordinal: int,
    ) -> VerifiedPilotPairArtifact: ...


class AppExactRestoreEvidenceVerifier(Protocol):
    def derive_q2_exact_restore_evidence(
        self,
        *,
        not_after: datetime | None = None,
    ) -> ExactRestoreEvidence: ...

    def verify_q2_exact_restore_evidence(
        self,
        evidence: ExactRestoreEvidence,
        *,
        not_after: datetime,
    ) -> ExactRestoreEvidence: ...

    def load_verified_qualified_bundle(self, *, not_after: datetime | None = None) -> Any: ...


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("ascii")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _SHA256_CHARS for character in value)
    )


def _safe_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 80
        or any(character not in _SAFE_ID_CHARS for character in value)
    ):
        raise AppAsyncObservationError("app_async_artifact_id_invalid")
    return value


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_private_file(path: Path, code: str) -> bytes:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(descriptor)
        named = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
            or before.st_nlink != 1
            or before.st_size > _MAX_PRIVATE_JSON_BYTES
            or (before.st_dev, before.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise AppAsyncObservationError(code)
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, 65_536):
            total += len(chunk)
            if total > _MAX_PRIVATE_JSON_BYTES:
                raise AppAsyncObservationError(code)
            chunks.append(chunk)
        after = os.fstat(descriptor)
        named_after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ) or (after.st_dev, after.st_ino) != (named_after.st_dev, named_after.st_ino):
            raise AppAsyncObservationError(code)
        return b"".join(chunks)
    except AppAsyncObservationError:
        raise
    except (FileNotFoundError, OSError) as error:
        raise AppAsyncObservationError(code) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _require_private_root(root: Path) -> None:
    try:
        metadata = root.lstat()
    except (FileNotFoundError, OSError) as error:
        raise AppAsyncObservationError("app_async_artifact_root_missing") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise AppAsyncObservationError("app_async_artifact_root_not_private")


def _entry(
    *, slot: int, start: str, end: str, mode: str, flow: int, frequency: int
) -> ScheduleEntry:
    return ScheduleEntry(
        slot=slot,
        start=start,
        end=end,
        mode=mode,
        mode_code=LOCAL_WAVEMAKER_PRO_MODES.index(mode),
        parameters={
            "flow": flow,
            "frequency": frequency,
            "feed_time": 0,
            "custom_frequency": 0,
        },
    )


def build_expected_schedule_image(participant: str, boundary_local: datetime) -> bytes:
    """Build the exact app reset (0xee) plus two-slot fixed schedule image."""

    if boundary_local.tzinfo is not None or boundary_local.second or boundary_local.microsecond:
        raise AppAsyncObservationError("app_async_boundary_invalid")
    if boundary_local.time() == time(0, 0):
        raise AppAsyncObservationError("app_async_boundary_midnight_invalid")
    boundary = boundary_local.strftime("%H:%M")
    if participant == "master":
        before = ("sine", 35, 30)
        after = ("constant", 30, 0)
    elif participant == "slave":
        before = ("constant", 32, 0)
        after = ("sine", 40, 30)
    else:
        raise AppAsyncObservationError("app_async_participant_invalid")
    snapshot = LocalWavemakerProScheduleSnapshot(
        LOCAL_WAVEMAKER_PRO_UNUSED_EE * LOCAL_WAVEMAKER_PRO_SLOT_COUNT
    )
    snapshot = snapshot.with_entry(
        _entry(
            slot=0,
            start="00:00",
            end=boundary,
            mode=before[0],
            flow=before[1],
            frequency=before[2],
        )
    )
    snapshot = snapshot.with_entry(
        _entry(
            slot=1,
            start=boundary,
            end="24:00",
            mode=after[0],
            flow=after[1],
            frequency=after[2],
        )
    )
    return snapshot.validate().image


def expected_ui_actions() -> tuple[AppUiActionExpectation, ...]:
    """Return the immutable wire-affecting app action sequence."""

    values = (
        (0, "master", "reset_schedule", "all 48 AutoTime slots set to 0xee", None),
        (1, "master", "save_before_slot", "slot 0 saved as master before tuple", None),
        (
            2,
            "master",
            "save_after_slot",
            "slot 1 saved as master after tuple",
            None,
        ),
        (3, "slave", "reset_schedule", "all 48 AutoTime slots set to 0xee", None),
        (4, "slave", "save_before_slot", "slot 0 saved as slave before tuple", None),
        (5, "slave", "save_after_slot", "slot 1 saved as slave after tuple", None),
        (
            6,
            "pair",
            "confirm_timers_on",
            "both devices remain ON TimerON and independent; no write if already true",
            AppAsyncStage.SCHEDULES_ARMED,
        ),
        (7, "master", "select_master", "Linkage becomes master", None),
        (
            8,
            "slave",
            "select_slave_parent",
            "Linkage becomes sync_slave",
            AppAsyncStage.SYNC_SELECTED,
        ),
        (
            9,
            "slave",
            "select_async",
            "Linkage becomes async_slave",
            AppAsyncStage.ASYNC_SELECTED,
        ),
    )
    return tuple(
        AppUiActionExpectation(
            ordinal=ordinal,
            participant=participant,
            action_name=name,
            expected_effect=effect,
            checkpoint_after=checkpoint,
        )
        for ordinal, participant, name, effect, checkpoint in values
    )


def _expected_schedules(master_boundary: datetime) -> tuple[AppScheduleExpectation, ...]:
    slave_boundary = master_boundary + _BOUNDARY_OFFSET
    master_image = build_expected_schedule_image("master", master_boundary)
    slave_image = build_expected_schedule_image("slave", slave_boundary)
    return (
        AppScheduleExpectation(
            participant="master",
            boundary_local=master_boundary,
            before=AppReportedTuple(mode="sine", flow=35, frequency=30),
            after=AppReportedTuple(mode="constant", flow=30, frequency=0),
            predicted_ee_image_sha256=hashlib.sha256(master_image).hexdigest(),
        ),
        AppScheduleExpectation(
            participant="slave",
            boundary_local=slave_boundary,
            before=AppReportedTuple(mode="constant", flow=32, frequency=0),
            after=AppReportedTuple(mode="sine", flow=40, frequency=30),
            predicted_ee_image_sha256=hashlib.sha256(slave_image).hexdigest(),
        ),
    )


def _restore_model(value: ExactRestoreEvidence) -> AppRestoreClaim:
    return AppRestoreClaim(
        baseline_artifact_id=value.baseline_artifact_id,
        baseline_sha256=value.baseline_sha256,
        sentinel_receipt_id=value.sentinel_receipt_id,
        sentinel_receipt_sha256=value.sentinel_receipt_sha256,
        baseline_receipt_id=value.baseline_receipt_id,
        baseline_receipt_sha256=value.baseline_receipt_sha256,
        final_restore_operation_id=value.final_restore_operation_id,
        final_restore_record_sha256=value.final_restore_record_sha256,
        expected_identity_bindings_sha256=value.expected_identity_bindings_sha256,
    )


def _restore_dataclass(value: AppRestoreClaim) -> ExactRestoreEvidence:
    return ExactRestoreEvidence(**value.model_dump(mode="python"))


def _evidence_reference(value: Any) -> AppEvidenceReference:
    return AppEvidenceReference(
        plan_artifact_id=value.plan_artifact_id,
        series_artifact_id=value.series_artifact_id,
        pair_ordinal=value.pair_ordinal,
        pair_manifest_sha256=value.pair_manifest_sha256,
    )


def _baseline_from_bundle(bundle: Any) -> AppBaselineClaim:
    baseline = bundle.qualified_record.baseline
    corroborating = baseline.corroborating_evidence
    if corroborating is None:
        raise AppAsyncObservationError("app_async_corroborating_baseline_missing")
    devices: list[AppBaselineDeviceClaim] = []
    for participant, device in zip(("master", "slave"), baseline.devices, strict=True):
        devices.append(
            AppBaselineDeviceClaim(
                participant=participant,
                logical_id=device.logical_id,
                identity_binding_sha256=device.identity_binding_sha256,
                manual=AppReportedTuple(
                    mode=device.outer.mode,
                    flow=device.outer.power,
                    frequency=device.outer.frequency,
                ),
                schedule_image_sha256=device.schedule.image_sha256,
            )
        )
    return AppBaselineClaim(
        baseline_artifact_id=bundle.artifact_id,
        baseline_sha256=bundle.baseline_sha256,
        primary_evidence=_evidence_reference(baseline.evidence),
        corroborating_evidence=_evidence_reference(corroborating),
        devices=(devices[0], devices[1]),
    )


def _plan_binding(
    stage: AppAsyncStage,
    reference: PilotPlanReference,
    plan: Mapping[str, Any],
) -> AppCollectorPlanBinding:
    return AppCollectorPlanBinding(
        stage=stage,
        plan_artifact_id=reference.plan_artifact_id,
        plan_sha256=reference.plan_sha256,
        series_id=reference.series_id,
        planned_pair_count=reference.planned_pair_count,
        requested_cadence_ns=reference.requested_cadence_ns,
        transport_policy=capture_transport_policy_from_acquisition(plan["acquisition"]),
        collector_commit_sha=plan["collector_commit_sha"],
        collector_runtime_source_digest_sha256=plan["collector_runtime_source_digest_sha256"],
    )


def _series_started(reference: PilotPlanReference) -> bool:
    return any(
        (reference.series_directory / name).exists()
        for name in (
            "started.json",
            "series.json",
            "series.commit.json",
            "aborted.json",
            "aborted.commit.json",
        )
    )


class AppAsyncObservationStore:
    """Owner-only immutable manifests and raw-derived offline receipts."""

    def __init__(self, root: Path) -> None:
        _require_private_root(root)
        self.root = root

    def prepare(
        self,
        collector_store: PilotSeriesStore,
        plans: Mapping[AppAsyncStage, PilotPlanReference],
        *,
        master_boundary_local: datetime,
        operation_nonce: str,
        verifier_commit_sha: str,
        verifier_runtime_source_digest_sha256: str,
        restore_verifier: AppExactRestoreEvidenceVerifier,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> AppAsyncManifestReference:
        """Commit all interpretation and collector plans before the app is opened."""

        created_at = utc_clock()
        if created_at.tzinfo is None or created_at.utcoffset() != timedelta(0):
            raise AppAsyncObservationError("app_async_manifest_clock_invalid")
        if set(plans) != set(AppAsyncStage):
            raise AppAsyncObservationError("app_async_collector_plan_set_invalid")
        if (
            master_boundary_local.tzinfo is not None
            or master_boundary_local.second
            or master_boundary_local.microsecond
            or master_boundary_local.date() != (master_boundary_local + _BOUNDARY_OFFSET).date()
            or master_boundary_local.time() == time(0, 0)
        ):
            raise AppAsyncObservationError("app_async_boundary_invalid")

        restore = restore_verifier.derive_q2_exact_restore_evidence(not_after=created_at)
        bundle = restore_verifier.load_verified_qualified_bundle(not_after=created_at)
        baseline = _baseline_from_bundle(bundle)
        if (
            restore.baseline_artifact_id != baseline.baseline_artifact_id
            or restore.baseline_sha256 != baseline.baseline_sha256
        ):
            raise AppAsyncObservationError("app_async_restore_baseline_mismatch")

        plan_bindings: list[AppCollectorPlanBinding] = []
        for stage in AppAsyncStage:
            reference = plans[stage]
            plan = collector_store.verify_plan(reference)
            if _series_started(reference):
                raise AppAsyncObservationError("app_async_collector_series_already_started")
            if reference.epoch != f"app_async_{stage.value}":
                raise AppAsyncObservationError("app_async_collector_epoch_mismatch")
            binding = _plan_binding(stage, reference, plan)
            expected_policy = (
                CaptureTransportPolicy.REPLY_OR_REPORT
                if stage is AppAsyncStage.OBSERVATION
                else CaptureTransportPolicy.EXPLICIT_ONLY
            )
            planned_bindings = tuple(
                item["expected_identity_binding_sha256"] for item in plan["ordered_targets"]
            )
            if (
                binding.transport_policy is not expected_policy
                or binding.collector_commit_sha != verifier_commit_sha
                or planned_bindings != restore.expected_identity_bindings_sha256
            ):
                raise AppAsyncObservationError("app_async_collector_plan_mismatch")
            plan_bindings.append(binding)

        manifest = AppAsyncManifest(
            operation_nonce=operation_nonce,
            created_utc=created_at,
            verifier_commit_sha=verifier_commit_sha,
            verifier_runtime_source_digest_sha256=(verifier_runtime_source_digest_sha256),
            expected_identity_bindings_sha256=restore.expected_identity_bindings_sha256,
            schedules=_expected_schedules(master_boundary_local),
            collector_plans=tuple(plan_bindings),
            ui_actions=expected_ui_actions(),
            baseline=baseline,
            exact_restore=_restore_model(restore),
        )
        payload = _canonical_json(manifest.model_dump(mode="json"))
        digest = hashlib.sha256(payload).hexdigest()
        if digest != manifest.manifest_sha256:
            raise AssertionError("manifest digest invariant failed")
        manifest_id = f"{_MANIFEST_PREFIX}-{digest}"
        final_path = self.root / manifest_id
        temporary = self.root / f".{manifest_id}.tmp-{secrets.token_hex(8)}"
        try:
            temporary.mkdir(mode=0o700)
            _write_exclusive(temporary / "manifest.json", payload)
            _write_exclusive(
                temporary / "manifest.commit.json",
                _canonical_json(
                    {
                        "schema_version": APP_ASYNC_MANIFEST_SCHEMA_VERSION,
                        "manifest_id": manifest_id,
                        "manifest_sha256": digest,
                        "operation_id": manifest.operation_id,
                    }
                ),
            )
            _fsync_directory(temporary)
            os.rename(temporary, final_path)
            _fsync_directory(self.root)
        except OSError as error:
            raise AppAsyncObservationError("app_async_manifest_durability_unconfirmed") from error
        reference = AppAsyncManifestReference(
            manifest_id=manifest_id,
            manifest_sha256=digest,
            operation_id=manifest.operation_id,
            directory=final_path,
        )
        self.verify_manifest(reference, collector_store)
        return reference

    def load_manifest(
        self,
        manifest_id: str,
        collector_store: PilotSeriesStore,
        *,
        expected_manifest_sha256: str,
    ) -> AppAsyncManifestReference:
        safe_id = _safe_id(manifest_id)
        if not _is_sha256(expected_manifest_sha256):
            raise AppAsyncObservationError("app_async_manifest_digest_invalid")
        payload = _read_private_file(
            self.root / safe_id / "manifest.json",
            "app_async_manifest_file_invalid",
        )
        if not secrets.compare_digest(
            hashlib.sha256(payload).hexdigest(), expected_manifest_sha256
        ):
            raise AppAsyncObservationError("app_async_manifest_digest_mismatch")
        try:
            manifest = AppAsyncManifest.model_validate_json(payload)
        except (ValueError, TypeError) as error:
            raise AppAsyncObservationError("app_async_manifest_claim_invalid") from error
        reference = AppAsyncManifestReference(
            manifest_id=safe_id,
            manifest_sha256=expected_manifest_sha256,
            operation_id=manifest.operation_id,
            directory=self.root / safe_id,
        )
        self.verify_manifest(reference, collector_store)
        return reference

    def verify_manifest(
        self,
        reference: AppAsyncManifestReference,
        collector_store: PilotSeriesStore,
    ) -> AppAsyncManifest:
        expected_id = f"{_MANIFEST_PREFIX}-{reference.manifest_sha256}"
        directory = self.root / expected_id
        try:
            metadata = directory.lstat()
        except (FileNotFoundError, OSError) as error:
            raise AppAsyncObservationError("app_async_manifest_directory_invalid") from error
        if (
            reference.manifest_id != expected_id
            or reference.directory != directory
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise AppAsyncObservationError("app_async_manifest_directory_invalid")
        payload = _read_private_file(directory / "manifest.json", "app_async_manifest_file_invalid")
        digest = hashlib.sha256(payload).hexdigest()
        marker_payload = _read_private_file(
            directory / "manifest.commit.json", "app_async_manifest_marker_invalid"
        )
        try:
            manifest = AppAsyncManifest.model_validate_json(payload)
            marker = json.loads(marker_payload)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            raise AppAsyncObservationError("app_async_manifest_claim_invalid") from error
        if (
            not secrets.compare_digest(digest, reference.manifest_sha256)
            or _canonical_json(manifest.model_dump(mode="json")) != payload
            or marker
            != {
                "schema_version": APP_ASYNC_MANIFEST_SCHEMA_VERSION,
                "manifest_id": reference.manifest_id,
                "manifest_sha256": reference.manifest_sha256,
                "operation_id": manifest.operation_id,
            }
            or manifest.operation_id != reference.operation_id
        ):
            raise AppAsyncObservationError("app_async_manifest_binding_invalid")
        for binding in manifest.collector_plans:
            loaded = collector_store.load(binding.series_id)
            plan = collector_store.verify_plan(loaded)
            if _plan_binding(binding.stage, loaded, plan) != binding:
                raise AppAsyncObservationError("app_async_collector_plan_binding_invalid")
            expected_bindings = tuple(
                target["expected_identity_binding_sha256"] for target in plan["ordered_targets"]
            )
            if expected_bindings != manifest.expected_identity_bindings_sha256:
                raise AppAsyncObservationError("app_async_collector_identity_mismatch")
        return manifest

    def verify_checkpoints(
        self,
        reference: AppAsyncManifestReference,
        collector_store: PilotSeriesStore,
        *,
        series_sha256_by_stage: Mapping[AppAsyncStage, str],
        through_stage: AppAsyncStage,
        restore_verifier: AppExactRestoreEvidenceVerifier,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> dict[str, Any]:
        """Reverify every checkpoint through ``through_stage`` without writing a receipt."""

        if through_stage is AppAsyncStage.OBSERVATION:
            raise AppAsyncObservationError("app_async_checkpoint_stage_invalid")
        manifest = self.verify_manifest(reference, collector_store)
        self._verify_restore_still_staged(manifest, restore_verifier, utc_clock())
        required = list(AppAsyncStage)[: list(AppAsyncStage).index(through_stage) + 1]
        if set(series_sha256_by_stage) != set(required):
            raise AppAsyncObservationError("app_async_checkpoint_series_set_invalid")
        results, _armed_images = _verify_checkpoint_chain(
            manifest,
            collector_store,
            series_sha256_by_stage,
            required,
        )
        return {
            "status": "app_async_checkpoint_verified",
            "manifest_id": reference.manifest_id,
            "manifest_sha256": reference.manifest_sha256,
            "operation_id": reference.operation_id,
            "through_stage": through_stage.value,
            "checkpoints": results,
            "physical_claim_scope": APP_ASYNC_PHYSICAL_CLAIM_SCOPE,
        }

    def verify_restore_ready(
        self,
        reference: AppAsyncManifestReference,
        collector_store: PilotSeriesStore,
        *,
        restore_verifier: AppExactRestoreEvidenceVerifier,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> AppAsyncManifest:
        """Reverify the pre-staged exact restore before a read-only or app phase continues."""

        manifest = self.verify_manifest(reference, collector_store)
        self._verify_restore_still_staged(manifest, restore_verifier, utc_clock())
        return manifest

    def classify_and_commit(
        self,
        reference: AppAsyncManifestReference,
        collector_store: PilotSeriesStore,
        *,
        series_sha256_by_stage: Mapping[AppAsyncStage, str],
        action_attestation: AppUiActionAttestation,
        restore_verifier: AppExactRestoreEvidenceVerifier,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> AppAsyncReceiptReference:
        manifest = self.verify_manifest(reference, collector_store)
        now = utc_clock()
        self._verify_restore_still_staged(manifest, restore_verifier, now)
        if set(series_sha256_by_stage) != set(AppAsyncStage):
            raise AppAsyncObservationError("app_async_series_set_invalid")
        expected_ordinals = tuple(action.ordinal for action in manifest.ui_actions)
        if (
            action_attestation.manifest_id != reference.manifest_id
            or action_attestation.manifest_sha256 != reference.manifest_sha256
            or action_attestation.completed_ordinals != expected_ordinals
        ):
            raise AppAsyncObservationError("app_async_action_attestation_invalid")

        checkpoint_results, armed_images = _verify_checkpoint_chain(
            manifest,
            collector_store,
            series_sha256_by_stage,
            list(AppAsyncStage)[:-1],
        )
        observation = _classify_observation_series(
            manifest,
            collector_store,
            series_sha256=series_sha256_by_stage[AppAsyncStage.OBSERVATION],
            armed_images=armed_images,
        )
        conclusion = AppAsyncConclusion(observation["conclusion"])
        payload_value = {
            "schema_version": APP_ASYNC_RECEIPT_SCHEMA_VERSION,
            "kind": "app_async_observation_receipt",
            "manifest_id": reference.manifest_id,
            "manifest_sha256": reference.manifest_sha256,
            "operation_id": reference.operation_id,
            "created_utc": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "collector_series_sha256": {
                stage.value: series_sha256_by_stage[stage] for stage in AppAsyncStage
            },
            "ui_action_attestation": {
                "sha256": action_attestation.attestation_sha256,
                "evidence_grade": "c",
                "completed_ordinals": list(action_attestation.completed_ordinals),
                "statement": action_attestation.statement,
            },
            "checkpoints": checkpoint_results,
            "observation": observation,
            "exact_restore": manifest.exact_restore.model_dump(mode="json"),
            "restore_status": "precommitted_and_staged_not_claimed_complete_by_this_receipt",
            "stability_duration_claimed": False,
            "physical_claim_scope": APP_ASYNC_PHYSICAL_CLAIM_SCOPE,
        }
        payload = _canonical_json(payload_value)
        digest = hashlib.sha256(payload).hexdigest()
        receipt_id = f"{_RECEIPT_PREFIX}-{digest}"
        try:
            _write_exclusive(reference.directory / "receipt.json", payload)
            _write_exclusive(
                reference.directory / "receipt.commit.json",
                _canonical_json(
                    {
                        "schema_version": APP_ASYNC_RECEIPT_SCHEMA_VERSION,
                        "receipt_id": receipt_id,
                        "receipt_sha256": digest,
                    }
                ),
            )
            _fsync_directory(reference.directory)
        except OSError as error:
            raise AppAsyncObservationError("app_async_receipt_durability_unconfirmed") from error
        receipt = AppAsyncReceiptReference(
            receipt_id=receipt_id,
            receipt_sha256=digest,
            manifest_id=reference.manifest_id,
            manifest_sha256=reference.manifest_sha256,
            operation_id=reference.operation_id,
            conclusion=conclusion,
            directory=reference.directory,
        )
        self.verify_receipt(reference, receipt)
        return receipt

    @staticmethod
    def _verify_restore_still_staged(
        manifest: AppAsyncManifest,
        verifier: AppExactRestoreEvidenceVerifier,
        now: datetime,
    ) -> None:
        if now.tzinfo is None or now.utcoffset() != timedelta(0):
            raise AppAsyncObservationError("app_async_verification_clock_invalid")
        expected = _restore_dataclass(manifest.exact_restore)
        if verifier.verify_q2_exact_restore_evidence(expected, not_after=now) != expected:
            raise AppAsyncObservationError("app_async_restore_stage_mismatch")

    def verify_receipt(
        self,
        manifest_reference: AppAsyncManifestReference,
        receipt_reference: AppAsyncReceiptReference,
    ) -> dict[str, Any]:
        if (
            receipt_reference.directory != manifest_reference.directory
            or receipt_reference.manifest_id != manifest_reference.manifest_id
            or receipt_reference.manifest_sha256 != manifest_reference.manifest_sha256
            or receipt_reference.operation_id != manifest_reference.operation_id
        ):
            raise AppAsyncObservationError("app_async_receipt_reference_invalid")
        payload = _read_private_file(
            receipt_reference.directory / "receipt.json",
            "app_async_receipt_file_invalid",
        )
        marker_payload = _read_private_file(
            receipt_reference.directory / "receipt.commit.json",
            "app_async_receipt_marker_invalid",
        )
        digest = hashlib.sha256(payload).hexdigest()
        try:
            value = json.loads(payload)
            marker = json.loads(marker_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise AppAsyncObservationError("app_async_receipt_json_invalid") from error
        if (
            not secrets.compare_digest(digest, receipt_reference.receipt_sha256)
            or receipt_reference.receipt_id != f"{_RECEIPT_PREFIX}-{digest}"
            or marker
            != {
                "schema_version": APP_ASYNC_RECEIPT_SCHEMA_VERSION,
                "receipt_id": receipt_reference.receipt_id,
                "receipt_sha256": digest,
            }
            or not isinstance(value, dict)
            or value.get("manifest_id") != manifest_reference.manifest_id
            or value.get("manifest_sha256") != manifest_reference.manifest_sha256
            or value.get("operation_id") != manifest_reference.operation_id
            or value.get("physical_claim_scope") != APP_ASYNC_PHYSICAL_CLAIM_SCOPE
            or value.get("stability_duration_claimed") is not False
            or not isinstance(value.get("observation"), dict)
            or value["observation"].get("conclusion") != receipt_reference.conclusion.value
        ):
            raise AppAsyncObservationError("app_async_receipt_binding_invalid")
        return value


def _decode_sample(sample: Any, policy: CaptureTransportPolicy) -> _DecodedSample:
    if sample.role not in {"a", "b"}:
        raise AppAsyncObservationError("app_async_sample_role_invalid")
    frame = decode_frame(sample.raw_wire_frame)
    accepted = (
        {STATE_REPLY_ACTION, STATE_REPORT_ACTION}
        if policy is CaptureTransportPolicy.REPLY_OR_REPORT
        else {STATE_REPLY_ACTION}
    )
    if (
        frame.command != GizwitsCommand.SERIAL_TRANSMIT_RESPONSE
        or len(frame.payload) != 453
        or frame.payload[0] not in accepted
    ):
        raise AppAsyncObservationError("app_async_state_frame_invalid")
    raw = frame.payload[1:]
    schema = get_product_schema(LOCAL_WAVEMAKER_PRO_PRODUCT_KEY)
    values = schema.decode_status(raw)
    schedule = decode_schedule(
        LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        raw,
        enabled=bool(values["TimerON"]),
    )
    if schedule is None or schedule.device_local_time is None:
        raise AppAsyncObservationError("app_async_schedule_decode_invalid")
    for name in ("Flow", "Frequency", "AutoFlow", "AutoFreq"):
        value = values.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise AppAsyncObservationError("app_async_control_value_invalid")
    image = LocalWavemakerProScheduleSnapshot.from_status(raw).image
    return _DecodedSample(
        participant="master" if sample.role == "a" else "slave",
        identity_binding_sha256=sample.identity_binding_sha256,
        action=f"0x{frame.payload[0]:02x}",
        read_started_ns=sample.read.started_monotonic_ns,
        read_completed_ns=sample.read.completed_monotonic_ns,
        device_time=schedule.device_local_time,
        enabled=bool(values["SwitchON"]),
        timer_on=bool(values["TimerON"]),
        linkage=str(values["Linkage"]),
        manual=AppReportedTuple(
            mode=str(values["Mode"]),
            flow=values["Flow"],
            frequency=values["Frequency"],
        ),
        auto=AppReportedTuple(
            mode=str(values["AutoMode"]),
            flow=values["AutoFlow"],
            frequency=values["AutoFreq"],
        ),
        schedule_image=image,
        schedule_image_sha256=hashlib.sha256(image).hexdigest(),
        schedule_invalid_slots=tuple(schedule.invalid_slots),
        active_problems=tuple(schema.active_problems(values)),
    )


def _decode_pair(
    artifact: VerifiedPilotPairArtifact,
    policy: CaptureTransportPolicy,
) -> _DecodedPair:
    master = _decode_sample(artifact.samples[0], policy)
    slave = _decode_sample(artifact.samples[1], policy)
    if master.participant != "master" or slave.participant != "slave":
        raise AppAsyncObservationError("app_async_pair_order_invalid")
    return _DecodedPair(
        ordinal=artifact.ordinal,
        pair_manifest_sha256=artifact.pair_manifest_sha256,
        pair_gap_ns=artifact.pair_completion_gap_ns,
        master=master,
        slave=slave,
    )


def _binding_for(manifest: AppAsyncManifest, stage: AppAsyncStage) -> AppCollectorPlanBinding:
    return next(item for item in manifest.collector_plans if item.stage is stage)


def _load_series(
    manifest: AppAsyncManifest,
    collector_store: _VerifiedSeriesProvider,
    stage: AppAsyncStage,
    expected_series_sha256: str,
) -> tuple[dict[str, Any], list[_DecodedPair], list[dict[str, Any]]]:
    if not _is_sha256(expected_series_sha256):
        raise AppAsyncObservationError("app_async_series_digest_invalid")
    binding = _binding_for(manifest, stage)
    reference = collector_store.load(binding.series_id)
    plan = collector_store.verify_plan(reference)
    if _plan_binding(stage, reference, plan) != binding:
        raise AppAsyncObservationError("app_async_collector_plan_binding_invalid")
    series = collector_store.verify_completed_series(
        reference,
        expected_series_sha256=expected_series_sha256,
    )
    pairs: list[_DecodedPair] = []
    rejections: list[dict[str, Any]] = []
    policy = capture_transport_policy_from_acquisition(plan["acquisition"])
    for ordinal, record in enumerate(series["records"]):
        if record["outcome"] != "accepted":
            rejections.append({"ordinal": ordinal, "reason": f"acquisition_{record['outcome']}"})
            continue
        artifact = collector_store.extract_verified_accepted_pair(
            reference,
            expected_series_sha256=expected_series_sha256,
            ordinal=ordinal,
        )
        pairs.append(_decode_pair(artifact, policy))
    return series, pairs, rejections


def _normalize_unused_slots(image: bytes) -> bytes:
    if len(image) != LOCAL_WAVEMAKER_PRO_SCHEDULE_IMAGE_SIZE:
        raise AppAsyncObservationError("app_async_schedule_image_size_invalid")
    normalized = bytearray(image)
    for slot in range(LOCAL_WAVEMAKER_PRO_SLOT_COUNT):
        start = slot * LOCAL_WAVEMAKER_PRO_SLOT_SIZE
        wire = image[start : start + LOCAL_WAVEMAKER_PRO_SLOT_SIZE]
        if wire in {LOCAL_WAVEMAKER_PRO_UNUSED_ZERO, LOCAL_WAVEMAKER_PRO_UNUSED_EE}:
            normalized[start : start + LOCAL_WAVEMAKER_PRO_SLOT_SIZE] = (
                LOCAL_WAVEMAKER_PRO_UNUSED_EE
            )
    return bytes(normalized)


def _public_sample(sample: _DecodedSample) -> dict[str, Any]:
    return {
        "participant": sample.participant,
        "identity_binding_sha256": sample.identity_binding_sha256,
        "action": sample.action,
        "read_started_ns": sample.read_started_ns,
        "read_completed_ns": sample.read_completed_ns,
        "device_time": sample.device_time.isoformat(),
        "enabled": sample.enabled,
        "timer_on": sample.timer_on,
        "linkage": sample.linkage,
        "manual": sample.manual.model_dump(mode="json"),
        "auto": sample.auto.model_dump(mode="json"),
        "schedule_image_sha256": sample.schedule_image_sha256,
        "schedule_invalid_slots": list(sample.schedule_invalid_slots),
        "active_problems": list(sample.active_problems),
    }


def _public_pair(pair: _DecodedPair) -> dict[str, Any]:
    return {
        "ordinal": pair.ordinal,
        "pair_manifest_sha256": pair.pair_manifest_sha256,
        "pair_gap_ns": pair.pair_gap_ns,
        "device_clock_delta_seconds": int(
            (pair.slave.device_time - pair.master.device_time).total_seconds()
        ),
        "master": _public_sample(pair.master),
        "slave": _public_sample(pair.slave),
    }


def _action_counts(pairs: Sequence[_DecodedPair]) -> dict[str, int]:
    return {
        action: sum(
            sample.action == action for pair in pairs for sample in (pair.master, pair.slave)
        )
        for action in ("0x03", "0x04")
    }


def _verified_series_action_counts(
    series: Mapping[str, Any],
    pairs: Sequence[_DecodedPair],
) -> dict[str, int]:
    claim = series.get("action_counts")
    if claim is None:
        return _action_counts(pairs)
    if (
        not isinstance(claim, dict)
        or set(claim) != {"0x03", "0x04"}
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in claim.values()
        )
    ):
        raise AppAsyncObservationError("app_async_series_action_counts_invalid")
    return {action: claim[action] for action in ("0x03", "0x04")}


def _checkpoint_roles(stage: AppAsyncStage) -> tuple[str, str]:
    return {
        AppAsyncStage.SCHEDULES_ARMED: ("independent", "independent"),
        AppAsyncStage.SYNC_SELECTED: ("master", "sync_slave"),
        AppAsyncStage.ASYNC_SELECTED: ("master", "async_slave"),
    }[stage]


def _verify_checkpoint_chain(
    manifest: AppAsyncManifest,
    collector_store: _VerifiedSeriesProvider,
    series_sha256_by_stage: Mapping[AppAsyncStage, str],
    stages: Sequence[AppAsyncStage],
) -> tuple[list[dict[str, Any]], tuple[bytes, bytes]]:
    armed_images: tuple[bytes, bytes] | None = None
    results: list[dict[str, Any]] = []
    expected_images = tuple(
        build_expected_schedule_image(item.participant, item.boundary_local)
        for item in manifest.schedules
    )
    for stage in stages:
        if stage is AppAsyncStage.OBSERVATION:
            raise AppAsyncObservationError("app_async_checkpoint_stage_invalid")
        series, pairs, rejections = _load_series(
            manifest,
            collector_store,
            stage,
            series_sha256_by_stage[stage],
        )
        if rejections or len(pairs) != _binding_for(manifest, stage).planned_pair_count:
            raise AppAsyncObservationError("app_async_checkpoint_acquisition_incomplete")
        expected_roles = _checkpoint_roles(stage)
        checkpoint_errors: list[dict[str, Any]] = []
        for pair in pairs:
            reasons: list[str] = []
            samples = (pair.master, pair.slave)
            if tuple(sample.identity_binding_sha256 for sample in samples) != (
                manifest.expected_identity_bindings_sha256
            ):
                reasons.append("identity_binding_mismatch")
            if tuple(sample.linkage for sample in samples) != expected_roles:
                reasons.append("role_mismatch")
            if any(not sample.enabled for sample in samples):
                reasons.append("device_not_enabled")
            if any(not sample.timer_on for sample in samples):
                reasons.append("timer_not_enabled")
            if any(sample.active_problems for sample in samples):
                reasons.append("active_problem")
            if any(sample.schedule_invalid_slots for sample in samples):
                reasons.append("schedule_invalid_slots")
            if any(
                sample.device_time.date() != manifest.schedules[index].boundary_local.date()
                for index, sample in enumerate(samples)
            ):
                reasons.append("device_date_mismatch")
            if stage is AppAsyncStage.SCHEDULES_ARMED:
                if any(
                    _normalize_unused_slots(sample.schedule_image) != expected_images[index]
                    for index, sample in enumerate(samples)
                ):
                    reasons.append("schedule_decoded_content_mismatch")
            elif armed_images is None or any(
                sample.schedule_image != armed_images[index] for index, sample in enumerate(samples)
            ):
                reasons.append("role_write_changed_schedule_bytes")
            if reasons:
                checkpoint_errors.append({"ordinal": pair.ordinal, "reasons": sorted(set(reasons))})
        if checkpoint_errors:
            raise AppAsyncObservationError("app_async_checkpoint_state_mismatch")
        current_images = (pairs[-1].master.schedule_image, pairs[-1].slave.schedule_image)
        if any(
            (pair.master.schedule_image, pair.slave.schedule_image) != current_images
            for pair in pairs
        ):
            raise AppAsyncObservationError("app_async_checkpoint_schedule_conflict")
        if stage is AppAsyncStage.SCHEDULES_ARMED:
            armed_images = current_images
        results.append(
            {
                "stage": stage.value,
                "series_id": _binding_for(manifest, stage).series_id,
                "series_sha256": series_sha256_by_stage[stage],
                "status": "verified_from_preserved_raw",
                "evidence_grade": "a",
                "completed_pair_count": len(series["records"]),
                "action_counts": _verified_series_action_counts(series, pairs),
                "schedule_image_sha256": [
                    hashlib.sha256(image).hexdigest() for image in current_images
                ],
                "predicted_ee_encoding_exact": [
                    current_images[index] == expected_images[index] for index in range(2)
                ],
                "unused_encoding_observation": [
                    (
                        "predicted_ee_exact"
                        if current_images[index] == expected_images[index]
                        else "semantically_exact_unused_zero_or_ee_differs"
                    )
                    for index in range(2)
                ],
                "last_pair": _public_pair(pairs[-1]),
                "slave_manual_tuple": pairs[-1].slave.manual.model_dump(mode="json"),
                "slave_manual_tuples": [
                    {
                        "ordinal": pair.ordinal,
                        "tuple": pair.slave.manual.model_dump(mode="json"),
                    }
                    for pair in pairs
                ],
            }
        )
    if armed_images is None:
        raise AppAsyncObservationError("app_async_armed_checkpoint_missing")
    return results, armed_images


def _frequency_matches(observed: AppReportedTuple, expected: AppReportedTuple) -> bool:
    if observed.mode != expected.mode or observed.flow != expected.flow:
        return False
    if expected.mode == "constant" and expected.frequency == 0:
        return observed.frequency in {0, 5}
    return observed.frequency == expected.frequency


def _pair_window(
    pair: _DecodedPair,
    master_boundary: datetime,
    slave_boundary: datetime,
) -> AppAsyncWindow:
    master_time = pair.master.device_time
    slave_time = pair.slave.device_time
    master_boundary_end = master_boundary + _BOUNDARY_MINUTE
    slave_boundary_end = slave_boundary + _BOUNDARY_MINUTE
    if master_time < master_boundary and slave_time < slave_boundary:
        return AppAsyncWindow.PRE
    if master_time >= master_boundary_end and slave_time < slave_boundary:
        return AppAsyncWindow.MIXED
    if master_time >= master_boundary_end and slave_time >= slave_boundary_end:
        return AppAsyncWindow.POST
    return AppAsyncWindow.EXCLUDED


def _field_split(
    observed: AppReportedTuple,
    candidates: Sequence[AppReportedTuple],
) -> bool:
    matching_flow = [candidate for candidate in candidates if candidate.flow == observed.flow]
    return bool(matching_flow) and all(
        candidate.mode != observed.mode for candidate in matching_flow
    )


def _classify_slave_tuple(
    window: AppAsyncWindow,
    observed: AppReportedTuple,
    manual: AppReportedTuple,
    master_before: AppReportedTuple,
    slave_before: AppReportedTuple,
    master_after: AppReportedTuple,
    slave_after: AppReportedTuple,
) -> AppAsyncAttribution:
    if _frequency_matches(observed, manual):
        return AppAsyncAttribution.MANUAL_ECHO
    if window is AppAsyncWindow.PRE:
        mapping = (
            (master_before, AppAsyncAttribution.MASTER_CONTENT),
            (
                slave_before,
                AppAsyncAttribution.SLAVE_SIDE_CONTENT_OR_ROLE_ENTRY_MIRROR,
            ),
        )
    elif window is AppAsyncWindow.MIXED:
        mapping = (
            (master_after, AppAsyncAttribution.MASTER_TIMING_MASTER_CONTENT),
            (slave_after, AppAsyncAttribution.MASTER_TIMING_SLAVE_CONTENT),
            (master_before, AppAsyncAttribution.SLAVE_TIMING_MASTER_CONTENT),
            (
                slave_before,
                AppAsyncAttribution.SLAVE_TIMING_SLAVE_SIDE_OR_ROLE_ENTRY_MIRROR,
            ),
        )
    elif window is AppAsyncWindow.POST:
        mapping = (
            (master_after, AppAsyncAttribution.MASTER_CONTENT),
            (slave_after, AppAsyncAttribution.SLAVE_CONTENT),
        )
    else:
        raise AppAsyncObservationError("app_async_window_not_classifiable")
    for expected, attribution in mapping:
        if _frequency_matches(observed, expected):
            return attribution
    if _field_split(observed, [expected for expected, _ in mapping]):
        return AppAsyncAttribution.FIELD_SPLIT
    return AppAsyncAttribution.UNEXPECTED_EFFECTIVE_STATE


def _content_owner(attribution: AppAsyncAttribution) -> str | None:
    if attribution in {
        AppAsyncAttribution.MASTER_CONTENT,
        AppAsyncAttribution.MASTER_TIMING_MASTER_CONTENT,
        AppAsyncAttribution.SLAVE_TIMING_MASTER_CONTENT,
    }:
        return "master"
    if attribution in {
        AppAsyncAttribution.SLAVE_SIDE_CONTENT_OR_ROLE_ENTRY_MIRROR,
        AppAsyncAttribution.SLAVE_CONTENT,
        AppAsyncAttribution.MASTER_TIMING_SLAVE_CONTENT,
        AppAsyncAttribution.SLAVE_TIMING_SLAVE_SIDE_OR_ROLE_ENTRY_MIRROR,
    }:
        return "slave_side"
    return None


def _classify_observation_series(
    manifest: AppAsyncManifest,
    collector_store: _VerifiedSeriesProvider,
    *,
    series_sha256: str,
    armed_images: tuple[bytes, bytes],
) -> dict[str, Any]:
    stage = AppAsyncStage.OBSERVATION
    series, pairs, acquisition_rejections = _load_series(
        manifest,
        collector_store,
        stage,
        series_sha256,
    )
    master_schedule, slave_schedule = manifest.schedules
    manual = manifest.baseline.devices[1].manual
    window_records: dict[AppAsyncWindow, list[dict[str, Any]]] = {
        window: [] for window in (AppAsyncWindow.PRE, AppAsyncWindow.MIXED, AppAsyncWindow.POST)
    }
    excluded: list[dict[str, Any]] = []
    invariant_violations: list[dict[str, Any]] = []
    previous_times: tuple[datetime, datetime] | None = None
    clock_monotonic = True
    for pair in pairs:
        reasons: list[str] = []
        samples = (pair.master, pair.slave)
        if tuple(sample.identity_binding_sha256 for sample in samples) != (
            manifest.expected_identity_bindings_sha256
        ):
            reasons.append("identity_binding_mismatch")
        if (pair.master.linkage, pair.slave.linkage) != ("master", "async_slave"):
            reasons.append("role_mismatch")
        if any(not sample.enabled for sample in samples):
            reasons.append("device_not_enabled")
        if any(not sample.timer_on for sample in samples):
            reasons.append("timer_not_enabled")
        if any(sample.active_problems for sample in samples):
            reasons.append("active_problem")
        if any(sample.schedule_invalid_slots for sample in samples):
            reasons.append("schedule_invalid_slots")
        if any(
            sample.schedule_image != armed_images[index] for index, sample in enumerate(samples)
        ):
            reasons.append("schedule_bytes_changed_after_armed_checkpoint")
        if any(
            sample.device_time.date() != manifest.schedules[index].boundary_local.date()
            for index, sample in enumerate(samples)
        ):
            reasons.append("device_date_mismatch")
        current_times = (pair.master.device_time, pair.slave.device_time)
        if previous_times is not None and any(
            current < previous
            for previous, current in zip(previous_times, current_times, strict=True)
        ):
            clock_monotonic = False
            reasons.append("participant_device_clock_decreased")
        previous_times = current_times
        if reasons:
            invariant_violations.append(
                {
                    "ordinal": pair.ordinal,
                    "reasons": sorted(set(reasons)),
                    "pair": _public_pair(pair),
                }
            )
            continue
        window = _pair_window(
            pair,
            master_schedule.boundary_local,
            slave_schedule.boundary_local,
        )
        if window is AppAsyncWindow.EXCLUDED:
            excluded.append(
                {
                    "ordinal": pair.ordinal,
                    "reason": "participant_device_times_do_not_establish_required_window",
                    "pair": _public_pair(pair),
                }
            )
            continue
        expected_master = (
            master_schedule.before if window is AppAsyncWindow.PRE else master_schedule.after
        )
        if not _frequency_matches(pair.master.auto, expected_master):
            window_records[window].append(
                {
                    "ordinal": pair.ordinal,
                    "attribution": AppAsyncAttribution.UNEXPECTED_EFFECTIVE_STATE.value,
                    "reason": "same_pair_master_tuple_not_exact_for_window",
                    "pair": _public_pair(pair),
                }
            )
            continue
        attribution = _classify_slave_tuple(
            window,
            pair.slave.auto,
            manual,
            master_schedule.before,
            slave_schedule.before,
            master_schedule.after,
            slave_schedule.after,
        )
        window_records[window].append(
            {
                "ordinal": pair.ordinal,
                "attribution": attribution.value,
                "reason": "precommitted_tuple_mapping_applied",
                "pair": _public_pair(pair),
            }
        )

    summaries: dict[str, dict[str, Any]] = {}
    selected: dict[AppAsyncWindow, AppAsyncAttribution | None] = {}
    for window, records in window_records.items():
        attributions = [record["attribution"] for record in records]
        distinct = sorted(set(attributions))
        selected[window] = AppAsyncAttribution(distinct[0]) if len(distinct) == 1 else None
        summaries[window.value] = {
            "qualifying_pair_count": len(records),
            "distinct_attributions": distinct,
            "conflict": len(distinct) > 1,
            "records": records,
        }

    conclusion = AppAsyncConclusion.UNKNOWN
    reason = "required_window_missing_or_conflicted"
    pre = selected[AppAsyncWindow.PRE]
    mixed = selected[AppAsyncWindow.MIXED]
    post = selected[AppAsyncWindow.POST]
    if not clock_monotonic:
        reason = "participant_device_clock_not_monotonic"
    elif invariant_violations:
        reason = "observation_invariant_violation"
    elif all(window_records[window] for window in window_records) and all(
        selected[window] is not None for window in selected
    ):
        mixed_owner = _content_owner(mixed) if mixed is not None else None
        if (
            mixed
            in {
                AppAsyncAttribution.MASTER_TIMING_MASTER_CONTENT,
                AppAsyncAttribution.MASTER_TIMING_SLAVE_CONTENT,
                AppAsyncAttribution.SLAVE_TIMING_MASTER_CONTENT,
                AppAsyncAttribution.SLAVE_TIMING_SLAVE_SIDE_OR_ROLE_ENTRY_MIRROR,
            }
            and mixed_owner is not None
            and _content_owner(pre) == mixed_owner
            and _content_owner(post) == mixed_owner
        ):
            conclusion = AppAsyncConclusion(mixed.value)
            reason = "pre_mixed_post_content_consistent_with_mixed_timing_mapping"
        else:
            reason = "window_attributions_conflict_or_are_nonownership_observations"

    return {
        "conclusion": conclusion.value,
        "conclusion_reason": reason,
        "series_id": _binding_for(manifest, stage).series_id,
        "series_sha256": series_sha256,
        "completed_pair_count": len(series["records"]),
        "accepted_pair_count": len(pairs),
        "acquisition_rejections": acquisition_rejections,
        "action_counts": _verified_series_action_counts(series, pairs),
        "participant_device_clocks_monotonic": clock_monotonic,
        "window_results": summaries,
        "excluded_pairs": excluded,
        "invariant_violations": invariant_violations,
        "minimum_qualifying_pairs_per_window": 1,
        "conflicting_pairs_allowed": 0,
        "stability_duration_claimed": False,
        "physical_claim_scope": APP_ASYNC_PHYSICAL_CLAIM_SCOPE,
    }


def parse_action_attestation(value: object) -> AppUiActionAttestation:
    try:
        return AppUiActionAttestation.model_validate(value)
    except (TypeError, ValueError) as error:
        raise AppAsyncObservationError("app_async_action_attestation_invalid") from error


def parse_series_digest_map(value: object) -> dict[AppAsyncStage, str]:
    if not isinstance(value, Mapping):
        raise AppAsyncObservationError("app_async_series_digest_map_invalid")
    try:
        result = {AppAsyncStage(key): digest for key, digest in value.items()}
    except (TypeError, ValueError) as error:
        raise AppAsyncObservationError("app_async_series_digest_map_invalid") from error
    if any(not _is_sha256(digest) for digest in result.values()):
        raise AppAsyncObservationError("app_async_series_digest_map_invalid")
    return result


__all__ = [
    "APP_ASYNC_PHYSICAL_CLAIM_SCOPE",
    "AppAsyncConclusion",
    "AppAsyncManifestReference",
    "AppAsyncObservationError",
    "AppAsyncObservationStore",
    "AppAsyncReceiptReference",
    "AppAsyncStage",
    "AppUiActionAttestation",
    "build_expected_schedule_image",
    "expected_ui_actions",
    "parse_action_attestation",
    "parse_series_digest_map",
]
