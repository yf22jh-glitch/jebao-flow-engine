"""Offline-verifiable, write-free Q2 measurement epochs.

The existing collector owns acquisition and deliberately has no authority to answer Q2.  This
module adds that authority only through an immutable manifest published *after* a collector plan
is prepared and *before* the plan is run.  It never imports a device or hardware-write module.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import secrets
import stat
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from jebao_flow.protocol.codec import GizwitsCommand, decode_frame
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule import LOCAL_WAVEMAKER_PRO_PRODUCT_KEY, decode_schedule
from jebao_flow.protocol.schedule_wire import LocalWavemakerProScheduleSnapshot
from jebao_flow.protocol.session import STATE_REPLY_ACTION
from jebao_flow.read_only_collector import (
    PilotPlanReference,
    PilotSeriesStore,
    VerifiedPilotPairArtifact,
)

Q2_EPOCH_MANIFEST_SCHEMA_VERSION = 1
Q2_EPOCH_RECEIPT_SCHEMA_VERSION = 1
_MANIFEST_PREFIX = "Q2M"
_RECEIPT_PREFIX = "Q2R"
_SHA256_CHARS = frozenset("0123456789abcdef")
_SAFE_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
_STABILITY_FLOOR_NS = 300_000_000_000


class Q2EpochError(RuntimeError):
    """Privacy-safe error raised before or during offline Q2 verification."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class Q2EpochKind(StrEnum):
    INDEPENDENT_CONTROL = "independent_control"
    ASYNC = "async"


class BoundaryClassification(StrEnum):
    MASTER_FOLLOWING = "master_following"
    SLOT_NOT_APPLIED = "slot_not_applied"
    INDEPENDENTLY_APPLIED = "independently_applied"
    UNEXPECTED = "unexpected"


class EpochConclusion(StrEnum):
    MASTER_FOLLOWING = "master_following"
    SLOT_NOT_APPLIED = "slot_not_applied"
    INDEPENDENTLY_APPLIED = "independently_applied"
    UNEXPECTED = "unexpected"
    UNESTABLISHED = "unestablished"


class Q2FinalJudgment(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PilotTimingEvidence:
    """One already verified pilot series used to derive timing thresholds."""

    series_id: str
    series_sha256: str


@dataclass(frozen=True, slots=True)
class ExactRestoreEvidence:
    """Privacy-safe references produced by the exact-restore qualification workflow."""

    baseline_artifact_id: str
    baseline_sha256: str
    sentinel_receipt_id: str
    sentinel_receipt_sha256: str
    baseline_receipt_id: str
    baseline_receipt_sha256: str
    final_restore_operation_id: str
    final_restore_record_sha256: str
    expected_identity_bindings_sha256: tuple[str, str]


@dataclass(frozen=True, slots=True)
class Q2Thresholds:
    requested_cadence_ns: int
    maximum_actual_cadence_ns: int
    maximum_pair_gap_ns: int
    freshness_window_ns: int
    boundary_exclusion_ns: int
    stability_window_ns: int


@dataclass(frozen=True, slots=True)
class Q2ScheduleExpectation:
    master_schedule_image_sha256: str
    slave_schedule_image_sha256: str
    master_flow: int = 35
    slave_flows: tuple[int, int] = (32, 40)
    required_mode: str = "constant"
    master_slot_count: int = 1
    slave_slot_count: int = 48
    slave_slot_duration_seconds: int = 1_800


@dataclass(frozen=True, slots=True)
class Q2EpochManifestReference:
    manifest_id: str
    manifest_sha256: str
    epoch_kind: Q2EpochKind
    collector_plan: PilotPlanReference
    directory: Path


@dataclass(frozen=True, slots=True)
class Q2EpochReceiptReference:
    receipt_id: str
    receipt_sha256: str
    manifest_id: str
    manifest_sha256: str
    epoch_kind: Q2EpochKind
    conclusion: EpochConclusion
    directory: Path


@dataclass(frozen=True, slots=True)
class Q2CombinedResult:
    judgment: Q2FinalJudgment
    reason: str
    control_receipt_sha256: str
    async_receipt_sha256: str


@dataclass(frozen=True, slots=True)
class _DecodedSample:
    role: str
    identity_binding_sha256: str
    read_started_ns: int
    read_completed_ns: int
    device_time: datetime
    enabled: bool
    timer_on: bool
    linkage: str
    auto_mode: str
    auto_flow: int
    auto_frequency: int
    schedule_image_sha256: str
    schedule_entries: tuple[dict[str, Any], ...]
    liveness_valid: bool


@dataclass(frozen=True, slots=True)
class _DecodedPair:
    ordinal: int
    pair_started_ns: int
    pair_gap_ns: int
    master: _DecodedSample
    slave: _DecodedSample


class _VerifiedSeriesProvider(Protocol):
    def verify_plan(self, reference: PilotPlanReference) -> dict[str, Any]: ...

    def load(self, series_id: str) -> PilotPlanReference: ...

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


class ExactRestoreEvidenceVerifier(Protocol):
    """Adapter implemented by the durable exact-restore archive composition."""

    def verify_q2_exact_restore_evidence(
        self,
        evidence: ExactRestoreEvidence,
        *,
        not_after: datetime,
    ) -> ExactRestoreEvidence: ...

    def verify_q2_historical_exact_restore_evidence(
        self,
        evidence: ExactRestoreEvidence,
        *,
        not_after: datetime,
    ) -> ExactRestoreEvidence: ...


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
        raise Q2EpochError("q2_artifact_id_invalid")
    return value


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _write_exclusive(path: Path, payload: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            if count <= 0:
                raise OSError("short write")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_private_file(path: Path, code: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except (FileNotFoundError, OSError) as error:
        raise Q2EpochError(code) from error
    try:
        before = os.fstat(descriptor)
        linked = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_uid != os.geteuid()
            or (before.st_dev, before.st_ino) != (linked.st_dev, linked.st_ino)
        ):
            raise Q2EpochError(code)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        linked_after = path.lstat()
        stable = (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) == (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if (
            not stable
            or (after.st_dev, after.st_ino) != (linked_after.st_dev, linked_after.st_ino)
            or sum(map(len, chunks)) != after.st_size
        ):
            raise Q2EpochError(code)
        return b"".join(chunks)
    except (OSError, FileNotFoundError) as error:
        raise Q2EpochError(code) from error
    finally:
        os.close(descriptor)


def _require_private_root(root: Path) -> None:
    try:
        metadata = root.lstat()
    except (FileNotFoundError, OSError) as error:
        raise Q2EpochError("q2_artifact_root_missing") from error
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or metadata.st_uid != os.geteuid()
    ):
        raise Q2EpochError("q2_artifact_root_not_private")


def _validate_thresholds(
    value: Q2Thresholds,
    plan: PilotPlanReference,
    schedule: Q2ScheduleExpectation,
) -> None:
    fields = asdict(value)
    if any(
        not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in fields.values()
    ):
        raise Q2EpochError("q2_threshold_invalid")
    if value.requested_cadence_ns != plan.requested_cadence_ns:
        raise Q2EpochError("q2_cadence_plan_mismatch")
    if value.maximum_actual_cadence_ns < value.requested_cadence_ns:
        raise Q2EpochError("q2_actual_cadence_limit_invalid")
    if value.stability_window_ns < _STABILITY_FLOOR_NS:
        raise Q2EpochError("q2_stability_below_floor")
    if value.boundary_exclusion_ns >= value.freshness_window_ns:
        raise Q2EpochError("q2_boundary_window_invalid")
    planned_span = (plan.planned_pair_count - 1) * value.requested_cadence_ns
    required_span = q2_minimum_series_span_ns(
        schedule,
        stability_window_ns=value.stability_window_ns,
    )
    if planned_span < required_span:
        raise Q2EpochError("q2_series_too_short_for_three_boundaries")


def q2_minimum_series_span_ns(
    schedule: Q2ScheduleExpectation,
    *,
    stability_window_ns: int = _STABILITY_FLOOR_NS,
) -> int:
    """Return the shortest span that can include three slot boundaries and stability."""

    return 3 * schedule.slave_slot_duration_seconds * 1_000_000_000 + stability_window_ns


def _validate_restore_evidence(value: ExactRestoreEvidence) -> None:
    for identifier in (
        value.baseline_artifact_id,
        value.sentinel_receipt_id,
        value.baseline_receipt_id,
        value.final_restore_operation_id,
    ):
        _safe_id(identifier)
    for digest in (
        value.baseline_sha256,
        value.sentinel_receipt_sha256,
        value.baseline_receipt_sha256,
        value.final_restore_record_sha256,
    ):
        if not _is_sha256(digest):
            raise Q2EpochError("q2_restore_evidence_digest_invalid")
    if (
        len(value.expected_identity_bindings_sha256) != 2
        or len(set(value.expected_identity_bindings_sha256)) != 2
        or any(not _is_sha256(item) for item in value.expected_identity_bindings_sha256)
    ):
        raise Q2EpochError("q2_restore_binding_invalid")


def _validate_schedule_expectation(value: Q2ScheduleExpectation) -> None:
    if not _is_sha256(value.master_schedule_image_sha256) or not _is_sha256(
        value.slave_schedule_image_sha256
    ):
        raise Q2EpochError("q2_schedule_digest_invalid")
    if value.master_flow != 35 or value.slave_flows != (32, 40):
        raise Q2EpochError("q2_discriminating_flows_invalid")
    if value.required_mode != "constant":
        raise Q2EpochError("q2_schedule_mode_invalid")
    if (
        value.master_slot_count != 1
        or value.slave_slot_count != 48
        or value.slave_slot_duration_seconds != 1_800
    ):
        raise Q2EpochError("q2_schedule_shape_invalid")


def _round_up_millisecond(value_ns: int, factor: float) -> int:
    return math.ceil((value_ns * factor) / 1_000_000) * 1_000_000


def _derive_thresholds(
    *,
    requested_cadence_ns: int,
    observed_cadence_ns: Sequence[int],
    observed_pair_gap_ns: Sequence[int],
) -> Q2Thresholds:
    """Apply the fixed v1 headroom policy to already verified pilot timing."""

    if not observed_cadence_ns or not observed_pair_gap_ns:
        raise Q2EpochError("q2_timing_evidence_samples_insufficient")
    maximum_actual_cadence = _round_up_millisecond(max(observed_cadence_ns), 1.10)
    maximum_pair_gap = _round_up_millisecond(max(observed_pair_gap_ns), 1.25)
    freshness = maximum_actual_cadence * 2
    exclusion = maximum_pair_gap
    return Q2Thresholds(
        requested_cadence_ns=requested_cadence_ns,
        maximum_actual_cadence_ns=maximum_actual_cadence,
        maximum_pair_gap_ns=maximum_pair_gap,
        freshness_window_ns=freshness,
        boundary_exclusion_ns=exclusion,
        stability_window_ns=_STABILITY_FLOOR_NS,
    )


def derive_q2_thresholds_from_pilots(
    collector_store: _VerifiedSeriesProvider,
    timing_evidence: Sequence[PilotTimingEvidence],
    *,
    requested_cadence_ns: int,
    expected_identity_bindings_sha256: tuple[str, str],
) -> Q2Thresholds:
    """Reverify timing series and apply the fixed threshold derivation policy."""

    if len(timing_evidence) < 2 or len(set(timing_evidence)) != len(timing_evidence):
        raise Q2EpochError("q2_timing_evidence_insufficient")
    observed_cadence_ns: list[int] = []
    observed_pair_gap_ns: list[int] = []
    for evidence in timing_evidence:
        reference = collector_store.load(_safe_id(evidence.series_id))
        plan = collector_store.verify_plan(reference)
        bindings = tuple(
            item["expected_identity_binding_sha256"] for item in plan["ordered_targets"]
        )
        if bindings != expected_identity_bindings_sha256:
            raise Q2EpochError("q2_timing_evidence_binding_mismatch")
        series = collector_store.verify_completed_series(
            reference,
            expected_series_sha256=evidence.series_sha256,
        )
        if (
            series.get("status") != "pilot_completed_all_acquisitions_accepted"
            or series.get("completed_pair_count") != reference.planned_pair_count
        ):
            raise Q2EpochError("q2_timing_evidence_not_all_accepted")
        artifacts = [
            collector_store.extract_verified_accepted_pair(
                reference,
                expected_series_sha256=evidence.series_sha256,
                ordinal=ordinal,
            )
            for ordinal in range(reference.planned_pair_count)
        ]
        if len(artifacts) < 2:
            raise Q2EpochError("q2_timing_evidence_samples_insufficient")
        starts = [artifact.attempt.started_monotonic_ns for artifact in artifacts]
        cadences = [
            current - previous for previous, current in zip(starts, starts[1:], strict=False)
        ]
        if any(value <= 0 for value in cadences):
            raise Q2EpochError("q2_timing_evidence_nonmonotonic")
        observed_cadence_ns.extend(cadences)
        observed_pair_gap_ns.extend(artifact.pair_completion_gap_ns for artifact in artifacts)
    return _derive_thresholds(
        requested_cadence_ns=requested_cadence_ns,
        observed_cadence_ns=observed_cadence_ns,
        observed_pair_gap_ns=observed_pair_gap_ns,
    )


def _parse_utc(value: object, code: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise Q2EpochError(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise Q2EpochError(code) from error
    return parsed.astimezone(UTC)


class Q2EpochStore:
    """Owner-only immutable Q2 manifests and offline classification receipts."""

    def __init__(self, root: Path) -> None:
        _require_private_root(root)
        self.root = root

    def prepare(
        self,
        collector_store: PilotSeriesStore,
        collector_plan: PilotPlanReference,
        *,
        epoch_kind: Q2EpochKind,
        timing_evidence: Sequence[PilotTimingEvidence],
        restore_evidence: ExactRestoreEvidence,
        restore_evidence_verifier: ExactRestoreEvidenceVerifier,
        thresholds: Q2Thresholds,
        schedule: Q2ScheduleExpectation,
        utc_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> Q2EpochManifestReference:
        """Commit the judgment criteria before the collector series starts."""

        created_at = utc_clock()
        if created_at.tzinfo is None or created_at.utcoffset() is None:
            raise Q2EpochError("q2_manifest_clock_not_aware")
        created_at = created_at.astimezone(UTC)
        plan = collector_store.verify_plan(collector_plan)
        if any(
            (collector_plan.series_directory / name).exists()
            for name in (
                "started.json",
                "series.json",
                "series.commit.json",
                "aborted.json",
                "aborted.commit.json",
            )
        ):
            raise Q2EpochError("q2_collector_series_already_started")
        try:
            kind = Q2EpochKind(epoch_kind)
        except ValueError as error:
            raise Q2EpochError("q2_epoch_kind_invalid") from error
        _validate_restore_evidence(restore_evidence)
        verified_restore = restore_evidence_verifier.verify_q2_exact_restore_evidence(
            restore_evidence,
            not_after=created_at,
        )
        if verified_restore != restore_evidence:
            raise Q2EpochError("q2_restore_evidence_verifier_mismatch")
        _validate_schedule_expectation(schedule)
        planned_bindings = tuple(
            item["expected_identity_binding_sha256"] for item in plan["ordered_targets"]
        )
        if planned_bindings != restore_evidence.expected_identity_bindings_sha256:
            raise Q2EpochError("q2_restore_binding_plan_mismatch")
        if len(timing_evidence) < 2:
            raise Q2EpochError("q2_timing_evidence_insufficient")
        verified_timing: list[dict[str, Any]] = []
        observed_cadence_ns: list[int] = []
        observed_pair_gap_ns: list[int] = []
        seen_series: set[str] = set()
        for evidence in timing_evidence:
            _safe_id(evidence.series_id)
            if not _is_sha256(evidence.series_sha256) or evidence.series_id in seen_series:
                raise Q2EpochError("q2_timing_evidence_invalid")
            seen_series.add(evidence.series_id)
            reference = collector_store.load(evidence.series_id)
            timing_plan = collector_store.verify_plan(reference)
            timing_bindings = tuple(
                item["expected_identity_binding_sha256"] for item in timing_plan["ordered_targets"]
            )
            if timing_bindings != planned_bindings:
                raise Q2EpochError("q2_timing_evidence_binding_mismatch")
            series = collector_store.verify_completed_series(
                reference,
                expected_series_sha256=evidence.series_sha256,
            )
            if (
                series.get("status") != "pilot_completed_all_acquisitions_accepted"
                or series.get("completed_pair_count") != reference.planned_pair_count
            ):
                raise Q2EpochError("q2_timing_evidence_not_all_accepted")
            artifacts = [
                collector_store.extract_verified_accepted_pair(
                    reference,
                    expected_series_sha256=evidence.series_sha256,
                    ordinal=ordinal,
                )
                for ordinal in range(reference.planned_pair_count)
            ]
            if len(artifacts) < 2:
                raise Q2EpochError("q2_timing_evidence_samples_insufficient")
            starts = [artifact.attempt.started_monotonic_ns for artifact in artifacts]
            evidence_cadences = [
                current - previous for previous, current in zip(starts, starts[1:], strict=False)
            ]
            if any(value <= 0 for value in evidence_cadences):
                raise Q2EpochError("q2_timing_evidence_nonmonotonic")
            evidence_gaps = [artifact.pair_completion_gap_ns for artifact in artifacts]
            observed_cadence_ns.extend(evidence_cadences)
            observed_pair_gap_ns.extend(evidence_gaps)
            verified_timing.append(
                {
                    "plan_artifact_id": reference.plan_artifact_id,
                    "plan_sha256": reference.plan_sha256,
                    "series_id": reference.series_id,
                    "series_sha256": evidence.series_sha256,
                    "utc_started": series["started"]["started_utc"],
                    "utc_completed": series["completed"]["completed_utc"],
                    "observed_maximum_cadence_ns": max(evidence_cadences),
                    "observed_maximum_pair_gap_ns": max(evidence_gaps),
                }
            )
        derived_thresholds = _derive_thresholds(
            requested_cadence_ns=collector_plan.requested_cadence_ns,
            observed_cadence_ns=observed_cadence_ns,
            observed_pair_gap_ns=observed_pair_gap_ns,
        )
        if thresholds != derived_thresholds:
            raise Q2EpochError("q2_thresholds_not_pilot_derived")
        _validate_thresholds(thresholds, collector_plan, schedule)

        expected_roles = (
            ["independent", "independent"]
            if kind is Q2EpochKind.INDEPENDENT_CONTROL
            else ["master", "async_slave"]
        )
        manifest_id = f"{_MANIFEST_PREFIX}-{secrets.token_hex(16)}"
        receipt_id = f"{_RECEIPT_PREFIX}-{secrets.token_hex(16)}"
        final_path = self.root / manifest_id
        temporary = self.root / f".{manifest_id}.tmp-{secrets.token_hex(8)}"
        manifest = {
            "schema_version": Q2_EPOCH_MANIFEST_SCHEMA_VERSION,
            "kind": "q2_readonly_epoch_manifest",
            "manifest_id": manifest_id,
            "receipt_id": receipt_id,
            "epoch_kind": kind.value,
            "created_utc": created_at.isoformat().replace("+00:00", "Z"),
            "collector": {
                "plan_artifact_id": collector_plan.plan_artifact_id,
                "plan_sha256": collector_plan.plan_sha256,
                "series_id": collector_plan.series_id,
                "planned_pair_count": collector_plan.planned_pair_count,
                "planned_ordinals": list(range(collector_plan.planned_pair_count)),
                "retry_policy": "none",
                "complete_series_required": True,
                "q2_interpretation_authority": "this_precommitted_manifest_only",
            },
            "expected_identity_bindings_sha256": list(planned_bindings),
            "expected_roles": expected_roles,
            "schedule_expectation": {
                **asdict(schedule),
                "slave_flows": list(schedule.slave_flows),
                "all_active_slots_required": True,
                "slave_flow_order": "strictly_alternating_circular",
            },
            "thresholds": asdict(thresholds),
            "threshold_derivation": {
                "policy": "pilot-max-v1",
                "actual_cadence_headroom": "110_percent_rounded_up_to_millisecond",
                "pair_gap_headroom": "125_percent_rounded_up_to_millisecond",
                "freshness": "twice_maximum_actual_cadence",
                "boundary_exclusion": "maximum_pair_gap",
                "stability": "fixed_300_second_floor",
            },
            "timing_evidence": verified_timing,
            "exact_restore": {
                **asdict(restore_evidence),
                "expected_identity_bindings_sha256": list(
                    restore_evidence.expected_identity_bindings_sha256
                ),
                "qualification_status": "qualified_and_phase5_restore_staged_before_app_write",
            },
            "acquisition_contract": {
                "write_count": 0,
                "fresh_discovery_before_and_after_each_read": True,
                "fresh_authenticated_session_per_sample": True,
                "reads_per_session": 1,
                "accept_reports": False,
                "required_action": "0x03",
                "raw_before_decode": True,
                "all_ordinals_and_failures_preserved": True,
                "application_control": "out_of_scope_never_invoked",
            },
            "judgment_contract": {
                "consecutive_same_valid_boundaries": 3,
                "conflicting_valid_boundaries": 0,
                "post_third_boundary_stability_required": True,
                "rejected_boundary_breaks_consecutive_run": True,
                "final_mapping_requires_both_epoch_receipts": True,
            },
        }
        payload = _canonical_json(manifest)
        digest = hashlib.sha256(payload).hexdigest()
        try:
            temporary.mkdir(mode=0o700)
            _write_exclusive(temporary / "manifest.json", payload)
            _write_exclusive(
                temporary / "manifest.commit.json",
                _canonical_json(
                    {
                        "schema_version": Q2_EPOCH_MANIFEST_SCHEMA_VERSION,
                        "manifest_id": manifest_id,
                        "manifest_sha256": digest,
                    }
                ),
            )
            _fsync_directory(temporary)
            os.rename(temporary, final_path)
            _fsync_directory(self.root)
        except OSError as error:
            raise Q2EpochError("q2_manifest_durability_unconfirmed") from error
        reference = Q2EpochManifestReference(
            manifest_id=manifest_id,
            manifest_sha256=digest,
            epoch_kind=kind,
            collector_plan=collector_plan,
            directory=final_path,
        )
        self.verify_manifest(reference)
        return reference

    def load_manifest(
        self,
        manifest_id: str,
        collector_store: PilotSeriesStore,
        *,
        expected_manifest_sha256: str,
    ) -> Q2EpochManifestReference:
        """Reconstruct a manifest using its externally recorded trust-root digest."""

        safe_id = _safe_id(manifest_id)
        if not _is_sha256(expected_manifest_sha256):
            raise Q2EpochError("q2_manifest_expected_digest_invalid")
        directory = self.root / safe_id
        payload = _read_private_file(directory / "manifest.json", "q2_manifest_file_invalid")
        marker_payload = _read_private_file(
            directory / "manifest.commit.json", "q2_manifest_marker_invalid"
        )
        try:
            manifest = json.loads(payload)
            marker = json.loads(marker_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Q2EpochError("q2_manifest_json_invalid") from error
        digest = hashlib.sha256(payload).hexdigest()
        if (
            not secrets.compare_digest(digest, expected_manifest_sha256)
            or not isinstance(manifest, dict)
            or marker
            != {
                "schema_version": Q2_EPOCH_MANIFEST_SCHEMA_VERSION,
                "manifest_id": safe_id,
                "manifest_sha256": digest,
            }
            or not isinstance(manifest.get("collector"), dict)
        ):
            raise Q2EpochError("q2_manifest_marker_mismatch")
        collector_series_id = manifest["collector"].get("series_id")
        try:
            epoch_kind = Q2EpochKind(manifest.get("epoch_kind"))
        except (TypeError, ValueError) as error:
            raise Q2EpochError("q2_epoch_kind_invalid") from error
        collector_plan = collector_store.load(_safe_id(collector_series_id))
        reference = Q2EpochManifestReference(
            manifest_id=safe_id,
            manifest_sha256=expected_manifest_sha256,
            epoch_kind=epoch_kind,
            collector_plan=collector_plan,
            directory=directory,
        )
        self.verify_manifest(reference)
        return reference

    def verify_manifest(self, reference: Q2EpochManifestReference) -> dict[str, Any]:
        _safe_id(reference.manifest_id)
        if not _is_sha256(reference.manifest_sha256):
            raise Q2EpochError("q2_manifest_reference_invalid")
        directory = self.root / reference.manifest_id
        try:
            metadata = directory.lstat()
        except (FileNotFoundError, OSError) as error:
            raise Q2EpochError("q2_manifest_directory_invalid") from error
        if (
            directory != reference.directory
            or not stat.S_ISDIR(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.geteuid()
        ):
            raise Q2EpochError("q2_manifest_directory_invalid")
        payload = _read_private_file(directory / "manifest.json", "q2_manifest_file_invalid")
        digest = hashlib.sha256(payload).hexdigest()
        if not secrets.compare_digest(digest, reference.manifest_sha256):
            raise Q2EpochError("q2_manifest_digest_mismatch")
        marker_payload = _read_private_file(
            directory / "manifest.commit.json", "q2_manifest_marker_invalid"
        )
        try:
            manifest = json.loads(payload)
            marker = json.loads(marker_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Q2EpochError("q2_manifest_json_invalid") from error
        if marker != {
            "schema_version": Q2_EPOCH_MANIFEST_SCHEMA_VERSION,
            "manifest_id": reference.manifest_id,
            "manifest_sha256": reference.manifest_sha256,
        }:
            raise Q2EpochError("q2_manifest_marker_mismatch")
        self._validate_manifest_claim(manifest, reference)
        return manifest

    def _validate_manifest_claim(
        self, manifest: object, reference: Q2EpochManifestReference
    ) -> None:
        if not isinstance(manifest, dict):
            raise Q2EpochError("q2_manifest_claim_invalid")
        collector = manifest.get("collector")
        schedule = manifest.get("schedule_expectation")
        thresholds = manifest.get("thresholds")
        exact_restore = manifest.get("exact_restore")
        if (
            manifest.get("schema_version") != Q2_EPOCH_MANIFEST_SCHEMA_VERSION
            or manifest.get("kind") != "q2_readonly_epoch_manifest"
            or manifest.get("manifest_id") != reference.manifest_id
            or manifest.get("epoch_kind") != reference.epoch_kind.value
            or not isinstance(collector, dict)
            or collector.get("plan_artifact_id") != reference.collector_plan.plan_artifact_id
            or collector.get("plan_sha256") != reference.collector_plan.plan_sha256
            or collector.get("series_id") != reference.collector_plan.series_id
            or collector.get("planned_pair_count") != reference.collector_plan.planned_pair_count
            or collector.get("planned_ordinals")
            != list(range(reference.collector_plan.planned_pair_count))
            or collector.get("retry_policy") != "none"
            or collector.get("complete_series_required") is not True
            or not isinstance(schedule, dict)
            or not isinstance(thresholds, dict)
            or not isinstance(exact_restore, dict)
        ):
            raise Q2EpochError("q2_manifest_claim_invalid")
        try:
            threshold_value = Q2Thresholds(**thresholds)
            schedule_value = Q2ScheduleExpectation(
                master_schedule_image_sha256=schedule["master_schedule_image_sha256"],
                slave_schedule_image_sha256=schedule["slave_schedule_image_sha256"],
                master_flow=schedule["master_flow"],
                slave_flows=tuple(schedule["slave_flows"]),
                required_mode=schedule["required_mode"],
                master_slot_count=schedule["master_slot_count"],
                slave_slot_count=schedule["slave_slot_count"],
                slave_slot_duration_seconds=schedule["slave_slot_duration_seconds"],
            )
            restore_value = ExactRestoreEvidence(
                baseline_artifact_id=exact_restore["baseline_artifact_id"],
                baseline_sha256=exact_restore["baseline_sha256"],
                sentinel_receipt_id=exact_restore["sentinel_receipt_id"],
                sentinel_receipt_sha256=exact_restore["sentinel_receipt_sha256"],
                baseline_receipt_id=exact_restore["baseline_receipt_id"],
                baseline_receipt_sha256=exact_restore["baseline_receipt_sha256"],
                final_restore_operation_id=exact_restore["final_restore_operation_id"],
                final_restore_record_sha256=exact_restore["final_restore_record_sha256"],
                expected_identity_bindings_sha256=tuple(
                    exact_restore["expected_identity_bindings_sha256"]
                ),
            )
        except (KeyError, TypeError) as error:
            raise Q2EpochError("q2_manifest_claim_invalid") from error
        _validate_thresholds(threshold_value, reference.collector_plan, schedule_value)
        _validate_schedule_expectation(schedule_value)
        _validate_restore_evidence(restore_value)
        expected_roles = (
            ["independent", "independent"]
            if reference.epoch_kind is Q2EpochKind.INDEPENDENT_CONTROL
            else ["master", "async_slave"]
        )
        bindings = manifest.get("expected_identity_bindings_sha256")
        if (
            manifest.get("expected_roles") != expected_roles
            or not isinstance(bindings, list)
            or len(bindings) != 2
            or len(set(bindings)) != 2
            or any(not _is_sha256(item) for item in bindings)
            or exact_restore.get("expected_identity_bindings_sha256") != bindings
            or exact_restore.get("qualification_status")
            != "qualified_and_phase5_restore_staged_before_app_write"
            or not isinstance(manifest.get("timing_evidence"), list)
            or len(manifest["timing_evidence"]) < 2
        ):
            raise Q2EpochError("q2_manifest_claim_invalid")
        _parse_utc(manifest.get("created_utc"), "q2_manifest_created_time_invalid")

    def classify_and_commit(
        self,
        reference: Q2EpochManifestReference,
        collector_store: PilotSeriesStore,
        *,
        expected_series_sha256: str,
        restore_evidence_verifier: ExactRestoreEvidenceVerifier,
    ) -> Q2EpochReceiptReference:
        manifest = self.verify_manifest(reference)
        restore_cutoff = _parse_utc(manifest["created_utc"], "q2_manifest_created_time_invalid")
        restore_claim = manifest["exact_restore"]
        restore_evidence = ExactRestoreEvidence(
            baseline_artifact_id=restore_claim["baseline_artifact_id"],
            baseline_sha256=restore_claim["baseline_sha256"],
            sentinel_receipt_id=restore_claim["sentinel_receipt_id"],
            sentinel_receipt_sha256=restore_claim["sentinel_receipt_sha256"],
            baseline_receipt_id=restore_claim["baseline_receipt_id"],
            baseline_receipt_sha256=restore_claim["baseline_receipt_sha256"],
            final_restore_operation_id=restore_claim["final_restore_operation_id"],
            final_restore_record_sha256=restore_claim["final_restore_record_sha256"],
            expected_identity_bindings_sha256=tuple(
                restore_claim["expected_identity_bindings_sha256"]
            ),
        )
        if (
            restore_evidence_verifier.verify_q2_historical_exact_restore_evidence(
                restore_evidence,
                not_after=restore_cutoff,
            )
            != restore_evidence
        ):
            raise Q2EpochError("q2_restore_evidence_verifier_mismatch")
        observed_cadence_ns: list[int] = []
        observed_pair_gap_ns: list[int] = []
        for evidence in manifest["timing_evidence"]:
            timing_reference = collector_store.load(evidence["series_id"])
            timing_plan = collector_store.verify_plan(timing_reference)
            timing_bindings = [
                item["expected_identity_binding_sha256"] for item in timing_plan["ordered_targets"]
            ]
            if timing_bindings != manifest["expected_identity_bindings_sha256"]:
                raise Q2EpochError("q2_timing_evidence_binding_mismatch")
            timing_series = collector_store.verify_completed_series(
                timing_reference,
                expected_series_sha256=evidence["series_sha256"],
            )
            if timing_series.get("status") != "pilot_completed_all_acquisitions_accepted":
                raise Q2EpochError("q2_timing_evidence_not_all_accepted")
            artifacts = [
                collector_store.extract_verified_accepted_pair(
                    timing_reference,
                    expected_series_sha256=evidence["series_sha256"],
                    ordinal=ordinal,
                )
                for ordinal in range(timing_reference.planned_pair_count)
            ]
            starts = [artifact.attempt.started_monotonic_ns for artifact in artifacts]
            observed_cadence_ns.extend(
                current - previous for previous, current in zip(starts, starts[1:], strict=False)
            )
            observed_pair_gap_ns.extend(artifact.pair_completion_gap_ns for artifact in artifacts)
        derived_thresholds = _derive_thresholds(
            requested_cadence_ns=reference.collector_plan.requested_cadence_ns,
            observed_cadence_ns=observed_cadence_ns,
            observed_pair_gap_ns=observed_pair_gap_ns,
        )
        if asdict(derived_thresholds) != manifest["thresholds"]:
            raise Q2EpochError("q2_thresholds_not_pilot_derived")
        plan = collector_store.verify_plan(reference.collector_plan)
        self._verify_plan_binding(manifest, plan)
        series = collector_store.verify_completed_series(
            reference.collector_plan,
            expected_series_sha256=expected_series_sha256,
        )
        records = series["records"]
        pairs: list[_DecodedPair | None] = []
        acquisition_rejections: list[dict[str, Any]] = []
        for ordinal, record in enumerate(records):
            if record["outcome"] != "accepted":
                pairs.append(None)
                acquisition_rejections.append(
                    {
                        "ordinal": ordinal,
                        "reason": f"acquisition_{record['outcome']}",
                    }
                )
                continue
            artifact = collector_store.extract_verified_accepted_pair(
                reference.collector_plan,
                expected_series_sha256=expected_series_sha256,
                ordinal=ordinal,
            )
            pairs.append(_decode_pair(artifact))
        result = _classify_complete_series(manifest, pairs, acquisition_rejections)
        receipt_id = manifest["receipt_id"]
        payload_value = {
            "schema_version": Q2_EPOCH_RECEIPT_SCHEMA_VERSION,
            "kind": "q2_readonly_epoch_receipt",
            "receipt_id": receipt_id,
            "manifest_id": reference.manifest_id,
            "manifest_sha256": reference.manifest_sha256,
            "epoch_kind": reference.epoch_kind.value,
            "collector_plan_artifact_id": reference.collector_plan.plan_artifact_id,
            "collector_plan_sha256": reference.collector_plan.plan_sha256,
            "collector_series_id": reference.collector_plan.series_id,
            "collector_series_sha256": expected_series_sha256,
            "planned_pair_count": reference.collector_plan.planned_pair_count,
            "completed_pair_count": len(records),
            "expected_identity_bindings_sha256": manifest["expected_identity_bindings_sha256"],
            "exact_restore": manifest["exact_restore"],
            "schedule_expectation": manifest["schedule_expectation"],
            **result,
        }
        payload = _canonical_json(payload_value)
        digest = hashlib.sha256(payload).hexdigest()
        try:
            _write_exclusive(reference.directory / "receipt.json", payload)
            _write_exclusive(
                reference.directory / "receipt.commit.json",
                _canonical_json(
                    {
                        "schema_version": Q2_EPOCH_RECEIPT_SCHEMA_VERSION,
                        "receipt_id": receipt_id,
                        "receipt_sha256": digest,
                    }
                ),
            )
            _fsync_directory(reference.directory)
        except OSError as error:
            raise Q2EpochError("q2_receipt_durability_unconfirmed") from error
        receipt = Q2EpochReceiptReference(
            receipt_id=receipt_id,
            receipt_sha256=digest,
            manifest_id=reference.manifest_id,
            manifest_sha256=reference.manifest_sha256,
            epoch_kind=reference.epoch_kind,
            conclusion=EpochConclusion(result["conclusion"]),
            directory=reference.directory,
        )
        self.verify_receipt(reference, receipt)
        return receipt

    @staticmethod
    def _verify_plan_binding(manifest: dict[str, Any], plan: dict[str, Any]) -> None:
        bindings = [
            target["expected_identity_binding_sha256"] for target in plan["ordered_targets"]
        ]
        collector = manifest["collector"]
        if (
            bindings != manifest["expected_identity_bindings_sha256"]
            or collector["planned_pair_count"] != plan["acquisition"]["planned_pair_count"]
            or manifest["thresholds"]["requested_cadence_ns"]
            != plan["acquisition"]["requested_cadence_ns"]
            or plan["acquisition"]["retry_policy"] != "none"
            or plan["acquisition"]["accept_reports"] is not False
        ):
            raise Q2EpochError("q2_collector_plan_binding_invalid")

    def verify_receipt(
        self,
        manifest_reference: Q2EpochManifestReference,
        receipt_reference: Q2EpochReceiptReference,
    ) -> dict[str, Any]:
        manifest = self.verify_manifest(manifest_reference)
        if (
            receipt_reference.receipt_id != manifest["receipt_id"]
            or receipt_reference.manifest_id != manifest_reference.manifest_id
            or receipt_reference.manifest_sha256 != manifest_reference.manifest_sha256
            or receipt_reference.epoch_kind is not manifest_reference.epoch_kind
        ):
            raise Q2EpochError("q2_receipt_reference_invalid")
        payload = _read_private_file(
            receipt_reference.directory / "receipt.json", "q2_receipt_file_invalid"
        )
        digest = hashlib.sha256(payload).hexdigest()
        if not secrets.compare_digest(digest, receipt_reference.receipt_sha256):
            raise Q2EpochError("q2_receipt_digest_mismatch")
        marker_payload = _read_private_file(
            receipt_reference.directory / "receipt.commit.json",
            "q2_receipt_marker_invalid",
        )
        try:
            receipt = json.loads(payload)
            marker = json.loads(marker_payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Q2EpochError("q2_receipt_json_invalid") from error
        if marker != {
            "schema_version": Q2_EPOCH_RECEIPT_SCHEMA_VERSION,
            "receipt_id": receipt_reference.receipt_id,
            "receipt_sha256": receipt_reference.receipt_sha256,
        }:
            raise Q2EpochError("q2_receipt_marker_mismatch")
        if (
            not isinstance(receipt, dict)
            or receipt.get("schema_version") != Q2_EPOCH_RECEIPT_SCHEMA_VERSION
            or receipt.get("kind") != "q2_readonly_epoch_receipt"
            or receipt.get("receipt_id") != manifest["receipt_id"]
            or receipt.get("manifest_id") != manifest_reference.manifest_id
            or receipt.get("manifest_sha256") != manifest_reference.manifest_sha256
            or receipt.get("epoch_kind") != manifest_reference.epoch_kind.value
            or receipt.get("conclusion") != receipt_reference.conclusion.value
            or receipt.get("planned_pair_count")
            != manifest_reference.collector_plan.planned_pair_count
            or receipt.get("completed_pair_count")
            != manifest_reference.collector_plan.planned_pair_count
            or receipt.get("expected_identity_bindings_sha256")
            != manifest["expected_identity_bindings_sha256"]
            or receipt.get("exact_restore") != manifest["exact_restore"]
            or receipt.get("schedule_expectation") != manifest["schedule_expectation"]
            or not isinstance(receipt.get("boundary_records"), list)
            or not isinstance(receipt.get("acquisition_rejections"), list)
        ):
            raise Q2EpochError("q2_receipt_claim_invalid")
        return receipt

    def load_receipt(
        self,
        manifest_reference: Q2EpochManifestReference,
        *,
        expected_receipt_sha256: str,
    ) -> tuple[Q2EpochReceiptReference, dict[str, Any]]:
        """Load a receipt using its externally recorded trust-root digest."""

        if not _is_sha256(expected_receipt_sha256):
            raise Q2EpochError("q2_receipt_expected_digest_invalid")

        marker_payload = _read_private_file(
            manifest_reference.directory / "receipt.commit.json",
            "q2_receipt_marker_invalid",
        )
        payload = _read_private_file(
            manifest_reference.directory / "receipt.json",
            "q2_receipt_file_invalid",
        )
        try:
            marker = json.loads(marker_payload)
            receipt = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise Q2EpochError("q2_receipt_json_invalid") from error
        digest = hashlib.sha256(payload).hexdigest()
        if (
            not secrets.compare_digest(digest, expected_receipt_sha256)
            or not isinstance(marker, dict)
            or marker.get("schema_version") != Q2_EPOCH_RECEIPT_SCHEMA_VERSION
            or marker.get("receipt_sha256") != digest
            or not isinstance(receipt, dict)
        ):
            raise Q2EpochError("q2_receipt_marker_mismatch")
        try:
            conclusion = EpochConclusion(receipt.get("conclusion"))
            epoch_kind = Q2EpochKind(receipt.get("epoch_kind"))
        except (TypeError, ValueError) as error:
            raise Q2EpochError("q2_receipt_claim_invalid") from error
        reference = Q2EpochReceiptReference(
            receipt_id=_safe_id(marker.get("receipt_id")),
            receipt_sha256=expected_receipt_sha256,
            manifest_id=manifest_reference.manifest_id,
            manifest_sha256=manifest_reference.manifest_sha256,
            epoch_kind=epoch_kind,
            conclusion=conclusion,
            directory=manifest_reference.directory,
        )
        verified = self.verify_receipt(manifest_reference, reference)
        return reference, verified


def _decode_pair(artifact: VerifiedPilotPairArtifact) -> _DecodedPair:
    samples = tuple(_decode_sample(sample) for sample in artifact.samples)
    if [sample.role for sample in samples] != ["a", "b"]:
        raise Q2EpochError("q2_pair_role_order_invalid")
    return _DecodedPair(
        ordinal=artifact.ordinal,
        pair_started_ns=artifact.attempt.started_monotonic_ns,
        pair_gap_ns=artifact.pair_completion_gap_ns,
        master=samples[0],
        slave=samples[1],
    )


def _decode_sample(sample: Any) -> _DecodedSample:
    frame = decode_frame(sample.raw_wire_frame)
    if (
        frame.command != GizwitsCommand.SERIAL_TRANSMIT_RESPONSE
        or len(frame.payload) != 453
        or frame.payload[0] != STATE_REPLY_ACTION
    ):
        raise Q2EpochError("q2_explicit_reply_invalid")
    raw = frame.payload[1:]
    schema = get_product_schema(LOCAL_WAVEMAKER_PRO_PRODUCT_KEY)
    values = schema.decode_status(raw)
    schedule = decode_schedule(
        LOCAL_WAVEMAKER_PRO_PRODUCT_KEY,
        raw,
        enabled=bool(values["TimerON"]),
    )
    if schedule is None or schedule.device_local_time is None:
        raise Q2EpochError("q2_schedule_decode_invalid")
    snapshot = LocalWavemakerProScheduleSnapshot.from_status(raw)
    entries = tuple(entry.model_dump(mode="json") for entry in schedule.entries)
    auto_flow = values.get("AutoFlow")
    if not isinstance(auto_flow, int) or isinstance(auto_flow, bool) or not 0 <= auto_flow <= 100:
        raise Q2EpochError("q2_auto_flow_invalid")
    auto_frequency = values.get("AutoFreq")
    if (
        not isinstance(auto_frequency, int)
        or isinstance(auto_frequency, bool)
        or not 0 <= auto_frequency <= 100
    ):
        raise Q2EpochError("q2_auto_frequency_invalid")
    return _DecodedSample(
        role=sample.role,
        identity_binding_sha256=sample.identity_binding_sha256,
        read_started_ns=sample.read.started_monotonic_ns,
        read_completed_ns=sample.read.completed_monotonic_ns,
        device_time=schedule.device_local_time,
        enabled=bool(values["SwitchON"]),
        timer_on=bool(values["TimerON"]),
        linkage=str(values["Linkage"]),
        auto_mode=str(values["AutoMode"]),
        auto_flow=auto_flow,
        auto_frequency=auto_frequency,
        schedule_image_sha256=hashlib.sha256(snapshot.image).hexdigest(),
        schedule_entries=entries,
        liveness_valid=not schema.active_problems(values) and not schedule.invalid_slots,
    )


def _seconds(value: str) -> int:
    hour, minute = (int(part) for part in value.split(":"))
    return hour * 3600 + minute * 60


def _active_entry(entries: tuple[dict[str, Any], ...], at: datetime) -> dict[str, Any] | None:
    second = at.hour * 3600 + at.minute * 60 + at.second
    matches = [
        entry for entry in entries if _seconds(entry["start"]) <= second < _seconds(entry["end"])
    ]
    return matches[0] if len(matches) == 1 else None


def _validate_schedule_shape(
    pair: _DecodedPair,
    schedule: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    if pair.master.schedule_image_sha256 != schedule["master_schedule_image_sha256"]:
        reasons.append("master_schedule_digest_changed")
    if pair.slave.schedule_image_sha256 != schedule["slave_schedule_image_sha256"]:
        reasons.append("slave_schedule_digest_changed")
    master_entries = pair.master.schedule_entries
    slave_entries = pair.slave.schedule_entries
    if not master_entries or not slave_entries:
        reasons.append("schedule_entries_missing")
        return reasons
    if (
        len(master_entries) != schedule["master_slot_count"]
        or _seconds(master_entries[0]["start"]) != 0
        or _seconds(master_entries[0]["end"]) != 86_400
        or any(
            entry["mode"] != "constant"
            or entry["parameters"].get("flow") != schedule["master_flow"]
            for entry in master_entries
        )
    ):
        reasons.append("master_schedule_not_constant_35")
    slave_values = [entry["parameters"].get("flow") for entry in slave_entries]
    expected = schedule["slave_flows"]
    duration = schedule["slave_slot_duration_seconds"]
    if (
        len(slave_values) != schedule["slave_slot_count"]
        or any(entry["mode"] != "constant" for entry in slave_entries)
        or any(value not in expected for value in slave_values)
        or any(
            _seconds(entry["start"]) != index * duration
            or _seconds(entry["end"]) != (index + 1) * duration
            for index, entry in enumerate(slave_entries)
        )
        or any(
            slave_values[index] == slave_values[(index + 1) % len(slave_values)]
            for index in range(len(slave_values))
        )
    ):
        reasons.append("slave_schedule_not_strict_32_40_alternation")
    return reasons


def _pair_invariant_reasons(pair: _DecodedPair, manifest: dict[str, Any]) -> list[str]:
    thresholds = manifest["thresholds"]
    bindings = manifest["expected_identity_bindings_sha256"]
    expected_roles = manifest["expected_roles"]
    reasons: list[str] = []
    if pair.pair_gap_ns > thresholds["maximum_pair_gap_ns"]:
        reasons.append("pair_gap_exceeded")
    if [pair.master.identity_binding_sha256, pair.slave.identity_binding_sha256] != bindings:
        reasons.append("identity_binding_mismatch")
    if not pair.master.liveness_valid or not pair.slave.liveness_valid:
        reasons.append("liveness_invalid")
    if not pair.master.enabled or not pair.slave.enabled:
        reasons.append("device_not_enabled")
    if not pair.master.timer_on or not pair.slave.timer_on:
        reasons.append("timer_not_enabled")
    if [pair.master.linkage, pair.slave.linkage] != expected_roles:
        reasons.append("role_invariant_failed")
    if pair.master.auto_mode != "constant" or pair.slave.auto_mode != "constant":
        reasons.append("auto_mode_not_constant")
    reasons.extend(_validate_schedule_shape(pair, manifest["schedule_expectation"]))
    return sorted(set(reasons))


def _flow_boundaries(
    entries: tuple[dict[str, Any], ...], start: datetime, end: datetime
) -> list[datetime]:
    if end <= start:
        return []
    start_seconds = {_seconds(entry["start"]) for entry in entries}
    dates = {start.date(), end.date(), (start - timedelta(days=1)).date()}
    boundaries: list[datetime] = []
    for day in dates:
        for second in start_seconds:
            candidate = datetime.combine(day, time()) + timedelta(seconds=second)
            if start < candidate <= end:
                before = _active_entry(entries, candidate - timedelta(seconds=1))
                after = _active_entry(entries, candidate)
                if (
                    before is not None
                    and after is not None
                    and before["parameters"].get("flow") != after["parameters"].get("flow")
                ):
                    boundaries.append(candidate)
    return sorted(set(boundaries))


def _boundary_classification(
    before: _DecodedPair,
    after: _DecodedPair,
    *,
    expected_before: int,
    expected_after: int,
) -> BoundaryClassification:
    master_values = (before.master.auto_flow, after.master.auto_flow)
    slave_values = (before.slave.auto_flow, after.slave.auto_flow)
    if (
        master_values == (35, 35)
        and slave_values == master_values
        and slave_values[0] == slave_values[1]
    ):
        return BoundaryClassification.MASTER_FOLLOWING
    if master_values == (35, 35) and slave_values == (expected_before, expected_after):
        return BoundaryClassification.INDEPENDENTLY_APPLIED
    if master_values == (35, 35) and slave_values[0] == slave_values[1]:
        return BoundaryClassification.SLOT_NOT_APPLIED
    return BoundaryClassification.UNEXPECTED


def _classify_complete_series(
    manifest: dict[str, Any],
    pairs: list[_DecodedPair | None],
    acquisition_rejections: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(pairs) != manifest["collector"]["planned_pair_count"]:
        raise Q2EpochError("q2_complete_series_missing")
    valid_pairs = [pair for pair in pairs if pair is not None]
    if not valid_pairs:
        return {
            "conclusion": EpochConclusion.UNESTABLISHED.value,
            "conclusion_reason": "no_accepted_pairs",
            "valid_boundary_count": 0,
            "rejected_boundary_count": 0,
            "boundary_records": [],
            "acquisition_rejections": acquisition_rejections,
            "post_third_stability_satisfied": False,
        }
    schedule_entries = valid_pairs[0].slave.schedule_entries
    boundaries = _flow_boundaries(
        schedule_entries,
        valid_pairs[0].slave.device_time,
        valid_pairs[-1].slave.device_time,
    )
    thresholds = manifest["thresholds"]
    boundary_records: list[dict[str, Any]] = []
    for boundary_index, boundary in enumerate(boundaries):
        before_candidates = [pair for pair in valid_pairs if pair.slave.device_time < boundary]
        after_candidates = [pair for pair in valid_pairs if pair.slave.device_time >= boundary]
        reasons: list[str] = []
        before = before_candidates[-1] if before_candidates else None
        after = after_candidates[0] if after_candidates else None
        if before is None or after is None:
            reasons.append("boundary_not_bracketed")
        elif after.ordinal != before.ordinal + 1:
            reasons.append("acquisition_gap_at_boundary")
        if before is not None and after is not None:
            reasons.extend(_pair_invariant_reasons(before, manifest))
            reasons.extend(_pair_invariant_reasons(after, manifest))
            pre_ns = int((boundary - before.slave.device_time).total_seconds() * 1e9)
            post_ns = int((after.slave.device_time - boundary).total_seconds() * 1e9)
            if pre_ns < thresholds["boundary_exclusion_ns"]:
                reasons.append("pre_sample_inside_boundary_exclusion")
            if post_ns < thresholds["boundary_exclusion_ns"]:
                reasons.append("post_sample_inside_boundary_exclusion")
            if pre_ns > thresholds["freshness_window_ns"]:
                reasons.append("pre_sample_not_fresh")
            if post_ns > thresholds["freshness_window_ns"]:
                reasons.append("post_sample_not_fresh")
            if after.slave.device_time <= before.slave.device_time:
                reasons.append("device_clock_not_monotonic")
            if (
                after.pair_started_ns - before.pair_started_ns
                > thresholds["maximum_actual_cadence_ns"]
            ):
                reasons.append("cadence_exceeded")
        record: dict[str, Any] = {
            "boundary_index": boundary_index,
            "boundary_device_time": boundary.isoformat(),
            "before_ordinal": before.ordinal if before is not None else None,
            "after_ordinal": after.ordinal if after is not None else None,
            "status": "rejected" if reasons else "valid",
            "reasons": sorted(set(reasons)),
            "classification": None,
        }
        if not reasons and before is not None and after is not None:
            before_entry = _active_entry(schedule_entries, before.slave.device_time)
            after_entry = _active_entry(schedule_entries, after.slave.device_time)
            if before_entry is None or after_entry is None:
                record["status"] = "rejected"
                record["reasons"] = ["active_schedule_slot_ambiguous"]
            else:
                classification = _boundary_classification(
                    before,
                    after,
                    expected_before=int(before_entry["parameters"]["flow"]),
                    expected_after=int(after_entry["parameters"]["flow"]),
                )
                record["classification"] = classification.value
        boundary_records.append(record)

    valid_records = [item for item in boundary_records if item["status"] == "valid"]
    classifications = [item["classification"] for item in valid_records]
    conflict = len(set(classifications)) > 1
    run: list[dict[str, Any]] = []
    selected_run: list[dict[str, Any]] | None = None
    for item in boundary_records:
        if item["status"] != "valid":
            run = []
            continue
        if run and item["classification"] != run[-1]["classification"]:
            run = []
        run.append(item)
        if len(run) >= 3 and selected_run is None:
            selected_run = list(run[-3:])

    stability_ok = False
    stability_reason = "three_consecutive_boundaries_missing"
    if selected_run is not None and not conflict:
        third = selected_run[-1]
        third_pair = next(pair for pair in valid_pairs if pair.ordinal == third["after_ordinal"])
        stability_deadline = third_pair.slave.read_completed_ns + thresholds["stability_window_ns"]
        at_or_after_deadline = next(
            (
                pair
                for pair in valid_pairs
                if pair.ordinal >= third_pair.ordinal
                and pair.slave.read_completed_ns >= stability_deadline
            ),
            None,
        )
        coverage_end = (
            at_or_after_deadline.ordinal
            if at_or_after_deadline is not None
            else valid_pairs[-1].ordinal
        )
        later = [
            pair
            for pair in valid_pairs
            if pair.ordinal >= third_pair.ordinal and pair.ordinal <= coverage_end
        ]
        series_reaches_deadline = at_or_after_deadline is not None
        contiguous = later and [item.ordinal for item in later] == list(
            range(later[0].ordinal, later[-1].ordinal + 1)
        )
        cadence_held = all(
            current.pair_started_ns - previous.pair_started_ns
            <= thresholds["maximum_actual_cadence_ns"]
            for previous, current in zip(later, later[1:], strict=False)
        )
        expected_class = selected_run[-1]["classification"]
        slot_not_applied_value = (
            third_pair.slave.auto_flow
            if expected_class == BoundaryClassification.SLOT_NOT_APPLIED.value
            else None
        )
        stable_values = all(
            not _pair_invariant_reasons(pair, manifest)
            and _sample_matches_class(
                pair,
                expected_class,
                slot_not_applied_value=slot_not_applied_value,
            )
            for pair in later
        )
        stability_boundaries = [
            item
            for item in boundary_records
            if item["boundary_index"] > third["boundary_index"]
            and item["before_ordinal"] is not None
            and item["before_ordinal"] <= coverage_end
        ]
        stability_boundaries_valid = all(
            item["status"] == "valid" and item["classification"] == expected_class
            for item in stability_boundaries
        )
        stability_ok = bool(
            series_reaches_deadline
            and contiguous
            and cadence_held
            and stable_values
            and stability_boundaries_valid
        )
        stability_reason = (
            "satisfied" if stability_ok else "series_or_values_do_not_cover_stability_window"
        )

    conclusion = EpochConclusion.UNESTABLISHED
    reason = "epoch_conditions_not_met"
    if conflict:
        reason = "conflicting_valid_boundaries"
    elif selected_run is not None and stability_ok:
        classification = BoundaryClassification(selected_run[-1]["classification"])
        conclusion = EpochConclusion(classification.value)
        reason = "three_consecutive_boundaries_and_stability_satisfied"
    return {
        "conclusion": conclusion.value,
        "conclusion_reason": reason,
        "valid_boundary_count": len(valid_records),
        "rejected_boundary_count": len(boundary_records) - len(valid_records),
        "boundary_records": boundary_records,
        "acquisition_rejections": acquisition_rejections,
        "post_third_stability_satisfied": stability_ok,
        "post_third_stability_reason": stability_reason,
    }


def _sample_matches_class(
    pair: _DecodedPair,
    classification: str,
    *,
    slot_not_applied_value: int | None = None,
) -> bool:
    expected_entry = _active_entry(pair.slave.schedule_entries, pair.slave.device_time)
    if expected_entry is None or pair.master.auto_flow != 35:
        return False
    if classification == BoundaryClassification.INDEPENDENTLY_APPLIED.value:
        return pair.slave.auto_flow == expected_entry["parameters"].get("flow")
    if classification == BoundaryClassification.MASTER_FOLLOWING.value:
        return pair.slave.auto_flow == pair.master.auto_flow
    if classification == BoundaryClassification.SLOT_NOT_APPLIED.value:
        # A fixed slave value can legitimately equal the new slot's expected value immediately
        # after a missed transition.  Stability means the unchanged value persists, not that it
        # must differ from the post-boundary expectation at every later sample.
        return (
            slot_not_applied_value is not None
            and pair.slave.auto_flow == slot_not_applied_value
            and pair.slave.auto_flow != pair.master.auto_flow
        )
    return False


def combine_q2_epoch_receipts(
    store: Q2EpochStore,
    control_manifest: Q2EpochManifestReference,
    control_receipt: Q2EpochReceiptReference,
    async_manifest: Q2EpochManifestReference,
    async_receipt: Q2EpochReceiptReference,
) -> Q2CombinedResult:
    """Verify both durable receipts before mapping them to the final Q2 judgment."""

    control = store.verify_receipt(control_manifest, control_receipt)
    async_epoch = store.verify_receipt(async_manifest, async_receipt)

    if control.get("epoch_kind") != Q2EpochKind.INDEPENDENT_CONTROL.value:
        raise Q2EpochError("q2_control_receipt_kind_invalid")
    if async_epoch.get("epoch_kind") != Q2EpochKind.ASYNC.value:
        raise Q2EpochError("q2_async_receipt_kind_invalid")
    for receipt in (control, async_epoch):
        if not _is_sha256(receipt.get("manifest_sha256")) or not _is_sha256(
            receipt.get("collector_series_sha256")
        ):
            raise Q2EpochError("q2_receipt_digest_claim_invalid")
    shared_fields = (
        "expected_identity_bindings_sha256",
        "exact_restore",
        "schedule_expectation",
    )
    if any(control.get(field) != async_epoch.get(field) for field in shared_fields):
        judgment = Q2FinalJudgment.UNKNOWN
        reason = "epoch_evidence_context_mismatch"
    elif control.get("conclusion") != EpochConclusion.INDEPENDENTLY_APPLIED.value:
        judgment = Q2FinalJudgment.UNKNOWN
        reason = "independent_control_not_established"
    elif async_epoch.get("conclusion") == EpochConclusion.INDEPENDENTLY_APPLIED.value:
        judgment = Q2FinalJudgment.YES
        reason = "async_slave_applied_own_schedule"
    elif async_epoch.get("conclusion") in {
        EpochConclusion.MASTER_FOLLOWING.value,
        EpochConclusion.SLOT_NOT_APPLIED.value,
    }:
        judgment = Q2FinalJudgment.NO
        reason = "async_slave_did_not_apply_own_schedule"
    else:
        judgment = Q2FinalJudgment.UNKNOWN
        reason = "async_epoch_not_established"
    return Q2CombinedResult(
        judgment=judgment,
        reason=reason,
        control_receipt_sha256=control_receipt.receipt_sha256,
        async_receipt_sha256=async_receipt.receipt_sha256,
    )


__all__ = [
    "BoundaryClassification",
    "EpochConclusion",
    "ExactRestoreEvidence",
    "ExactRestoreEvidenceVerifier",
    "PilotTimingEvidence",
    "Q2CombinedResult",
    "Q2EpochError",
    "Q2EpochKind",
    "Q2EpochManifestReference",
    "Q2EpochReceiptReference",
    "Q2EpochStore",
    "Q2FinalJudgment",
    "Q2ScheduleExpectation",
    "Q2Thresholds",
    "combine_q2_epoch_receipts",
    "derive_q2_thresholds_from_pilots",
    "q2_minimum_series_span_ns",
]
