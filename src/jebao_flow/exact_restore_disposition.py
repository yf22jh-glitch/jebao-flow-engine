"""Write-free terminal disposition for one failed exact-restore final verification.

The canonical preserved-artifact reader brings in inert protocol constants, ABCs, and data
classes transitively.  This module does not construct discovery/session objects or import a
device writer.  It revalidates one strict collector pair, archives the exact live journal,
installs a permanent operation-level replay barrier, and only then replaces the live journal
with a non-resumable tombstone.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_serializer,
    model_validator,
)

from jebao_flow.exact_restore import (
    ExactRestoreActionOutcome,
    ExactRestoreCycle,
    ExactRestoreRecord,
    ExactRestoreRole,
    ExactScheduleImage,
    OperationId,
    OuterControlSnapshot,
    _receipt_from_final_verified_record,
    system_boot_identity_sha256,
    system_boottime_ns,
)
from jebao_flow.exact_restore_authority import ConfirmationChannel, controlling_tty_channel
from jebao_flow.exact_restore_guard import ExactRestoreGuard
from jebao_flow.exact_restore_receipts import (
    ExactRestoreOperationFinalization,
    ExactRestoreOperationFinalizationConflictError,
    ExactRestoreReceiptArchive,
    ExactRestoreReceiptArchiveBusyError,
    ExactRestoreReceiptArchiveError,
)
from jebao_flow.exact_restore_store import ExactRestoreJournalStore
from jebao_flow.protocol.codec import GizwitsCommand, decode_frame
from jebao_flow.protocol.models import LinkageRole
from jebao_flow.protocol.profiles import get_product_schema
from jebao_flow.protocol.schedule_wire import (
    LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE,
    LocalWavemakerProScheduleSnapshot,
)
from jebao_flow.read_only_collector import PilotSeriesStore, VerifiedPilotPairArtifact

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
DispositionId = Annotated[
    str,
    StringConstraints(min_length=36, max_length=36, pattern=r"^ERD-[0-9a-f]{32}$"),
]

_MAX_PRIVATE_FILE_BYTES = 1024 * 1024
_STATE_REPLY_ACTION = 0x03
_AUTHORITY_LIFETIME = timedelta(minutes=5)
_MAX_CHALLENGE_AGE = timedelta(minutes=5)
_MAX_CONFIRMATION_BYTES = 160
_FINALIZATION_DOMAIN = b"jebao-flow/exact-restore-operation-finalization/v1\0"
_DISPOSITION_ID_DOMAIN = b"jebao-flow/exact-restore-parked-disposition-id/v1\0"
_AUTHORITY_DOMAIN = b"jebao-flow/exact-restore-parked-disposition-authority/v1\0"


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        + b"\n"
    )


def _sha256_json(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _operation_public_sha256(operation_id: str) -> str:
    return hashlib.sha256(operation_id.encode("ascii")).hexdigest()


def _operation_finalization_sha256(operation_id: str) -> str:
    return hashlib.sha256(_FINALIZATION_DOMAIN + operation_id.encode("ascii")).hexdigest()


class ExactRestoreDispositionError(RuntimeError):
    """Privacy-safe refusal from the write-free disposition path."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


_ARCHIVE_CLAIM_UNAVAILABLE = "disposition_archive_claim_unavailable"


def _archive_failure(error: BaseException, fallback: str) -> ExactRestoreDispositionError:
    """Name a live archive-lock holder exactly and keep every other refusal unchanged.

    Every broad archive wrapper translates through here so that one transient claim refusal
    cannot reach an operator as tombstone corruption.  Ownership, nesting, fork and
    identity-change claim refusals are deliberately not transient and keep their own code.
    """

    if isinstance(error, ExactRestoreReceiptArchiveBusyError):
        return ExactRestoreDispositionError(_ARCHIVE_CLAIM_UNAVAILABLE)
    return ExactRestoreDispositionError(fallback)


class ParkedDispositionReason(StrEnum):
    """Why a journal is parked without a qualification receipt.

    Widening this set never changes how an already-stored value serialises, so an installed
    disposition keeps its canonical bytes and digest.
    """

    FINAL_VERIFICATION_POLICY_OVERLOAD = "final_verification_policy_overload"
    FINAL_VERIFICATION_UNRESOLVED = "final_verification_unresolved"


# Both cycles share safe fallback, restore schedule and restore outer for each of the two roles.
# Only the sentinel cycle adds the two qualify-sentinel writes.  A regression pins this against
# the deterministic plan builder so the table cannot drift from the real action counts.
_CYCLE_ACTION_COUNTS: dict[ExactRestoreCycle, int] = {
    ExactRestoreCycle.SENTINEL_QUALIFICATION: 8,
    ExactRestoreCycle.BASELINE_RESTORE: 6,
}

# A phase-5 record carries the same cycle as the baseline restore it descends from, so the cycle
# alone cannot tell them apart.  Embedded predecessor depth does: sentinel 0, baseline 1, phase-5 2.
_MAXIMUM_PREDECESSOR_DEPTH = 2

# Closed set.  A future outcome value must fail rather than be accepted by a stale disposition.
_ALLOWED_ACTION_OUTCOMES: frozenset[ExactRestoreActionOutcome] = frozenset(
    {
        ExactRestoreActionOutcome.ALREADY_SATISFIED,
        ExactRestoreActionOutcome.WRITTEN_VERIFIED,
        ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN,
    }
)


def _outcomes_match_manifest(
    record: ExactRestoreRecord,
    manifest: ParkedDispositionManifest,
) -> bool:
    """Bind every completed outcome to what the manifest declares for this version.

    Version 1 asserts one required outcome for the whole prefix.  Version 2 declares the ordered
    vector, because a baseline cycle legitimately skips actions the device already satisfies.
    """

    outcomes = tuple(item.outcome for item in record.completed_actions)
    if any(outcome not in _ALLOWED_ACTION_OUTCOMES for outcome in outcomes):
        return False
    if manifest.version == 1:
        return all(outcome is ExactRestoreActionOutcome.WRITTEN_VERIFIED for outcome in outcomes)
    return outcomes == tuple(manifest.completed_action_outcomes or ())


def _version_binding_holds(
    record: ExactRestoreRecord,
    manifest: ParkedDispositionManifest,
) -> bool:
    """Tie the disposition version to the journal's own policy and predecessor shape."""

    split = record.baseline.verification_policy.max_final_acquisition_duration_seconds
    if manifest.version == 1:
        if split is not None:
            return False
    elif split is None:
        return False
    if manifest.maximum_pair_completion_gap_seconds != (
        record.baseline.verification_policy.max_final_pair_gap_seconds
    ):
        return False
    depth = _predecessor_depth(record)
    if not _depth_matches_cycle(record.cycle, depth):
        return False
    return manifest.version == 1 or manifest.predecessor_depth == depth


def _depth_matches_cycle(cycle: ExactRestoreCycle, depth: int) -> bool:
    """A sentinel has no predecessor; a baseline restore and a phase-5 restore have one or two."""

    if cycle is ExactRestoreCycle.SENTINEL_QUALIFICATION:
        return depth == 0
    return depth in (1, 2)


def _predecessor_depth(record: ExactRestoreRecord) -> int:
    depth = 0
    parent = record.qualification_final_record
    while parent is not None:
        depth += 1
        parent = parent.qualification_final_record
    return depth


class ParkedDispositionEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    plan_artifact_id: str = Field(min_length=1, max_length=80)
    plan_sha256: Sha256Digest
    series_artifact_id: str = Field(min_length=1, max_length=80)
    series_sha256: Sha256Digest
    accepted_pair_ordinal: int = Field(ge=0)


class ParkedDispositionManifest(BaseModel):
    """Owner-only exact inputs for one specific unqualified journal disposition."""

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)

    version: Literal[1, 2] = 1
    operation_id: OperationId
    operation_sha256: Sha256Digest
    journal_sha256: Sha256Digest
    cycle: ExactRestoreCycle = ExactRestoreCycle.SENTINEL_QUALIFICATION
    baseline_sha256: Sha256Digest
    action_plan_sha256: Sha256Digest
    authority_chain_sha256: Sha256Digest
    completed_action_count: int = Field(default=8, ge=1)
    # Version 1 asserts a single required outcome; version 2 carries the ordered vector
    # instead, because a baseline cycle legitimately skips actions it already satisfies.
    required_action_outcome: Literal["written_verified"] | None = "written_verified"
    evidence: ParkedDispositionEvidenceReference
    # A policy bound, not an observation.  Every variant carries it and binds it to the journal.
    maximum_pair_completion_gap_seconds: float = Field(gt=0)
    # Overload evidence only; a generic version 2 disposition makes no such claim.
    minimum_total_attempt_seconds: float | None = Field(default=None, gt=0)
    predecessor_depth: int | None = Field(default=None, ge=0, le=_MAXIMUM_PREDECESSOR_DEPTH)
    completed_action_outcomes: tuple[ExactRestoreActionOutcome, ...] | None = None
    outcome: Literal["parked_unqualified"] = "parked_unqualified"
    qualification_achieved: Literal[False] = False
    device_network_access: Literal[False] = False
    device_write_count: Literal[0] = 0
    reason: ParkedDispositionReason = ParkedDispositionReason.FINAL_VERIFICATION_POLICY_OVERLOAD

    @model_serializer(mode="wrap")
    def serialize_legacy_compatible(self, handler: Any) -> dict[str, Any]:
        payload = handler(self)
        for key in (
            "required_action_outcome",
            "minimum_total_attempt_seconds",
            "predecessor_depth",
            "completed_action_outcomes",
        ):
            if getattr(self, key) is None:
                payload.pop(key, None)
        return payload

    @model_validator(mode="after")
    def validate_exact_operation(self) -> Self:
        if self.operation_sha256 != _operation_public_sha256(self.operation_id):
            raise ValueError("operation digest does not match operation id")
        if self.completed_action_count != _CYCLE_ACTION_COUNTS[self.cycle]:
            raise ValueError("completed action count does not match the cycle's deterministic plan")
        if self.version == 1:
            # Version 1 describes exactly the original overload park and nothing else.
            if (
                self.cycle is not ExactRestoreCycle.SENTINEL_QUALIFICATION
                or self.reason is not ParkedDispositionReason.FINAL_VERIFICATION_POLICY_OVERLOAD
                or self.required_action_outcome != "written_verified"
                or self.minimum_total_attempt_seconds is None
                or self.predecessor_depth is not None
                or self.completed_action_outcomes is not None
            ):
                raise ValueError("version 1 disposition must describe the original overload park")
        else:
            # Version 2 has no durable failure frame to justify an overload claim, so it stays
            # generic and never carries the overload evidence bound.
            if (
                self.reason is not ParkedDispositionReason.FINAL_VERIFICATION_UNRESOLVED
                or self.minimum_total_attempt_seconds is not None
                or self.required_action_outcome is not None
            ):
                raise ValueError("version 2 disposition must be a generic unresolved park")
            if self.predecessor_depth is None or self.completed_action_outcomes is None:
                raise ValueError("version 2 disposition must declare depth and outcome vector")
            if len(self.completed_action_outcomes) != self.completed_action_count:
                raise ValueError("outcome vector length must match the completed action count")
            if any(
                outcome not in _ALLOWED_ACTION_OUTCOMES
                for outcome in self.completed_action_outcomes
            ):
                raise ValueError("outcome vector contains an unsupported action outcome")
            if not _depth_matches_cycle(self.cycle, self.predecessor_depth):
                raise ValueError("predecessor depth does not match the declared cycle")
        if (
            self.minimum_total_attempt_seconds is not None
            and self.minimum_total_attempt_seconds < self.maximum_pair_completion_gap_seconds
        ):
            raise ValueError("overload total must not be below the completion-gap bound")
        return self

    @property
    def manifest_sha256(self) -> str:
        return _sha256_json(self.model_dump(mode="json"))


class ParkedDispositionAuthority(BaseModel):
    """Boot-bound attended consent that cannot authorize any device operation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    intent: Literal["park_unqualified_without_device_io"] = "park_unqualified_without_device_io"
    manifest_sha256: Sha256Digest
    operation_sha256: Sha256Digest
    journal_sha256: Sha256Digest
    evidence_series_sha256: Sha256Digest
    outcome: Literal["parked_unqualified"] = "parked_unqualified"
    qualification_achieved: Literal[False] = False
    device_network_access: Literal[False] = False
    device_write_count: Literal[0] = 0
    issued_at: datetime
    expires_at: datetime
    boot_identity_sha256: Sha256Digest
    issued_monotonic_ns: int = Field(ge=0)
    deadline_monotonic_ns: int = Field(ge=0)
    confirmation_token_sha256: Sha256Digest

    @field_validator("issued_at", "expires_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("disposition authority time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_lifetime(self) -> Self:
        lifetime = self.expires_at - self.issued_at
        lifetime_ns = int(lifetime.total_seconds() * 1_000_000_000)
        if (
            lifetime <= timedelta(0)
            or lifetime > _AUTHORITY_LIFETIME
            or self.deadline_monotonic_ns <= self.issued_monotonic_ns
            or self.deadline_monotonic_ns - self.issued_monotonic_ns != lifetime_ns
        ):
            raise ValueError("disposition authority lifetime is invalid")
        return self

    @property
    def authority_sha256(self) -> str:
        return _sha256_json(self.model_dump(mode="json"))


class ParkedDispositionRecord(BaseModel):
    """Immutable audit record; it is deliberately not an exact-restore receipt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1, 2] = 1
    disposition_id: DispositionId
    outcome: Literal["parked_unqualified"] = "parked_unqualified"
    qualification_achieved: Literal[False] = False
    device_network_access: Literal[False] = False
    device_write_count: Literal[0] = 0
    reason: ParkedDispositionReason
    manifest_sha256: Sha256Digest
    operation_sha256: Sha256Digest
    operation_finalization_sha256: Sha256Digest
    cycle: ExactRestoreCycle
    journal_snapshot_sha256: Sha256Digest
    baseline_sha256: Sha256Digest
    action_plan_sha256: Sha256Digest
    authority_chain_sha256: Sha256Digest
    completed_action_count: int = Field(ge=0)
    evidence: ParkedDispositionEvidenceReference
    # Observations.  The approved bound is the separate field below, kept apart on purpose.
    pair_completion_gap_ns: int = Field(ge=0)
    total_attempt_ns: int = Field(ge=0)
    # Version 2 stores the approved bound so an archived reload can re-check the binding without
    # the manifest.  Version 1 records predate this and cannot be re-checked after the fact.
    maximum_pair_completion_gap_seconds: float | None = Field(default=None, gt=0)
    predecessor_depth: int | None = Field(default=None, ge=0, le=_MAXIMUM_PREDECESSOR_DEPTH)
    completed_action_outcomes: tuple[ExactRestoreActionOutcome, ...] | None = None
    disposition_authority: ParkedDispositionAuthority
    disposition_authority_sha256: Sha256Digest
    recorded_at: datetime

    @model_serializer(mode="wrap")
    def serialize_legacy_compatible(self, handler: Any) -> dict[str, Any]:
        payload = handler(self)
        for key in (
            "maximum_pair_completion_gap_seconds",
            "predecessor_depth",
            "completed_action_outcomes",
        ):
            if getattr(self, key) is None:
                payload.pop(key, None)
        return payload

    @model_validator(mode="after")
    def validate_version_shape(self) -> Self:
        if self.completed_action_count != _CYCLE_ACTION_COUNTS[self.cycle]:
            raise ValueError("completed action count does not match the cycle's deterministic plan")
        version_two_fields = (
            self.maximum_pair_completion_gap_seconds,
            self.predecessor_depth,
            self.completed_action_outcomes,
        )
        if self.version == 1:
            if (
                self.cycle is not ExactRestoreCycle.SENTINEL_QUALIFICATION
                or self.reason is not ParkedDispositionReason.FINAL_VERIFICATION_POLICY_OVERLOAD
                or any(value is not None for value in version_two_fields)
            ):
                raise ValueError("version 1 record must describe the original overload park")
        else:
            if self.reason is not ParkedDispositionReason.FINAL_VERIFICATION_UNRESOLVED:
                raise ValueError("version 2 record must be a generic unresolved park")
            if any(value is None for value in version_two_fields):
                raise ValueError("version 2 record must carry bound, depth and outcome vector")
            outcomes = self.completed_action_outcomes or ()
            if len(outcomes) != self.completed_action_count:
                raise ValueError("outcome vector length must match the completed action count")
            if any(outcome not in _ALLOWED_ACTION_OUTCOMES for outcome in outcomes):
                raise ValueError("outcome vector contains an unsupported action outcome")
            if self.predecessor_depth is None or not _depth_matches_cycle(
                self.cycle, self.predecessor_depth
            ):
                raise ValueError("predecessor depth does not match the declared cycle")
            bound = self.maximum_pair_completion_gap_seconds
            if bound is not None and self.pair_completion_gap_ns > int(bound * 1_000_000_000):
                raise ValueError("observed completion gap exceeds the approved bound")
        return self

    @field_validator("recorded_at")
    @classmethod
    def require_aware_recorded_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("disposition record time must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_authority_binding(self) -> Self:
        authority = self.disposition_authority
        if (
            self.disposition_authority_sha256 != authority.authority_sha256
            or authority.manifest_sha256 != self.manifest_sha256
            or authority.operation_sha256 != self.operation_sha256
            or authority.journal_sha256 != self.journal_snapshot_sha256
            or authority.evidence_series_sha256 != self.evidence.series_sha256
            or not authority.issued_at <= self.recorded_at <= authority.expires_at
        ):
            raise ValueError("disposition authority does not match the disposition")
        return self

    @property
    def disposition_sha256(self) -> str:
        return _sha256_json(self.model_dump(mode="json"))


class ParkedDispositionTombstone(BaseModel):
    """Temporary journal-path replay barrier understood before ExactRestoreRecord parsing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[2] = 2
    journal_format: Literal[2] = 2
    operation_id: OperationId
    phase: Literal["parked_unqualified"] = "parked_unqualified"
    operation_sha256: Sha256Digest
    disposition_id: DispositionId
    disposition_sha256: Sha256Digest
    journal_snapshot_sha256: Sha256Digest

    @model_validator(mode="after")
    def validate_operation_digest(self) -> Self:
        if self.operation_sha256 != _operation_public_sha256(self.operation_id):
            raise ValueError("tombstone operation digest mismatch")
        return self


@dataclass(frozen=True, slots=True)
class VerifiedParkEligibility:
    manifest: ParkedDispositionManifest
    record: ExactRestoreRecord
    journal_canonical: bytes
    artifact: VerifiedPilotPairArtifact
    total_attempt_ns: int


class _CollectorStore(Protocol):
    def load(self, series_id: str) -> Any: ...

    def extract_verified_accepted_pair(
        self,
        reference: Any,
        *,
        expected_series_sha256: str,
        ordinal: int,
    ) -> VerifiedPilotPairArtifact: ...


WallClock = Callable[[], datetime]
MonotonicClock = Callable[[], int]
BootIdentity = Callable[[], str]
Entropy = Callable[[int], bytes]
ChannelFactory = Callable[[], AbstractContextManager[ConfirmationChannel]]


def _read_owner_only_regular_file(path: str | Path) -> bytes:
    """Read one stable owner-only manifest without following its final component."""

    selected = Path(path)
    if not hasattr(os, "O_NOFOLLOW"):
        raise ExactRestoreDispositionError("disposition_manifest_invalid")
    descriptor = -1
    try:
        before = selected.lstat()
        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = os.open(selected, flags)
        opened = os.fstat(descriptor)
        current = selected.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or before.st_uid != os.geteuid()
            or opened.st_uid != os.geteuid()
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or stat.S_IMODE(opened.st_mode) != 0o600
            or stat.S_IMODE(current.st_mode) != 0o600
            or before.st_nlink != 1
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or opened.st_size > _MAX_PRIVATE_FILE_BYTES
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            raise ExactRestoreDispositionError("disposition_manifest_invalid")
        chunks: list[bytes] = []
        remaining = _MAX_PRIVATE_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        reread = os.fstat(descriptor)
        after = selected.lstat()
        if (
            len(payload) > _MAX_PRIVATE_FILE_BYTES
            or (reread.st_dev, reread.st_ino, reread.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
            or (after.st_dev, after.st_ino, after.st_size)
            != (opened.st_dev, opened.st_ino, opened.st_size)
            or not stat.S_ISREG(after.st_mode)
            or stat.S_IMODE(reread.st_mode) != 0o600
            or stat.S_IMODE(after.st_mode) != 0o600
            or reread.st_uid != os.geteuid()
            or after.st_uid != os.geteuid()
            or reread.st_nlink != 1
            or after.st_nlink != 1
        ):
            raise ExactRestoreDispositionError("disposition_manifest_invalid")
        return payload
    except ExactRestoreDispositionError:
        raise
    except OSError as error:
        raise ExactRestoreDispositionError("disposition_manifest_invalid") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def load_parked_disposition_manifest(path: str | Path) -> ParkedDispositionManifest:
    payload = _read_owner_only_regular_file(path)
    try:
        return ParkedDispositionManifest.model_validate_json(payload)
    except (TypeError, ValidationError, ValueError) as error:
        raise ExactRestoreDispositionError("disposition_manifest_invalid") from error


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ExactRestoreDispositionError("disposition_artifact_time_invalid") from error
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ExactRestoreDispositionError("disposition_artifact_time_invalid")
    return parsed


def _canonical_journal(payload: Mapping[str, object]) -> bytes:
    try:
        return (
            json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            + b"\n"
        )
    except (RecursionError, TypeError, ValueError, UnicodeError) as error:
        raise ExactRestoreDispositionError("disposition_journal_invalid") from error


def _decode_exact_sample(
    record: ExactRestoreRecord,
    role: ExactRestoreRole,
    sample: Any,
) -> tuple[OuterControlSnapshot, ExactScheduleImage]:
    baseline = record.baseline.for_role(role)
    try:
        frame = decode_frame(sample.raw_wire_frame)
        if (
            frame.command != GizwitsCommand.SERIAL_TRANSMIT_RESPONSE
            or len(frame.payload) != LOCAL_WAVEMAKER_PRO_RAW_STATUS_SIZE + 1
            or frame.payload[0] != _STATE_REPLY_ACTION
        ):
            raise ValueError("not one explicit Pro state reply")
        status = frame.payload[1:]
        values = get_product_schema(baseline.physical_binding.product_key).decode_status(status)
        enabled = values["SwitchON"]
        timer_enabled = values["TimerON"]
        linkage = values["Linkage"]
        mode = values["Mode"]
        power = values["Flow"]
        frequency = values["Frequency"]
        if (
            type(enabled) is not bool
            or type(timer_enabled) is not bool
            or not isinstance(linkage, str)
            or not isinstance(mode, str)
            or type(power) is not int
            or type(frequency) is not int
        ):
            raise ValueError("invalid outer controls")
        outer = OuterControlSnapshot(
            enabled=enabled,
            timer_enabled=timer_enabled,
            linkage=LinkageRole(linkage),
            mode=mode,
            power=power,
            frequency=frequency,
        )
        schedule = ExactScheduleImage.from_bytes(
            LocalWavemakerProScheduleSnapshot.from_status(status).validate().image
        )
    except Exception as error:
        raise ExactRestoreDispositionError("disposition_artifact_frame_invalid") from error
    return outer, schedule


def verify_park_eligibility(
    manifest: ParkedDispositionManifest,
    *,
    journal_store: ExactRestoreJournalStore,
    collector_store: _CollectorStore,
    finalization_store: ExactRestoreReceiptArchive,
    expected_parked_disposition: ParkedDispositionRecord | None = None,
) -> VerifiedParkEligibility:
    """Revalidate the exact live journal and preserved raw pair without device access."""

    payload = journal_store.load()
    if not isinstance(payload, Mapping):
        raise ExactRestoreDispositionError("disposition_journal_absent")
    try:
        tombstone = ParkedDispositionTombstone.model_validate(payload)
    except (TypeError, ValidationError, ValueError):
        tombstone = None
    if tombstone is not None:
        try:
            finalization_store.confirm_parked_tombstone(tombstone.model_dump(mode="json"))
        except Exception as error:
            raise _archive_failure(error, "disposition_tombstone_invalid") from error
        raise ExactRestoreDispositionError("disposition_already_parked")

    journal_canonical = _canonical_journal(payload)
    if not secrets.compare_digest(
        hashlib.sha256(journal_canonical).hexdigest(),
        manifest.journal_sha256,
    ):
        raise ExactRestoreDispositionError("disposition_journal_digest_mismatch")
    try:
        record = ExactRestoreRecord.model_validate(payload)
    except (TypeError, ValidationError, ValueError) as error:
        raise ExactRestoreDispositionError("disposition_journal_invalid") from error
    if (
        record.operation_id != manifest.operation_id
        or _operation_public_sha256(record.operation_id) != manifest.operation_sha256
        or record.cycle is not manifest.cycle
        or not _version_binding_holds(record, manifest)
        or record.phase.value != "awaiting_final_verify"
        or record.baseline_sha256 != manifest.baseline_sha256
        or record.action_plan_sha256 != manifest.action_plan_sha256
        or record.authority_chain_sha256 != manifest.authority_chain_sha256
        or len(record.actions) != manifest.completed_action_count
        or len(record.completed_actions) != manifest.completed_action_count
        or tuple(item.index for item in record.completed_actions)
        != tuple(range(manifest.completed_action_count))
        or not _outcomes_match_manifest(record, manifest)
        or record.inflight is not None
        or record.error_code is not None
        or record.final_evidence is not None
    ):
        raise ExactRestoreDispositionError("disposition_journal_ineligible")
    try:
        existing_finalization = finalization_store.load_operation_finalization(record.operation_id)
    except Exception as error:
        raise _archive_failure(error, "disposition_finalization_unavailable") from error
    if expected_parked_disposition is None:
        if existing_finalization is not None:
            raise ExactRestoreDispositionError("disposition_operation_already_terminal")
    elif (
        not isinstance(existing_finalization, Mapping)
        or existing_finalization.get("version") != 2
        or existing_finalization.get("outcome") != "parked_unqualified"
        or existing_finalization.get("cycle") != record.cycle.value
        or existing_finalization.get("disposition_sha256")
        != expected_parked_disposition.disposition_sha256
    ):
        raise ExactRestoreDispositionError("disposition_finalization_mismatch")

    evidence = manifest.evidence
    try:
        reference = collector_store.load(evidence.series_artifact_id)
        if (
            reference.plan_artifact_id != evidence.plan_artifact_id
            or reference.plan_sha256 != evidence.plan_sha256
            or reference.series_id != evidence.series_artifact_id
        ):
            raise ValueError("collector plan reference mismatch")
        artifact = collector_store.extract_verified_accepted_pair(
            reference,
            expected_series_sha256=evidence.series_sha256,
            ordinal=evidence.accepted_pair_ordinal,
        )
    except ExactRestoreDispositionError:
        raise
    except Exception as error:
        raise ExactRestoreDispositionError("disposition_artifact_invalid") from error
    # Dry verification confirms the same chain through the public surface.  The decisive check
    # stays in the protected path that installs the index under one claim; this one exists so a
    # `park-verify` refuses an unbarriered ancestor before anyone is asked to authorize a park.
    _require_public_predecessor_chain(record, finalization_store)
    if (
        artifact.plan_artifact_id != evidence.plan_artifact_id
        or artifact.plan_sha256 != evidence.plan_sha256
        or artifact.series_id != evidence.series_artifact_id
        or artifact.series_sha256 != evidence.series_sha256
        or artifact.ordinal != evidence.accepted_pair_ordinal
        or artifact.pair_completion_gap_ns
        > int(manifest.maximum_pair_completion_gap_seconds * 1_000_000_000)
    ):
        raise ExactRestoreDispositionError("disposition_artifact_mismatch")
    total_attempt_ns = (
        artifact.attempt.completed_monotonic_ns - artifact.attempt.started_monotonic_ns
    )
    # Keep the local span sanity check for every version even though the canonical collector
    # already validates it; losing a boundary-local defence buys nothing.
    if total_attempt_ns < 0:
        raise ExactRestoreDispositionError("disposition_artifact_mismatch")
    # The total bound binds the overload claim only.  A generic park makes no such claim and
    # the manifest validator forbids it from carrying the bound at all.
    minimum_total_seconds = manifest.minimum_total_attempt_seconds
    if minimum_total_seconds is not None and total_attempt_ns <= int(
        minimum_total_seconds * 1_000_000_000
    ):
        raise ExactRestoreDispositionError("disposition_overload_evidence_missing")
    # The eligibility this gate needs is "no device write happened at or after the artifact
    # started".  The checks above already establish that: the completed prefix is exactly the
    # deterministic plan, every outcome is WRITTEN_VERIFIED, and inflight/error/final evidence are
    # all absent, so a write can neither be pending nor have been dropped from the record.  The
    # last verified completion is therefore the write boundary, and it is stamped after the
    # post-write observation, which makes it conservative against the wire send.  ``updated_at``
    # is not that boundary: it also moves for write-free journal touches such as an attended
    # reauthorization, and it never moves for a write outside this journal at all.
    # Every allowed outcome stamps its completion after that action's own device contact:
    # already-satisfied after the observation that found it satisfied, written-verified after the
    # post-write verification, and verified-after-uncertain after the fresh reconciliation that
    # follows a possible write without resending it.  So a completion is always a safe lower
    # bound for that action.  What is not safe is taking the last index: `_complete()` stamps from
    # the wall clock without comparing to the previous completion and no validator enforces
    # monotonicity across the prefix, so a clock that goes backwards can leave `[-1]` earlier than
    # a preceding write's completion.  The conservative boundary is therefore the maximum.
    last_completed_at = max(item.completed_at for item in record.completed_actions)
    artifact_started_at = _parse_utc(artifact.attempt.started_utc)
    if artifact_started_at <= last_completed_at:
        raise ExactRestoreDispositionError("disposition_artifact_predates_restore")

    for role, expected_sample_role, sample in zip(
        (ExactRestoreRole.MASTER, ExactRestoreRole.SLAVE),
        ("a", "b"),
        artifact.samples,
        strict=True,
    ):
        baseline = record.baseline.for_role(role)
        if (
            sample.role != expected_sample_role
            or sample.identity_binding_sha256 != baseline.identity_binding_sha256
            or sample.raw_wire_frame_sha256 != hashlib.sha256(sample.raw_wire_frame).hexdigest()
        ):
            raise ExactRestoreDispositionError("disposition_artifact_binding_mismatch")
        outer, schedule = _decode_exact_sample(record, role, sample)
        if outer != baseline.outer or schedule.image_bytes != baseline.schedule.image_bytes:
            raise ExactRestoreDispositionError("disposition_artifact_state_mismatch")

    return VerifiedParkEligibility(
        manifest=manifest,
        record=record,
        journal_canonical=journal_canonical,
        artifact=artifact,
        total_attempt_ns=total_attempt_ns,
    )


class ExactRestoreDispositionArchive(ExactRestoreReceiptArchive):
    """Park archive sharing the receipt namespace's single deployment-wide claim."""

    def persist_parked_bundle(
        self,
        *,
        journal_canonical: bytes,
        disposition: ParkedDispositionRecord,
    ) -> ExactRestoreOperationFinalization:
        journal_sha256 = hashlib.sha256(journal_canonical).hexdigest()
        if journal_sha256 != disposition.journal_snapshot_sha256:
            raise ExactRestoreDispositionError("disposition_snapshot_digest_mismatch")
        disposition_canonical = _canonical_json(disposition.model_dump(mode="json"))
        disposition_sha256 = hashlib.sha256(disposition_canonical).hexdigest()
        if disposition_sha256 != disposition.disposition_sha256:
            raise ExactRestoreDispositionError("disposition_record_digest_mismatch")
        finalization = ExactRestoreOperationFinalization(
            version=2,
            operation_sha256=disposition.operation_finalization_sha256,
            disposition_sha256=disposition_sha256,
            outcome="parked_unqualified",
            cycle=disposition.cycle,
        )
        finalization_canonical = self._canonical_finalization(finalization)
        try:
            with self._claim():
                root_descriptor = self._require_active_claim()
                existing = self._load_finalization_snapshot(
                    root_descriptor,
                    finalization.operation_sha256,
                    allow_absent=True,
                )
                if existing is not None:
                    self._require_exact_finalization_snapshot(
                        existing,
                        finalization,
                        finalization_canonical,
                    )
                    self._require_parked_backing_finalization(
                        root_descriptor,
                        existing.finalization,
                    )
                    self._confirm_blob(
                        root_descriptor,
                        self._parked_journal_name(journal_sha256),
                        journal_sha256,
                        journal_canonical,
                    )
                    self._confirm_blob(
                        root_descriptor,
                        self._parked_disposition_name(disposition_sha256),
                        disposition_sha256,
                        disposition_canonical,
                    )
                    return existing.finalization

                self._persist_blob(
                    root_descriptor,
                    namespace_prefix="exact-restore-parked-journal-",
                    name=self._parked_journal_name(journal_sha256),
                    digest=journal_sha256,
                    payload=journal_canonical,
                )
                self._persist_blob(
                    root_descriptor,
                    namespace_prefix="exact-restore-parked-disposition-",
                    name=self._parked_disposition_name(disposition_sha256),
                    digest=disposition_sha256,
                    payload=disposition_canonical,
                )
                # Both checks happen here, inside the claim that installs the index and before
                # that index exists.  Validating only after the index is installed lets an
                # incompatible candidate occupy the slot and strand the live journal, and
                # confirming the predecessor chain under a separate claim leaves a window between
                # the check and the install.
                self._require_decodable_candidate(
                    root_descriptor,
                    finalization=finalization,
                    disposition=disposition,
                    journal_canonical=journal_canonical,
                )
                self._require_archived_predecessor_chain(root_descriptor, journal_canonical)
                installed = self._persist_finalization_under_claim(
                    root_descriptor,
                    finalization,
                    finalization_canonical,
                )
                self._require_exact_finalization_snapshot(
                    installed,
                    finalization,
                    finalization_canonical,
                )
                self._require_parked_backing_finalization(root_descriptor, finalization)
                self._confirm_blob(
                    root_descriptor,
                    self._parked_journal_name(journal_sha256),
                    journal_sha256,
                    journal_canonical,
                )
                self._confirm_blob(
                    root_descriptor,
                    self._parked_disposition_name(disposition_sha256),
                    disposition_sha256,
                    disposition_canonical,
                )
                return finalization
        except (
            ExactRestoreDispositionError,
            ExactRestoreOperationFinalizationConflictError,
        ):
            raise
        except Exception as error:
            raise _archive_failure(error, "disposition_archive_failed") from error

    def _require_decodable_candidate(
        self,
        root_descriptor: int,
        *,
        finalization: ExactRestoreOperationFinalization,
        disposition: ParkedDispositionRecord,
        journal_canonical: bytes,
    ) -> None:
        """Reject a candidate the final loader would refuse, before any index can name it.

        This has to run the real archive decoder, not only the model binding.  The exact key set
        and reason rules live in the receipt archive, so validating the blob through them here is
        what stops an incompatible candidate from occupying the index and stranding the journal.
        """

        del disposition
        try:
            journal_record = ExactRestoreRecord.model_validate_json(journal_canonical)
        except (TypeError, ValidationError, ValueError) as error:
            raise ExactRestoreDispositionError("disposition_archive_binding_mismatch") from error
        # Read back and decode what was actually written, through the archive's own exact-key and
        # reason rules.  Binding the in-memory object instead would leave the stored bytes
        # unchecked until after the index already named them.
        decoded = self._require_parked_backing_finalization(root_descriptor, finalization)
        try:
            stored = ParkedDispositionRecord.model_validate(decoded)
        except (TypeError, ValidationError, ValueError) as error:
            raise ExactRestoreDispositionError("disposition_archive_binding_mismatch") from error
        _require_disposition_binds_journal(stored, journal_record)

    def _require_archived_predecessor_chain(
        self,
        root_descriptor: int,
        journal_canonical: bytes,
    ) -> None:
        """Confirm every finished ancestor still holds its permanent barrier, oldest first."""

        try:
            journal_record = ExactRestoreRecord.model_validate_json(journal_canonical)
        except (TypeError, ValidationError, ValueError) as error:
            raise ExactRestoreDispositionError("disposition_archive_binding_mismatch") from error
        ancestors: list[ExactRestoreRecord] = []
        parent = journal_record.qualification_final_record
        while parent is not None:
            ancestors.append(parent)
            parent = parent.qualification_final_record
        for ancestor in reversed(ancestors):
            try:
                receipt = _receipt_from_final_verified_record(ancestor)
            except (TypeError, ValueError) as error:
                raise ExactRestoreDispositionError("disposition_predecessor_unbarriered") from error
            snapshot = self._load_finalization_snapshot(
                root_descriptor,
                self._operation_digest(ancestor.operation_id),
                allow_absent=True,
            )
            if (
                snapshot is None
                or snapshot.finalization.version != 1
                or snapshot.finalization.receipt_sha256 != receipt.receipt_sha256
                or snapshot.finalization.cycle is not receipt.cycle
            ):
                raise ExactRestoreDispositionError("disposition_predecessor_unbarriered")
            self._require_receipt_backing_finalization(root_descriptor, snapshot.finalization)

    def load_parked_disposition(self, operation_id: str) -> ParkedDispositionRecord | None:
        operation_digest = _operation_finalization_sha256(operation_id)
        try:
            with self._claim():
                root_descriptor = self._require_active_claim()
                snapshot = self._load_finalization_snapshot(
                    root_descriptor,
                    operation_digest,
                    allow_absent=True,
                )
                if snapshot is None:
                    return None
                if snapshot.finalization.version != 2:
                    return None
                decoded = self._require_parked_backing_finalization(
                    root_descriptor,
                    snapshot.finalization,
                )
                disposition = ParkedDispositionRecord.model_validate(decoded)
                journal_payload = self._load_digest_blob(
                    root_descriptor,
                    self._parked_journal_name(disposition.journal_snapshot_sha256),
                    disposition.journal_snapshot_sha256,
                )
                journal_decoded = json.loads(journal_payload.decode("utf-8"))
                journal_record = ExactRestoreRecord.model_validate(journal_decoded)
                if (
                    journal_record.operation_id != operation_id
                    or disposition.operation_finalization_sha256 != operation_digest
                    or disposition.journal_snapshot_sha256
                    != hashlib.sha256(journal_payload).hexdigest()
                ):
                    raise ExactRestoreDispositionError("disposition_archive_binding_mismatch")
                # Same rule the pre-index validator applied, so the two gates cannot drift.
                _require_disposition_binds_journal(disposition, journal_record)
                return disposition
        except ExactRestoreDispositionError:
            raise
        except Exception as error:
            raise _archive_failure(error, "disposition_archive_invalid") from error

    def _persist_blob(
        self,
        root_descriptor: int,
        *,
        namespace_prefix: str,
        name: str,
        digest: str,
        payload: bytes,
    ) -> None:
        try:
            existing = self._load_digest_blob(
                root_descriptor,
                name,
                digest,
            )
        except ExactRestoreReceiptArchiveError as error:
            # Absence and corruption must be distinguished without parsing error strings.
            try:
                os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            except OSError as metadata_error:
                raise ExactRestoreDispositionError("disposition_archive_metadata_invalid") from (
                    metadata_error
                )
            else:
                raise ExactRestoreDispositionError("disposition_archive_blob_invalid") from error
        if existing is not None:
            if existing != payload:
                raise ExactRestoreDispositionError("disposition_archive_blob_conflict")
            self._confirm_blob(root_descriptor, name, digest, payload)
            return

        temporary_name: str | None = None
        try:
            temporary_name = self._write_temporary(
                root_descriptor,
                namespace_prefix,
                digest,
                payload,
            )
            self._validate_active_claim()
            try:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=root_descriptor,
                    dst_dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                contender = self._load_digest_blob(root_descriptor, name, digest)
                if contender != payload:
                    raise ExactRestoreDispositionError(
                        "disposition_archive_blob_conflict"
                    ) from None
                return
            self._complete_install(root_descriptor, temporary_name)
            temporary_name = None
            self._confirm_blob(root_descriptor, name, digest, payload)
        except ExactRestoreDispositionError:
            raise
        except Exception as error:
            raise ExactRestoreDispositionError("disposition_archive_blob_write_failed") from error
        finally:
            if temporary_name is not None:
                self._unlink_temporary(root_descriptor, temporary_name)

    def _confirm_blob(
        self,
        root_descriptor: int,
        name: str,
        digest: str,
        expected: bytes,
    ) -> None:
        descriptor = self._open_existing(root_descriptor, name, allow_absent=False)
        if descriptor is None:  # pragma: no cover - guarded by allow_absent=False
            raise ExactRestoreDispositionError("disposition_archive_blob_absent")
        try:
            observed = self._read_bounded(descriptor)
            if observed != expected or hashlib.sha256(observed).hexdigest() != digest:
                raise ExactRestoreDispositionError("disposition_archive_blob_mismatch")
            os.fsync(descriptor)
            self._validate_named_file(root_descriptor, descriptor, name)
            self._validate_active_claim()
        except ExactRestoreDispositionError:
            raise
        except Exception as error:
            raise ExactRestoreDispositionError("disposition_archive_blob_fsync_failed") from error
        finally:
            os.close(descriptor)
        self._fsync_root(root_descriptor)
        self._validate_active_claim()
        confirmed = self._load_digest_blob(root_descriptor, name, digest)
        if confirmed != expected:
            raise ExactRestoreDispositionError("disposition_archive_blob_changed")


def _disposition_id(manifest: ParkedDispositionManifest) -> str:
    digest = hashlib.sha256(
        _DISPOSITION_ID_DOMAIN
        + bytes.fromhex(manifest.manifest_sha256)
        + bytes.fromhex(manifest.journal_sha256)
    ).hexdigest()
    return f"ERD-{digest[:32]}"


def _require_public_predecessor_chain(
    record: ExactRestoreRecord,
    finalization_store: ExactRestoreReceiptArchive,
) -> None:
    """Confirm each finished ancestor still holds a version 1 success barrier, oldest first."""

    ancestors: list[ExactRestoreRecord] = []
    parent = record.qualification_final_record
    while parent is not None:
        ancestors.append(parent)
        parent = parent.qualification_final_record
    for ancestor in reversed(ancestors):
        try:
            receipt = _receipt_from_final_verified_record(ancestor)
            finalization = finalization_store.load_operation_finalization(ancestor.operation_id)
        except ExactRestoreDispositionError:
            raise
        except Exception as error:
            raise _archive_failure(error, "disposition_predecessor_unbarriered") from error
        if (
            not isinstance(finalization, Mapping)
            or finalization.get("version") != 1
            or finalization.get("receipt_sha256") != receipt.receipt_sha256
            or finalization.get("cycle") != receipt.cycle.value
        ):
            raise ExactRestoreDispositionError("disposition_predecessor_unbarriered")


def _require_disposition_binds_journal(
    disposition: ParkedDispositionRecord,
    journal_record: ExactRestoreRecord,
) -> None:
    """One cross-binding rule shared by the pre-index validator and the final loader.

    Keeping a single implementation is the point: a second copy would drift and let a candidate
    pass one gate while failing the other.
    """

    policy = journal_record.baseline.verification_policy
    split = policy.max_final_acquisition_duration_seconds
    depth = _predecessor_depth(journal_record)
    outcomes = tuple(item.outcome for item in journal_record.completed_actions)
    if (
        disposition.operation_sha256 != _operation_public_sha256(journal_record.operation_id)
        or disposition.operation_finalization_sha256
        != _operation_finalization_sha256(journal_record.operation_id)
        or disposition.cycle is not journal_record.cycle
        or disposition.baseline_sha256 != journal_record.baseline_sha256
        or disposition.action_plan_sha256 != journal_record.action_plan_sha256
        or disposition.authority_chain_sha256 != journal_record.authority_chain_sha256
        or disposition.completed_action_count != len(journal_record.completed_actions)
        or disposition.completed_action_count != _CYCLE_ACTION_COUNTS[journal_record.cycle]
        or not _depth_matches_cycle(journal_record.cycle, depth)
        or any(outcome not in _ALLOWED_ACTION_OUTCOMES for outcome in outcomes)
    ):
        raise ExactRestoreDispositionError("disposition_archive_binding_mismatch")
    if disposition.version == 1:
        if split is not None or any(
            outcome is not ExactRestoreActionOutcome.WRITTEN_VERIFIED for outcome in outcomes
        ):
            raise ExactRestoreDispositionError("disposition_archive_binding_mismatch")
        return
    bound = disposition.maximum_pair_completion_gap_seconds
    if (
        split is None
        or disposition.predecessor_depth != depth
        or disposition.completed_action_outcomes != outcomes
        or bound is None
        or bound != policy.max_final_pair_gap_seconds
        # The stored observation must sit inside the stored bound, or a schema-valid candidate
        # could claim an over-large gap and still take the index.
        or disposition.pair_completion_gap_ns > int(bound * 1_000_000_000)
    ):
        raise ExactRestoreDispositionError("disposition_archive_binding_mismatch")


def _build_disposition(
    eligibility: VerifiedParkEligibility,
    authority: ParkedDispositionAuthority,
    *,
    recorded_at: datetime,
) -> ParkedDispositionRecord:
    manifest = eligibility.manifest
    return ParkedDispositionRecord(
        version=manifest.version,
        disposition_id=_disposition_id(manifest),
        reason=manifest.reason,
        manifest_sha256=manifest.manifest_sha256,
        operation_sha256=manifest.operation_sha256,
        operation_finalization_sha256=_operation_finalization_sha256(manifest.operation_id),
        cycle=eligibility.record.cycle,
        journal_snapshot_sha256=manifest.journal_sha256,
        baseline_sha256=eligibility.record.baseline_sha256,
        action_plan_sha256=eligibility.record.action_plan_sha256,
        authority_chain_sha256=eligibility.record.authority_chain_sha256,
        completed_action_count=len(eligibility.record.completed_actions),
        evidence=manifest.evidence,
        pair_completion_gap_ns=eligibility.artifact.pair_completion_gap_ns,
        total_attempt_ns=eligibility.total_attempt_ns,
        maximum_pair_completion_gap_seconds=(
            None if manifest.version == 1 else manifest.maximum_pair_completion_gap_seconds
        ),
        predecessor_depth=(
            None if manifest.version == 1 else _predecessor_depth(eligibility.record)
        ),
        completed_action_outcomes=(
            None
            if manifest.version == 1
            else tuple(item.outcome for item in eligibility.record.completed_actions)
        ),
        disposition_authority=authority,
        disposition_authority_sha256=authority.authority_sha256,
        recorded_at=recorded_at,
    )


def _tombstone_for(
    manifest: ParkedDispositionManifest,
    disposition: ParkedDispositionRecord,
) -> ParkedDispositionTombstone:
    return ParkedDispositionTombstone(
        operation_id=manifest.operation_id,
        operation_sha256=manifest.operation_sha256,
        disposition_id=disposition.disposition_id,
        disposition_sha256=disposition.disposition_sha256,
        journal_snapshot_sha256=disposition.journal_snapshot_sha256,
    )


class ParkedDispositionService:
    """Verify, archive, and terminally park one exact journal without device I/O."""

    def __init__(
        self,
        manifest: ParkedDispositionManifest,
        *,
        journal_store: ExactRestoreJournalStore,
        collector_store: _CollectorStore,
        archive: ExactRestoreDispositionArchive,
        guard: ExactRestoreGuard,
        wall_clock: WallClock = lambda: datetime.now(UTC),
        monotonic_clock: MonotonicClock = system_boottime_ns,
        boot_identity: BootIdentity = system_boot_identity_sha256,
    ) -> None:
        self.manifest = manifest
        self._journal_store = journal_store
        self._collector_store = collector_store
        self._archive = archive
        self._guard = guard
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._boot_identity = boot_identity

    def verify(self) -> VerifiedParkEligibility:
        existing = self._archive.load_parked_disposition(self.manifest.operation_id)
        return verify_park_eligibility(
            self.manifest,
            journal_store=self._journal_store,
            collector_store=self._collector_store,
            finalization_store=self._archive,
            expected_parked_disposition=existing,
        )

    def load_completed(self) -> ParkedDispositionRecord | None:
        """Return an exactly backed live tombstone without changing either store."""

        current = self._journal_store.load()
        if current is None:
            return None
        if not isinstance(current, Mapping):
            raise ExactRestoreDispositionError("disposition_journal_invalid")
        try:
            tombstone = ParkedDispositionTombstone.model_validate(current)
        except (TypeError, ValidationError, ValueError):
            return None
        return self._load_exact_existing(tombstone)

    def resume_if_archived(self) -> ParkedDispositionRecord | None:
        """Finish only an already-authorized park or return ``None`` before first archive."""

        completed = self.load_completed()
        if completed is not None:
            return completed
        existing = self._archive.load_parked_disposition(self.manifest.operation_id)
        if existing is None:
            return None
        return self.park(None)

    def park(
        self,
        authority: ParkedDispositionAuthority | None,
    ) -> ParkedDispositionRecord:
        """Install archive, permanent finalization, then the temporary live tombstone."""

        with self._guard.lease(), self._journal_store.claim():
            current = self._journal_store.load()
            if not isinstance(current, Mapping):
                raise ExactRestoreDispositionError("disposition_journal_absent")
            try:
                existing_tombstone = ParkedDispositionTombstone.model_validate(current)
            except (TypeError, ValidationError, ValueError):
                existing_tombstone = None
            if existing_tombstone is not None:
                return self._load_exact_existing(existing_tombstone)

            existing_disposition = self._archive.load_parked_disposition(self.manifest.operation_id)
            eligibility = verify_park_eligibility(
                self.manifest,
                journal_store=self._journal_store,
                collector_store=self._collector_store,
                finalization_store=self._archive,
                expected_parked_disposition=existing_disposition,
            )
            if existing_disposition is not None:
                self._require_existing_matches(existing_disposition, eligibility)
                disposition = existing_disposition
            else:
                if authority is None:
                    raise ExactRestoreDispositionError("disposition_authority_required")
                self._validate_authority(authority)
                disposition = _build_disposition(
                    eligibility,
                    authority,
                    recorded_at=self._now(),
                )
                self._archive.persist_parked_bundle(
                    journal_canonical=eligibility.journal_canonical,
                    disposition=disposition,
                )
                confirmed = self._archive.load_parked_disposition(self.manifest.operation_id)
                if confirmed != disposition:
                    raise ExactRestoreDispositionError("disposition_archive_unconfirmed")

            tombstone = _tombstone_for(self.manifest, disposition)
            payload = tombstone.model_dump(mode="json")
            try:
                self._journal_store.save(payload)
            except Exception:
                if not self._journal_store.reload_and_confirm_successor(payload):
                    raise ExactRestoreDispositionError(
                        "disposition_tombstone_unconfirmed"
                    ) from None
            confirmed_payload = self._journal_store.load()
            if confirmed_payload != payload:
                raise ExactRestoreDispositionError("disposition_tombstone_unconfirmed")
            try:
                terminal = self._archive.confirm_parked_tombstone(payload)
            except Exception as error:
                raise _archive_failure(error, "disposition_tombstone_unconfirmed") from error
            if (
                terminal.get("version") != 2
                or terminal.get("outcome") != "parked_unqualified"
                or terminal.get("disposition_sha256") != disposition.disposition_sha256
            ):
                raise ExactRestoreDispositionError("disposition_tombstone_unconfirmed")
            return disposition

    def _load_exact_existing(
        self,
        tombstone: ParkedDispositionTombstone,
    ) -> ParkedDispositionRecord:
        if (
            tombstone.operation_id != self.manifest.operation_id
            or tombstone.operation_sha256 != self.manifest.operation_sha256
            or tombstone.journal_snapshot_sha256 != self.manifest.journal_sha256
        ):
            raise ExactRestoreDispositionError("disposition_tombstone_conflict")
        try:
            self._archive.confirm_parked_tombstone(tombstone.model_dump(mode="json"))
            disposition = self._archive.load_parked_disposition(tombstone.operation_id)
        except ExactRestoreDispositionError:
            raise
        except Exception as error:
            raise _archive_failure(error, "disposition_tombstone_invalid") from error
        if (
            disposition is None
            or disposition.disposition_id != tombstone.disposition_id
            or disposition.disposition_sha256 != tombstone.disposition_sha256
            or disposition.manifest_sha256 != self.manifest.manifest_sha256
        ):
            raise ExactRestoreDispositionError("disposition_tombstone_conflict")
        return disposition

    def _require_existing_matches(
        self,
        disposition: ParkedDispositionRecord,
        eligibility: VerifiedParkEligibility,
    ) -> None:
        manifest = eligibility.manifest
        if (
            disposition.disposition_id != _disposition_id(manifest)
            or disposition.manifest_sha256 != manifest.manifest_sha256
            or disposition.operation_sha256 != manifest.operation_sha256
            or disposition.journal_snapshot_sha256 != manifest.journal_sha256
            or disposition.baseline_sha256 != eligibility.record.baseline_sha256
            or disposition.action_plan_sha256 != eligibility.record.action_plan_sha256
            or disposition.authority_chain_sha256 != eligibility.record.authority_chain_sha256
            or disposition.evidence != manifest.evidence
            or disposition.pair_completion_gap_ns != eligibility.artifact.pair_completion_gap_ns
            or disposition.total_attempt_ns != eligibility.total_attempt_ns
        ):
            raise ExactRestoreDispositionError("disposition_archive_conflict")

    def _validate_authority(self, authority: ParkedDispositionAuthority) -> None:
        now = self._now()
        monotonic_ns = self._monotonic_now()
        boot = self._boot_identity_now()
        if (
            authority.manifest_sha256 != self.manifest.manifest_sha256
            or authority.operation_sha256 != self.manifest.operation_sha256
            or authority.journal_sha256 != self.manifest.journal_sha256
            or authority.evidence_series_sha256 != self.manifest.evidence.series_sha256
            or authority.boot_identity_sha256 != boot
            or not authority.issued_at <= now <= authority.expires_at
            or not authority.issued_monotonic_ns <= monotonic_ns <= authority.deadline_monotonic_ns
        ):
            raise ExactRestoreDispositionError("disposition_authority_invalid")

    def _now(self) -> datetime:
        try:
            value = self._wall_clock()
        except Exception as error:
            raise ExactRestoreDispositionError("disposition_clock_invalid") from error
        if value.tzinfo is None or value.utcoffset() is None:
            raise ExactRestoreDispositionError("disposition_clock_invalid")
        return value

    def _monotonic_now(self) -> int:
        try:
            value = self._monotonic_clock()
        except Exception as error:
            raise ExactRestoreDispositionError("disposition_clock_invalid") from error
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ExactRestoreDispositionError("disposition_clock_invalid")
        return value

    def _boot_identity_now(self) -> str:
        try:
            value = self._boot_identity()
        except Exception as error:
            raise ExactRestoreDispositionError("disposition_clock_invalid") from error
        if (
            not isinstance(value, str)
            or len(value) != 64
            or any(character not in "0123456789abcdef" for character in value)
        ):
            raise ExactRestoreDispositionError("disposition_clock_invalid")
        return value


class AttendedDispositionIssuer:
    """Issue one park-only authority from a controlling TTY and consume it immediately."""

    def __init__(
        self,
        *,
        channel_factory: ChannelFactory = controlling_tty_channel,
        wall_clock: WallClock = lambda: datetime.now(UTC),
        monotonic_clock: MonotonicClock = system_boottime_ns,
        boot_identity: BootIdentity = system_boot_identity_sha256,
        entropy: Entropy = secrets.token_bytes,
    ) -> None:
        self._channel_factory = channel_factory
        self._wall_clock = wall_clock
        self._monotonic_clock = monotonic_clock
        self._boot_identity = boot_identity
        self._entropy = entropy

    def confirm_and_park(
        self,
        service: ParkedDispositionService,
        eligibility: VerifiedParkEligibility,
    ) -> ParkedDispositionRecord:
        if eligibility.manifest != service.manifest:
            raise ExactRestoreDispositionError("disposition_authority_context_mismatch")
        authority = self._confirm(eligibility)
        return service.park(authority)

    def _confirm(self, eligibility: VerifiedParkEligibility) -> ParkedDispositionAuthority:
        manifest = eligibility.manifest
        nonce = self._entropy(32)
        if len(nonce) != 32:
            raise ExactRestoreDispositionError("disposition_tty_invalid")
        challenge_digest = hashlib.sha256(
            _AUTHORITY_DOMAIN
            + bytes.fromhex(manifest.manifest_sha256)
            + bytes.fromhex(manifest.operation_sha256)
            + bytes.fromhex(manifest.journal_sha256)
            + bytes.fromhex(manifest.evidence.series_sha256)
            + nonce
        ).hexdigest()
        challenge = f"AUTHORIZE-PARK-{challenge_digest[:20].upper()}"
        outcomes = tuple(item.outcome for item in eligibility.record.completed_actions)
        outcome_counts = ", ".join(
            f"{outcome.value}={sum(1 for item in outcomes if item is outcome)}"
            for outcome in sorted({*outcomes}, key=lambda item: item.value)
        )
        definite = sum(1 for item in outcomes if item is ExactRestoreActionOutcome.WRITTEN_VERIFIED)
        possible = sum(
            1 for item in outcomes if item is ExactRestoreActionOutcome.VERIFIED_AFTER_UNCERTAIN
        )
        skipped = sum(1 for item in outcomes if item is ExactRestoreActionOutcome.ALREADY_SATISFIED)
        write_note = f"{definite} wrote, {possible} may have written, {skipped} skipped"
        summary = (
            "Jebao exact restore parked disposition authorization\n"
            f"operation_sha256: {manifest.operation_sha256}\n"
            f"journal_sha256: {manifest.journal_sha256}\n"
            f"manifest_sha256: {manifest.manifest_sha256}\n"
            f"evidence_series_sha256: {manifest.evidence.series_sha256}\n"
            f"parked_cycle: {eligibility.record.cycle.value}\n"
            f"predecessor_depth: {_predecessor_depth(eligibility.record)}\n"
            f"reason: {manifest.reason.value}\n"
            f"past_action_outcomes: {outcome_counts}\n"
            "outcome: PARKED_UNQUALIFIED\n"
            "qualification_achieved: false\n"
            "device_network_access: false\n"
            f"device_write_count: 0 (this park only; past restore actions above: {write_note})\n"
            "This does not access either device and does not create a qualification receipt.\n"
            "After immutable archive verification it replaces the live recovery journal with "
            "a non-resumable tombstone.\n"
            f"Type exactly: {challenge}\n> "
        )
        try:
            with self._channel_factory() as channel:
                before = self._sample_clock()
                channel.write(summary)
                supplied = channel.read_line(_MAX_CONFIRMATION_BYTES)
                after = self._sample_clock()
        except ExactRestoreDispositionError:
            raise
        except Exception as error:
            raise ExactRestoreDispositionError("disposition_tty_invalid") from error
        if not secrets.compare_digest(supplied, challenge):
            raise ExactRestoreDispositionError("disposition_declined")
        before_wall, before_monotonic, before_boot = before
        after_wall, after_monotonic, after_boot = after
        max_age_ns = int(_MAX_CHALLENGE_AGE.total_seconds() * 1_000_000_000)
        if (
            before_boot != after_boot
            or after_wall < before_wall
            or after_monotonic < before_monotonic
            or after_wall - before_wall > _MAX_CHALLENGE_AGE
            or after_monotonic - before_monotonic > max_age_ns
        ):
            raise ExactRestoreDispositionError("disposition_authority_context_mismatch")
        lifetime_ns = int(_AUTHORITY_LIFETIME.total_seconds() * 1_000_000_000)
        return ParkedDispositionAuthority(
            manifest_sha256=manifest.manifest_sha256,
            operation_sha256=manifest.operation_sha256,
            journal_sha256=manifest.journal_sha256,
            evidence_series_sha256=manifest.evidence.series_sha256,
            issued_at=after_wall,
            expires_at=after_wall + _AUTHORITY_LIFETIME,
            boot_identity_sha256=after_boot,
            issued_monotonic_ns=after_monotonic,
            deadline_monotonic_ns=after_monotonic + lifetime_ns,
            confirmation_token_sha256=hashlib.sha256(challenge.encode("ascii")).hexdigest(),
        )

    def _sample_clock(self) -> tuple[datetime, int, str]:
        try:
            wall = self._wall_clock()
            monotonic_ns = self._monotonic_clock()
            boot = self._boot_identity()
        except Exception as error:
            raise ExactRestoreDispositionError("disposition_authority_context_mismatch") from error
        if (
            wall.tzinfo is None
            or wall.utcoffset() is None
            or isinstance(monotonic_ns, bool)
            or not isinstance(monotonic_ns, int)
            or monotonic_ns < 0
            or not isinstance(boot, str)
            or len(boot) != 64
            or any(character not in "0123456789abcdef" for character in boot)
        ):
            raise ExactRestoreDispositionError("disposition_authority_context_mismatch")
        return wall, monotonic_ns, boot


def build_parked_disposition_service(
    manifest: ParkedDispositionManifest,
    *,
    artifact_root: str | Path,
) -> ParkedDispositionService:
    """Build the installed write-free service from fixed stores and preserved artifacts."""

    return ParkedDispositionService(
        manifest,
        journal_store=ExactRestoreJournalStore(),
        collector_store=PilotSeriesStore(Path(artifact_root)),
        archive=ExactRestoreDispositionArchive(),
        guard=ExactRestoreGuard(),
    )


def public_park_eligibility(eligibility: VerifiedParkEligibility) -> dict[str, object]:
    return {
        "device_network_access": False,
        "device_write_count": 0,
        "evidence_series_sha256": eligibility.manifest.evidence.series_sha256,
        "journal_sha256": eligibility.manifest.journal_sha256,
        "operation_sha256": eligibility.manifest.operation_sha256,
        "maximum_pair_completion_gap_seconds": (
            eligibility.manifest.maximum_pair_completion_gap_seconds
        ),
        "observed_action_outcomes": [
            item.outcome.value for item in eligibility.record.completed_actions
        ],
        "pair_completion_gap_ns": eligibility.artifact.pair_completion_gap_ns,
        "parked_cycle": eligibility.record.cycle.value,
        "predecessor_depth": _predecessor_depth(eligibility.record),
        "qualification_achieved": False,
        "reason": eligibility.manifest.reason.value,
        "status": "exact_restore_park_disposition_ready",
        "total_attempt_ns": eligibility.total_attempt_ns,
    }


def public_parked_disposition(disposition: ParkedDispositionRecord) -> dict[str, object]:
    return {
        "device_network_access": False,
        "device_write_count": 0,
        "disposition_id": disposition.disposition_id,
        "disposition_sha256": disposition.disposition_sha256,
        "journal_snapshot_sha256": disposition.journal_snapshot_sha256,
        "operation_sha256": disposition.operation_sha256,
        "parked_cycle": disposition.cycle.value,
        "phase": "parked_unqualified",
        # This is the park operation's own device write count.  Past restore actions in this
        # journal may have written; the outcome vector below is where that shows.
        "past_action_outcomes": (
            [outcome.value for outcome in disposition.completed_action_outcomes]
            if disposition.completed_action_outcomes is not None
            # Version 1 asserts a single required outcome for its whole prefix, so render that
            # rather than hiding the past actions behind a null.
            else [ExactRestoreActionOutcome.WRITTEN_VERIFIED.value]
            * disposition.completed_action_count
        ),
        # Version 1 is sentinel-only by its own validator, so its depth is 0, not unknown.
        "predecessor_depth": (
            disposition.predecessor_depth if disposition.predecessor_depth is not None else 0
        ),
        "qualification_achieved": False,
        "reason": disposition.reason.value,
        "status": "exact_restore_parked_unqualified",
    }


def public_parked_tombstone_status(
    payload: Mapping[str, object],
    *,
    archive: ExactRestoreDispositionArchive | None = None,
) -> dict[str, object]:
    try:
        tombstone = ParkedDispositionTombstone.model_validate(payload)
        selected_archive = archive or ExactRestoreDispositionArchive()
        selected_archive.confirm_parked_tombstone(tombstone.model_dump(mode="json"))
        disposition = selected_archive.load_parked_disposition(tombstone.operation_id)
    except ExactRestoreDispositionError:
        raise
    except Exception as error:
        raise _archive_failure(error, "disposition_tombstone_invalid") from error
    if (
        disposition is None
        or disposition.disposition_id != tombstone.disposition_id
        or disposition.disposition_sha256 != tombstone.disposition_sha256
    ):
        raise ExactRestoreDispositionError("disposition_tombstone_invalid")
    return public_parked_disposition(disposition)


__all__ = [
    "AttendedDispositionIssuer",
    "ExactRestoreDispositionArchive",
    "ExactRestoreDispositionError",
    "ParkedDispositionAuthority",
    "ParkedDispositionEvidenceReference",
    "ParkedDispositionManifest",
    "ParkedDispositionRecord",
    "ParkedDispositionService",
    "ParkedDispositionTombstone",
    "VerifiedParkEligibility",
    "build_parked_disposition_service",
    "load_parked_disposition_manifest",
    "public_park_eligibility",
    "public_parked_disposition",
    "public_parked_tombstone_status",
    "verify_park_eligibility",
]
