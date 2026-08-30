"""Durable bridge from attended exact restore to write-free Q2 manifests.

The Q2 caller supplies only a claim.  This adapter independently follows the two deterministic
operation-finalization indexes, reloads their immutable receipts, and reloads one qualified
baseline bundle persisted before the exact-restore journal was cleared.  Only the independently
derived value is returned.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import stat
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Literal, Protocol, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    field_validator,
    model_validator,
)

from jebao_flow.exact_restore import (
    ExactRestoreCycle,
    ExactRestorePhase,
    ExactRestoreReceipt,
    ExactRestoreRecord,
    _receipt_from_final_verified_record,
    prepare_qualified_final_restore_record,
)
from jebao_flow.exact_restore_composition import ExactRestoreOperationManifest
from jebao_flow.exact_restore_receipts import ExactRestoreReceiptArchive
from jebao_flow.exact_restore_store import ExactRestoreJournalStore
from jebao_flow.hardware_safety import (
    HardwareSafetyRootError,
    _open_hardware_safety_root,
    _validate_hardware_safety_root_descriptor,
    hardware_safety_root,
)
from jebao_flow.q2_epoch import ExactRestoreEvidence

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
_PREFIX = "exact-restore-q2-qualified-"
_SUFFIX = ".json"
_LOCK_NAME = ".exact-restore-q2-qualified.lock"
_MAX_BYTES = 128 * 1024


class ExactRestoreQ2EvidenceError(RuntimeError):
    """Privacy-safe evidence bridge failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _receipt_id(receipt_sha256: str) -> str:
    return f"ERQ-{receipt_sha256}"


def _baseline_artifact_id(baseline_sha256: str, baseline_receipt_sha256: str) -> str:
    digest = hashlib.sha256(
        b"jebao-flow/q2-qualified-baseline/v1\0"
        + bytes.fromhex(baseline_sha256)
        + bytes.fromhex(baseline_receipt_sha256)
    ).hexdigest()
    return f"ERB-{digest}"


class QualifiedBaselineBundle(BaseModel):
    """Immutable cross-link persisted before the final journal is eligible for clear."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    artifact_id: Annotated[str, StringConstraints(pattern=r"^ERB-[0-9a-f]{64}$")]
    baseline_sha256: Sha256Digest
    sentinel_operation_id: str
    sentinel_receipt_sha256: Sha256Digest
    baseline_operation_id: str
    baseline_receipt_sha256: Sha256Digest
    qualification_receipt_sha256: Sha256Digest
    qualified_record: ExactRestoreRecord = Field(repr=False)
    expected_identity_bindings_sha256: tuple[Sha256Digest, Sha256Digest]
    maximum_handoff_age_seconds: float = Field(gt=0, allow_inf_nan=False)
    sentinel_completed_at: datetime
    baseline_completed_at: datetime
    persisted_at: datetime

    @field_validator("sentinel_completed_at", "baseline_completed_at", "persisted_at")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("qualified baseline timestamps must be UTC")
        return value

    @model_validator(mode="after")
    def validate_cross_links(self) -> Self:
        if self.artifact_id != _baseline_artifact_id(
            self.baseline_sha256,
            self.baseline_receipt_sha256,
        ):
            raise ValueError("qualified baseline artifact id mismatch")
        if self.qualification_receipt_sha256 != self.sentinel_receipt_sha256:
            raise ValueError("qualified baseline receipt chain mismatch")
        if len(set(self.expected_identity_bindings_sha256)) != 2:
            raise ValueError("qualified baseline bindings must be distinct")
        if not self.sentinel_completed_at <= self.baseline_completed_at <= self.persisted_at:
            raise ValueError("qualified baseline timestamps are out of order")
        record = self.qualified_record
        qualification = record.qualification_final_record
        if (
            record.cycle is not ExactRestoreCycle.BASELINE_RESTORE
            or record.phase is not ExactRestorePhase.FINAL_VERIFIED
            or record.baseline_sha256 != self.baseline_sha256
            or qualification is None
            or qualification.cycle is not ExactRestoreCycle.SENTINEL_QUALIFICATION
            or qualification.phase is not ExactRestorePhase.FINAL_VERIFIED
            or tuple(device.identity_binding_sha256 for device in record.baseline.devices)
            != self.expected_identity_bindings_sha256
            or record.baseline.verification_policy.max_observation_age_seconds
            != self.maximum_handoff_age_seconds
        ):
            raise ValueError("qualified baseline record does not match the bundle")
        baseline_receipt = _receipt_from_final_verified_record(record)
        sentinel_receipt = _receipt_from_final_verified_record(qualification)
        if (
            baseline_receipt.receipt_sha256 != self.baseline_receipt_sha256
            or sentinel_receipt.receipt_sha256 != self.sentinel_receipt_sha256
            or baseline_receipt.qualification_receipt_sha256 != self.qualification_receipt_sha256
            or baseline_receipt.completed_at != self.baseline_completed_at
            or sentinel_receipt.completed_at != self.sentinel_completed_at
        ):
            raise ValueError("qualified baseline record receipt chain mismatch")
        return self


class _ReceiptArchive(Protocol):
    def load_final_verified_receipt(self, receipt_sha256: str) -> Mapping[str, object] | None: ...
    def load_operation_finalization(self, operation_id: str) -> Mapping[str, object] | None: ...
    def confirm_operation_finalization(
        self, receipt: ExactRestoreReceipt
    ) -> Mapping[str, object]: ...


class _JournalStore(Protocol):
    def load(self) -> Mapping[str, object] | None: ...


class QualifiedBaselineArchive:
    """No-clobber owner-only archive below the deployment safety root."""

    def __init__(self) -> None:
        self._root = hardware_safety_root().absolute()
        self._validate_fixed_root = True

    @classmethod
    def _for_test(cls, root: Path) -> QualifiedBaselineArchive:
        archive = cls.__new__(cls)
        archive._root = Path(root).absolute()
        archive._validate_fixed_root = False
        return archive

    @contextmanager
    def _claim(self) -> Iterator[int]:
        root_descriptor = -1
        lock_descriptor = -1
        locked = False
        try:
            if self._validate_fixed_root:
                root_descriptor = _open_hardware_safety_root()
            else:
                flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                if hasattr(os, "O_CLOEXEC"):
                    flags |= os.O_CLOEXEC
                root_descriptor = os.open(self._root, flags)
                metadata = os.fstat(root_descriptor)
                if (
                    not stat.S_ISDIR(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                ):
                    raise ExactRestoreQ2EvidenceError("qualified_archive_root_invalid")
            lock_descriptor = os.open(
                _LOCK_NAME,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=root_descriptor,
            )
            os.fchmod(lock_descriptor, 0o600)
            opened = os.fstat(lock_descriptor)
            named = os.stat(_LOCK_NAME, dir_fd=root_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or opened.st_uid != os.geteuid()
                or named.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or stat.S_IMODE(named.st_mode) != 0o600
                or opened.st_nlink != 1
                or named.st_nlink != 1
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise ExactRestoreQ2EvidenceError("qualified_archive_lock_invalid")
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except BlockingIOError as error:
                raise ExactRestoreQ2EvidenceError("qualified_archive_busy") from error
            if self._validate_fixed_root:
                _validate_hardware_safety_root_descriptor(root_descriptor)
            yield root_descriptor
        except ExactRestoreQ2EvidenceError:
            raise
        except (HardwareSafetyRootError, OSError) as error:
            raise ExactRestoreQ2EvidenceError("qualified_archive_unavailable") from error
        finally:
            if locked and lock_descriptor >= 0:
                try:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            if lock_descriptor >= 0:
                os.close(lock_descriptor)
            if root_descriptor >= 0:
                os.close(root_descriptor)

    @staticmethod
    def _name(artifact_id: str) -> str:
        if (
            not isinstance(artifact_id, str)
            or len(artifact_id) != 68
            or not artifact_id.startswith("ERB-")
            or any(character not in "0123456789abcdef" for character in artifact_id[4:])
        ):
            raise ExactRestoreQ2EvidenceError("qualified_artifact_id_invalid")
        return f"{_PREFIX}{artifact_id[4:]}{_SUFFIX}"

    def persist(self, bundle: QualifiedBaselineBundle) -> None:
        try:
            validated = QualifiedBaselineBundle.model_validate(bundle.model_dump(mode="json"))
        except (AttributeError, ValidationError, ValueError) as error:
            raise ExactRestoreQ2EvidenceError("qualified_bundle_invalid") from error
        canonical = _canonical_json(validated.model_dump(mode="json"))
        name = self._name(validated.artifact_id)
        with self._claim() as root_descriptor:
            existing = self._load_under_claim(root_descriptor, validated.artifact_id)
            if existing is not None:
                if existing != validated:
                    raise ExactRestoreQ2EvidenceError("qualified_bundle_conflict")
                return
            temporary = f".{name}.tmp-{secrets.token_hex(12)}"
            descriptor = -1
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                    0o600,
                    dir_fd=root_descriptor,
                )
                os.fchmod(descriptor, 0o600)
                view = memoryview(canonical)
                while view:
                    written = os.write(descriptor, view)
                    if written <= 0:
                        raise OSError("short qualified bundle write")
                    view = view[written:]
                os.fsync(descriptor)
                os.close(descriptor)
                descriptor = -1
                os.link(
                    temporary,
                    name,
                    src_dir_fd=root_descriptor,
                    dst_dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
                os.fsync(root_descriptor)
            except FileExistsError:
                contender = self._load_under_claim(root_descriptor, validated.artifact_id)
                if contender != validated:
                    raise ExactRestoreQ2EvidenceError("qualified_bundle_conflict") from None
            except ExactRestoreQ2EvidenceError:
                raise
            except OSError as error:
                raise ExactRestoreQ2EvidenceError("qualified_bundle_persist_failed") from error
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                try:
                    os.unlink(temporary, dir_fd=root_descriptor)
                except FileNotFoundError:
                    pass
                except OSError as error:
                    raise ExactRestoreQ2EvidenceError(
                        "qualified_bundle_cleanup_unconfirmed"
                    ) from error
                os.fsync(root_descriptor)
            confirmed = self._load_under_claim(root_descriptor, validated.artifact_id)
            if confirmed != validated:
                raise ExactRestoreQ2EvidenceError("qualified_bundle_confirmation_failed")

    def load(self, artifact_id: str) -> QualifiedBaselineBundle | None:
        with self._claim() as root_descriptor:
            return self._load_under_claim(root_descriptor, artifact_id)

    def _load_under_claim(
        self,
        root_descriptor: int,
        artifact_id: str,
    ) -> QualifiedBaselineBundle | None:
        name = self._name(artifact_id)
        descriptor = -1
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=root_descriptor,
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            raise ExactRestoreQ2EvidenceError("qualified_bundle_unreadable") from error
        try:
            opened = os.fstat(descriptor)
            named = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not stat.S_ISREG(named.st_mode)
                or opened.st_uid != os.geteuid()
                or named.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o600
                or stat.S_IMODE(named.st_mode) != 0o600
                or opened.st_nlink != 1
                or named.st_nlink != 1
                or opened.st_size > _MAX_BYTES
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
            ):
                raise ExactRestoreQ2EvidenceError("qualified_bundle_metadata_invalid")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(descriptor, min(65_536, _MAX_BYTES + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > _MAX_BYTES:
                    break
            payload = b"".join(chunks)
            if len(payload) > _MAX_BYTES:
                raise ExactRestoreQ2EvidenceError("qualified_bundle_too_large")
            after = os.fstat(descriptor)
            named_after = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)

            def generation(value: os.stat_result) -> tuple[int, ...]:
                return (
                    value.st_dev,
                    value.st_ino,
                    value.st_mode,
                    value.st_uid,
                    value.st_nlink,
                    value.st_size,
                    value.st_mtime_ns,
                    value.st_ctime_ns,
                )

            if (
                generation(opened) != generation(after)
                or generation(after) != generation(named_after)
                or len(payload) != after.st_size
            ):
                raise ExactRestoreQ2EvidenceError("qualified_bundle_changed")
            bundle = QualifiedBaselineBundle.model_validate_json(payload)
            if bundle.artifact_id != artifact_id:
                raise ExactRestoreQ2EvidenceError("qualified_bundle_artifact_mismatch")
            if _canonical_json(bundle.model_dump(mode="json")) != payload:
                raise ExactRestoreQ2EvidenceError("qualified_bundle_noncanonical")
            return bundle
        except ExactRestoreQ2EvidenceError:
            raise
        except (OSError, ValidationError, ValueError) as error:
            raise ExactRestoreQ2EvidenceError("qualified_bundle_invalid") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)


def build_qualified_baseline_bundle(
    *,
    manifest: ExactRestoreOperationManifest,
    record: ExactRestoreRecord,
    baseline_receipt: ExactRestoreReceipt,
    now: datetime | None = None,
) -> QualifiedBaselineBundle:
    qualification = record.qualification_final_record
    if (
        record.phase is not ExactRestorePhase.FINAL_VERIFIED
        or record.cycle is not ExactRestoreCycle.BASELINE_RESTORE
        or qualification is None
        or qualification.phase is not ExactRestorePhase.FINAL_VERIFIED
    ):
        raise ExactRestoreQ2EvidenceError("qualified_record_not_final")
    try:
        expected_baseline = _receipt_from_final_verified_record(record)
        sentinel_receipt = _receipt_from_final_verified_record(qualification)
    except ValueError as error:
        raise ExactRestoreQ2EvidenceError("qualified_receipt_invalid") from error
    if baseline_receipt != expected_baseline:
        raise ExactRestoreQ2EvidenceError("qualified_receipt_mismatch")
    if (
        sentinel_receipt.operation_id != manifest.sentinel_operation_id
        or baseline_receipt.operation_id != manifest.baseline_operation_id
        or sentinel_receipt.baseline_sha256 != record.baseline_sha256
        or baseline_receipt.baseline_sha256 != record.baseline_sha256
        or baseline_receipt.qualification_receipt_sha256 != sentinel_receipt.receipt_sha256
        or record.baseline.verification_policy != manifest.verification_policy
    ):
        raise ExactRestoreQ2EvidenceError("qualified_chain_mismatch")
    persisted_at = now or datetime.now(UTC)
    try:
        return QualifiedBaselineBundle(
            artifact_id=_baseline_artifact_id(
                record.baseline_sha256,
                baseline_receipt.receipt_sha256,
            ),
            baseline_sha256=record.baseline_sha256,
            sentinel_operation_id=manifest.sentinel_operation_id,
            sentinel_receipt_sha256=sentinel_receipt.receipt_sha256,
            baseline_operation_id=manifest.baseline_operation_id,
            baseline_receipt_sha256=baseline_receipt.receipt_sha256,
            qualification_receipt_sha256=baseline_receipt.qualification_receipt_sha256,
            qualified_record=record,
            expected_identity_bindings_sha256=tuple(
                device.identity_binding_sha256 for device in record.baseline.devices
            ),
            maximum_handoff_age_seconds=(
                record.baseline.verification_policy.max_observation_age_seconds
            ),
            sentinel_completed_at=sentinel_receipt.completed_at,
            baseline_completed_at=baseline_receipt.completed_at,
            persisted_at=persisted_at,
        )
    except ValueError as error:
        raise ExactRestoreQ2EvidenceError("qualified_bundle_invalid") from error


class ExactRestoreQ2EvidenceVerifier:
    """Derive Q2 evidence from immutable indexes and receipts, never from caller claims."""

    def __init__(
        self,
        manifest: ExactRestoreOperationManifest,
        *,
        receipt_archive: _ReceiptArchive | None = None,
        qualified_archive: QualifiedBaselineArchive | None = None,
        journal_store: _JournalStore | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._manifest = manifest
        self._receipts = receipt_archive or ExactRestoreReceiptArchive()
        self._qualified = qualified_archive or QualifiedBaselineArchive()
        self._journal = journal_store or ExactRestoreJournalStore()
        self._clock = clock

    def verify_q2_exact_restore_evidence(
        self,
        evidence: ExactRestoreEvidence,
        *,
        not_after: datetime,
    ) -> ExactRestoreEvidence:
        derived = self.derive_q2_exact_restore_evidence(not_after=not_after)
        if evidence != derived:
            raise ExactRestoreQ2EvidenceError("q2_restore_evidence_claim_mismatch")
        return derived

    def verify_q2_historical_exact_restore_evidence(
        self,
        evidence: ExactRestoreEvidence,
        *,
        not_after: datetime,
    ) -> ExactRestoreEvidence:
        """Reverify a precommitted staging claim after phase 5 cleared the live journal.

        Q2 manifest creation still calls :meth:`verify_q2_exact_restore_evidence`, which requires
        the exact PREPARED journal to exist at that moment.  Later offline classification may run
        only from that immutable manifest.  The staged record is reconstructed from the qualified
        bundle's fixed timestamp and manifest-derived operation id, so the recorded digest remains
        independently checkable without requiring a successfully completed restore to leave stale
        recovery state behind.
        """

        sentinel, baseline, bundle = self._load_verified_bundle(not_after=not_after)
        staged = self._expected_staged_final_restore(bundle)
        derived = self._evidence(sentinel, baseline, bundle, staged)
        if evidence != derived:
            raise ExactRestoreQ2EvidenceError("q2_restore_evidence_claim_mismatch")
        return derived

    def load_verified_qualified_bundle(
        self,
        *,
        not_after: datetime | None = None,
    ) -> QualifiedBaselineBundle:
        """Return the immutable qualified record after the full receipt-chain verification."""

        _sentinel, _baseline, bundle = self._load_verified_bundle(not_after=not_after)
        return bundle

    def derive_q2_exact_restore_evidence(
        self,
        *,
        not_after: datetime | None = None,
    ) -> ExactRestoreEvidence:
        """Build the only claim represented by the two finalization indexes and bundle."""

        sentinel, baseline, bundle = self._load_verified_bundle(not_after=not_after)
        staged = self._load_staged_final_restore(bundle)
        return self._evidence(sentinel, baseline, bundle, staged)

    def _evidence(
        self,
        sentinel: ExactRestoreReceipt,
        baseline: ExactRestoreReceipt,
        bundle: QualifiedBaselineBundle,
        staged: ExactRestoreRecord,
    ) -> ExactRestoreEvidence:
        return ExactRestoreEvidence(
            baseline_artifact_id=bundle.artifact_id,
            baseline_sha256=bundle.baseline_sha256,
            sentinel_receipt_id=_receipt_id(sentinel.receipt_sha256),
            sentinel_receipt_sha256=sentinel.receipt_sha256,
            baseline_receipt_id=_receipt_id(baseline.receipt_sha256),
            baseline_receipt_sha256=baseline.receipt_sha256,
            final_restore_operation_id=staged.operation_id,
            final_restore_record_sha256=staged.authority_context_sha256,
            expected_identity_bindings_sha256=bundle.expected_identity_bindings_sha256,
        )

    def _expected_staged_final_restore(
        self,
        bundle: QualifiedBaselineBundle,
    ) -> ExactRestoreRecord:
        try:
            return prepare_qualified_final_restore_record(
                bundle.qualified_record,
                operation_id=self._manifest.final_restore_operation_id,
                now=bundle.persisted_at,
            )
        except (TypeError, ValueError) as error:
            raise ExactRestoreQ2EvidenceError("q2_final_restore_stage_mismatch") from error

    def _load_verified_bundle(
        self,
        *,
        not_after: datetime | None,
    ) -> tuple[ExactRestoreReceipt, ExactRestoreReceipt, QualifiedBaselineBundle]:
        sentinel = self._load_cycle(
            self._manifest.sentinel_operation_id,
            ExactRestoreCycle.SENTINEL_QUALIFICATION,
        )
        baseline = self._load_cycle(
            self._manifest.baseline_operation_id,
            ExactRestoreCycle.BASELINE_RESTORE,
        )
        if (
            sentinel.baseline_sha256 != baseline.baseline_sha256
            or baseline.qualification_receipt_sha256 != sentinel.receipt_sha256
            or sentinel.completed_at > baseline.completed_at
        ):
            raise ExactRestoreQ2EvidenceError("q2_restore_receipt_chain_invalid")
        artifact_id = _baseline_artifact_id(
            baseline.baseline_sha256,
            baseline.receipt_sha256,
        )
        bundle = self._qualified.load(artifact_id)
        if bundle is None:
            raise ExactRestoreQ2EvidenceError("q2_restore_bundle_absent")
        if (
            bundle.baseline_sha256 != baseline.baseline_sha256
            or bundle.sentinel_operation_id != sentinel.operation_id
            or bundle.sentinel_receipt_sha256 != sentinel.receipt_sha256
            or bundle.baseline_operation_id != baseline.operation_id
            or bundle.baseline_receipt_sha256 != baseline.receipt_sha256
            or bundle.qualification_receipt_sha256 != sentinel.receipt_sha256
            or bundle.maximum_handoff_age_seconds
            != self._manifest.verification_policy.max_observation_age_seconds
            or bundle.sentinel_completed_at != sentinel.completed_at
            or bundle.baseline_completed_at != baseline.completed_at
        ):
            raise ExactRestoreQ2EvidenceError("q2_restore_bundle_mismatch")
        cutoff = self._clock() if not_after is None else not_after
        if (
            not isinstance(cutoff, datetime)
            or cutoff.tzinfo is None
            or cutoff.utcoffset() != timedelta(0)
            or baseline.completed_at > cutoff
            or bundle.persisted_at > cutoff
            or (cutoff - baseline.completed_at).total_seconds() > bundle.maximum_handoff_age_seconds
        ):
            raise ExactRestoreQ2EvidenceError("q2_restore_not_completed_before_manifest")
        return sentinel, baseline, bundle

    def _load_staged_final_restore(
        self,
        bundle: QualifiedBaselineBundle,
    ) -> ExactRestoreRecord:
        try:
            payload = self._journal.load()
            if not isinstance(payload, Mapping):
                raise ExactRestoreQ2EvidenceError("q2_final_restore_not_staged")
            record = ExactRestoreRecord.model_validate(payload)
        except ExactRestoreQ2EvidenceError:
            raise
        except (TypeError, ValidationError, ValueError) as error:
            raise ExactRestoreQ2EvidenceError("q2_final_restore_journal_invalid") from error
        except Exception as error:
            raise ExactRestoreQ2EvidenceError("q2_final_restore_journal_unavailable") from error
        if record != self._expected_staged_final_restore(bundle):
            raise ExactRestoreQ2EvidenceError("q2_final_restore_stage_mismatch")
        return record

    def _load_cycle(
        self,
        operation_id: str,
        cycle: ExactRestoreCycle,
    ) -> ExactRestoreReceipt:
        try:
            finalization = self._receipts.load_operation_finalization(operation_id)
            if not isinstance(finalization, Mapping):
                raise ExactRestoreQ2EvidenceError("q2_restore_unfinalized")
            receipt_sha = finalization.get("receipt_sha256")
            if finalization.get("cycle") != cycle.value or not isinstance(receipt_sha, str):
                raise ExactRestoreQ2EvidenceError("q2_restore_finalization_mismatch")
            payload = self._receipts.load_final_verified_receipt(receipt_sha)
            if not isinstance(payload, Mapping):
                raise ExactRestoreQ2EvidenceError("q2_restore_receipt_absent")
            receipt = ExactRestoreReceipt.model_validate(payload)
            if (
                receipt.receipt_sha256 != receipt_sha
                or receipt.operation_id != operation_id
                or receipt.cycle is not cycle
            ):
                raise ExactRestoreQ2EvidenceError("q2_restore_receipt_mismatch")
            confirmed = self._receipts.confirm_operation_finalization(receipt)
            if dict(confirmed) != dict(finalization):
                raise ExactRestoreQ2EvidenceError("q2_restore_finalization_mismatch")
            return receipt
        except ExactRestoreQ2EvidenceError:
            raise
        except (TypeError, ValidationError, ValueError) as error:
            raise ExactRestoreQ2EvidenceError("q2_restore_archive_invalid") from error
        except Exception as error:
            raise ExactRestoreQ2EvidenceError("q2_restore_archive_unavailable") from error


__all__ = [
    "ExactRestoreQ2EvidenceError",
    "ExactRestoreQ2EvidenceVerifier",
    "QualifiedBaselineArchive",
    "QualifiedBaselineBundle",
    "build_qualified_baseline_bundle",
]
