"""Immutable durable archive for exact-restore qualification receipts.

Each receipt is addressed by the SHA-256 digest derived by ``ExactRestoreReceipt``.  Production
callers cannot choose a directory: every archive entry and its claim file live directly below the
deployment-wide ``/hardware-safety`` mount.  Entries are immutable.  A repeated save is accepted
only when the existing canonical bytes describe the exact same receipt.

The archive deliberately uses an exclusive hard-link install instead of a clobbering rename.  A
temporary file is fsynced first, then linked to the digest-derived final name without replacing an
existing entry.  This closes the absent-check/replace race while retaining an atomic namespace
transition.  The temporary link is removed and both directory transitions are fsynced before an
exact reload confirmation succeeds.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock, get_ident
from types import TracebackType
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, model_validator

from jebao_flow.exact_restore import ExactRestoreCycle, ExactRestoreReceipt
from jebao_flow.hardware_safety import (
    HardwareSafetyRootError,
    _open_hardware_safety_root,
    _validate_hardware_safety_root_descriptor,
    hardware_safety_root,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_RECEIPT_PREFIX = "exact-restore-receipt-"
_RECEIPT_SUFFIX = ".json"
_FINALIZATION_PREFIX = "exact-restore-finalized-"
_FINALIZATION_SUFFIX = ".json"
_LOCK_NAME = ".exact-restore-receipts.lock"
_OPERATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_OPERATION_DIGEST_DOMAIN = b"jebao-flow/exact-restore-operation-finalization/v1\0"
_Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ExactRestoreReceiptArchiveError(RuntimeError):
    """A receipt could not be archived or read without weakening its evidence boundary."""


class ExactRestoreReceiptArchiveClaimError(ExactRestoreReceiptArchiveError):
    """The immutable receipt namespace could not be claimed by this owner."""


class ExactRestoreOperationFinalizationConflictError(ExactRestoreReceiptArchiveError):
    """An operation id is already bound to a different immutable final receipt."""


class ExactRestoreOperationFinalization(BaseModel):
    """Privacy-preserving operation-to-receipt binding stored below the safety root."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation_sha256: _Sha256Digest
    receipt_sha256: _Sha256Digest
    cycle: ExactRestoreCycle

    @model_validator(mode="after")
    def require_supported_cycle(self) -> Self:
        # Future cycles must not silently inherit v1 finalization semantics.
        if self.cycle not in {
            ExactRestoreCycle.SENTINEL_QUALIFICATION,
            ExactRestoreCycle.BASELINE_RESTORE,
        }:
            raise ValueError("unsupported exact-restore finalization cycle")
        return self


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int


@dataclass(frozen=True)
class _ReceiptSnapshot:
    receipt: ExactRestoreReceipt
    canonical: bytes
    identity: _FileIdentity


@dataclass(frozen=True)
class _FinalizationSnapshot:
    finalization: ExactRestoreOperationFinalization
    canonical: bytes
    identity: _FileIdentity


class _ReceiptArchiveClaimContext:
    """Release a claim only from the process and thread that acquired it."""

    def __init__(self, archive: ExactRestoreReceiptArchive) -> None:
        self._archive = archive
        self._active = False
        self._owner_pid = -1
        self._owner_thread_id = -1

    def __enter__(self) -> None:
        if self._active:
            raise ExactRestoreReceiptArchiveClaimError(
                "exact-restore receipt claim context is already active"
            )
        self._archive._acquire_claim()
        self._owner_pid = os.getpid()
        self._owner_thread_id = get_ident()
        self._active = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, exc_value, traceback
        if not self._active:
            raise ExactRestoreReceiptArchiveClaimError(
                "exact-restore receipt claim context is not active"
            )
        if self._owner_pid != os.getpid() or self._owner_thread_id != get_ident():
            raise ExactRestoreReceiptArchiveClaimError(
                "exact-restore receipt claim must be released by its owner process and thread"
            )
        try:
            self._archive._release_claim()
        finally:
            self._active = False
        return False


class ExactRestoreReceiptArchive:
    """Production implementation of ``ExactRestoreQualificationReceiptStore``.

    ``persist_final_verified_receipt`` and ``load_final_verified_receipt`` acquire the same
    deployment-wide, nonblocking archive claim.  An instance is process-bound: a forked child must
    construct its own archive instead of inheriting descriptors or lock ownership from its parent.
    """

    _DEFAULT_MAX_BYTES = 64 * 1024

    def __init__(self, *, max_bytes: int = _DEFAULT_MAX_BYTES) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        self._initialize(hardware_safety_root(), max_bytes=max_bytes, validate_fixed_root=True)

    @classmethod
    def _for_test(
        cls,
        root: str | Path,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> ExactRestoreReceiptArchive:
        """Build an isolated archive; production callers always use the fixed safety mount."""

        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        archive = cls.__new__(cls)
        archive._initialize(Path(root), max_bytes=max_bytes, validate_fixed_root=False)
        return archive

    def _initialize(
        self,
        root: Path,
        *,
        max_bytes: int,
        validate_fixed_root: bool,
    ) -> None:
        self.root = root.absolute()
        self.lock_path = self.root / _LOCK_NAME
        self._max_bytes = max_bytes
        self._validate_fixed_root = validate_fixed_root
        self._creation_pid = os.getpid()
        self._thread_lock = RLock()
        self._claim_active = False
        self._claim_root_descriptor = -1
        self._claim_lock_descriptor = -1
        self._claim_pid = -1
        self._claim_thread_id = -1

    def path_for_receipt(self, receipt_sha256: str) -> Path:
        """Return the fixed digest-derived path after validating the digest."""

        digest = self._validate_digest(receipt_sha256)
        return self.root / self._receipt_name(digest)

    def path_for_operation_finalization(self, operation_id: str) -> Path:
        """Return an opaque operation-digest path without exposing the raw operation id."""

        digest = self._operation_digest(operation_id)
        return self.root / self._finalization_name(digest)

    def persist_final_verified_receipt(self, receipt: ExactRestoreReceipt) -> None:
        """Durably archive a receipt and its immutable operation finalization index.

        Success is returned only after both files have been fsynced, their namespace transitions
        have been fsynced, and exact reload confirmation has revalidated the cross-file binding.
        A crash before the index install may leave an inert receipt orphan; a retry repairs that
        state idempotently.  An index without its exact receipt is always corruption and is never
        repaired implicitly.
        """

        validated = self._validated_receipt(receipt)
        canonical = self._canonical_receipt(validated)
        finalization = self._finalization_from_receipt(validated)
        finalization_canonical = self._canonical_finalization(finalization)

        with self._claim():
            root_descriptor = self._require_active_claim()
            operation_digest = finalization.operation_sha256
            existing_finalization = self._load_finalization_snapshot(
                root_descriptor,
                operation_digest,
                allow_absent=True,
            )
            if existing_finalization is not None:
                self._require_exact_finalization_snapshot(
                    existing_finalization,
                    finalization,
                    finalization_canonical,
                )
                self._confirm_exact_bundle_under_claim(
                    root_descriptor,
                    validated,
                    canonical,
                    finalization,
                    finalization_canonical,
                    finalization_identity=existing_finalization.identity,
                )
                return

            self._persist_receipt_under_claim(root_descriptor, validated, canonical)
            finalization_snapshot = self._persist_finalization_under_claim(
                root_descriptor,
                finalization,
                finalization_canonical,
            )
            self._confirm_exact_bundle_under_claim(
                root_descriptor,
                validated,
                canonical,
                finalization,
                finalization_canonical,
                finalization_identity=finalization_snapshot.identity,
            )

    def load_final_verified_receipt(
        self,
        receipt_sha256: str,
    ) -> Mapping[str, object] | None:
        """Load one canonical receipt and rederive its digest from the stored contents."""

        digest = self._validate_digest(receipt_sha256)
        with self._claim():
            snapshot = self._load_snapshot(
                self._require_active_claim(),
                digest,
                allow_absent=True,
            )
            if snapshot is None:
                return None
            return snapshot.receipt.model_dump(mode="json")

    def load_operation_finalization(
        self,
        operation_id: str,
    ) -> Mapping[str, object] | None:
        """Return a valid receipt-backed finalization, ``None`` when absent, or raise on damage."""

        operation_digest = self._operation_digest(operation_id)
        with self._claim():
            root_descriptor = self._require_active_claim()
            snapshot = self._load_finalization_snapshot(
                root_descriptor,
                operation_digest,
                allow_absent=True,
            )
            if snapshot is None:
                return None
            self._require_receipt_backing_finalization(root_descriptor, snapshot.finalization)
            return snapshot.finalization.model_dump(mode="json")

    def confirm_operation_finalization(
        self,
        receipt: ExactRestoreReceipt,
    ) -> Mapping[str, object]:
        """Prove the exact operation/receipt/cycle bundle exists before journal clear."""

        validated = self._validated_receipt(receipt)
        canonical = self._canonical_receipt(validated)
        finalization = self._finalization_from_receipt(validated)
        finalization_canonical = self._canonical_finalization(finalization)
        with self._claim():
            root_descriptor = self._require_active_claim()
            snapshot = self._load_finalization_snapshot(
                root_descriptor,
                finalization.operation_sha256,
                allow_absent=False,
            )
            if snapshot is None:  # pragma: no cover - guarded by allow_absent=False
                raise ExactRestoreReceiptArchiveError(
                    "exact-restore operation finalization is absent"
                )
            self._require_exact_finalization_snapshot(
                snapshot,
                finalization,
                finalization_canonical,
            )
            self._confirm_exact_bundle_under_claim(
                root_descriptor,
                validated,
                canonical,
                finalization,
                finalization_canonical,
                finalization_identity=snapshot.identity,
            )
            return snapshot.finalization.model_dump(mode="json")

    def _persist_receipt_under_claim(
        self,
        root_descriptor: int,
        receipt: ExactRestoreReceipt,
        canonical: bytes,
    ) -> _ReceiptSnapshot:
        digest = receipt.receipt_sha256
        existing = self._load_snapshot(root_descriptor, digest, allow_absent=True)
        if existing is not None:
            self._require_exact_snapshot(existing, receipt, canonical)
            self._durably_confirm_snapshot(
                root_descriptor,
                digest,
                receipt,
                canonical,
                expected_identity=existing.identity,
            )
            return existing

        temporary_name: str | None = None
        installed = False
        try:
            temporary_name = self._write_temporary(
                root_descriptor,
                _RECEIPT_PREFIX,
                digest,
                canonical,
            )
            self._validate_active_claim()
            try:
                os.link(
                    temporary_name,
                    self._receipt_name(digest),
                    src_dir_fd=root_descriptor,
                    dst_dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
                installed = True
            except FileExistsError:
                contender = self._load_snapshot(root_descriptor, digest, allow_absent=False)
                if contender is None:  # pragma: no cover - guarded by allow_absent=False
                    raise ExactRestoreReceiptArchiveError(
                        "conflicting exact-restore receipt disappeared"
                    ) from None
                self._require_exact_snapshot(contender, receipt, canonical)
                self._durably_confirm_snapshot(
                    root_descriptor,
                    digest,
                    receipt,
                    canonical,
                    expected_identity=contender.identity,
                )
                return contender

            self._complete_install(root_descriptor, temporary_name)
            temporary_name = None
            installed_snapshot = self._load_snapshot(
                root_descriptor,
                digest,
                allow_absent=False,
            )
            if installed_snapshot is None:  # pragma: no cover - guarded by allow_absent=False
                raise ExactRestoreReceiptArchiveError("installed exact-restore receipt disappeared")
            self._require_exact_snapshot(installed_snapshot, receipt, canonical)
            self._durably_confirm_snapshot(
                root_descriptor,
                digest,
                receipt,
                canonical,
                expected_identity=installed_snapshot.identity,
            )
            return installed_snapshot
        except (ExactRestoreReceiptArchiveClaimError, ExactRestoreReceiptArchiveError):
            raise
        except OSError as error:
            action = "confirm" if installed else "persist"
            raise ExactRestoreReceiptArchiveError(
                f"cannot {action} exact-restore receipt durably"
            ) from error
        finally:
            if temporary_name is not None:
                self._unlink_temporary(root_descriptor, temporary_name)

    def _persist_finalization_under_claim(
        self,
        root_descriptor: int,
        finalization: ExactRestoreOperationFinalization,
        canonical: bytes,
    ) -> _FinalizationSnapshot:
        operation_digest = finalization.operation_sha256
        existing = self._load_finalization_snapshot(
            root_descriptor,
            operation_digest,
            allow_absent=True,
        )
        if existing is not None:
            self._require_exact_finalization_snapshot(existing, finalization, canonical)
            self._durably_confirm_finalization_snapshot(
                root_descriptor,
                finalization,
                canonical,
                expected_identity=existing.identity,
            )
            return existing

        temporary_name: str | None = None
        installed = False
        try:
            temporary_name = self._write_temporary(
                root_descriptor,
                _FINALIZATION_PREFIX,
                operation_digest,
                canonical,
            )
            self._validate_active_claim()
            try:
                os.link(
                    temporary_name,
                    self._finalization_name(operation_digest),
                    src_dir_fd=root_descriptor,
                    dst_dir_fd=root_descriptor,
                    follow_symlinks=False,
                )
                installed = True
            except FileExistsError:
                contender = self._load_finalization_snapshot(
                    root_descriptor,
                    operation_digest,
                    allow_absent=False,
                )
                if contender is None:  # pragma: no cover - guarded by allow_absent=False
                    raise ExactRestoreReceiptArchiveError(
                        "conflicting exact-restore operation finalization disappeared"
                    ) from None
                self._require_exact_finalization_snapshot(contender, finalization, canonical)
                self._durably_confirm_finalization_snapshot(
                    root_descriptor,
                    finalization,
                    canonical,
                    expected_identity=contender.identity,
                )
                return contender

            self._complete_install(root_descriptor, temporary_name)
            temporary_name = None
            installed_snapshot = self._load_finalization_snapshot(
                root_descriptor,
                operation_digest,
                allow_absent=False,
            )
            if installed_snapshot is None:  # pragma: no cover - guarded by allow_absent=False
                raise ExactRestoreReceiptArchiveError(
                    "installed exact-restore operation finalization disappeared"
                )
            self._require_exact_finalization_snapshot(installed_snapshot, finalization, canonical)
            self._durably_confirm_finalization_snapshot(
                root_descriptor,
                finalization,
                canonical,
                expected_identity=installed_snapshot.identity,
            )
            return installed_snapshot
        except (ExactRestoreReceiptArchiveClaimError, ExactRestoreReceiptArchiveError):
            raise
        except OSError as error:
            action = "confirm" if installed else "persist"
            raise ExactRestoreReceiptArchiveError(
                f"cannot {action} exact-restore operation finalization durably"
            ) from error
        finally:
            if temporary_name is not None:
                self._unlink_temporary(root_descriptor, temporary_name)

    def _complete_install(self, root_descriptor: int, temporary_name: str) -> None:
        self._validate_active_claim()
        self._fsync_root(root_descriptor)
        self._validate_active_claim()
        os.unlink(temporary_name, dir_fd=root_descriptor)
        self._fsync_root(root_descriptor)
        self._validate_active_claim()

    def _confirm_exact_bundle_under_claim(
        self,
        root_descriptor: int,
        receipt: ExactRestoreReceipt,
        receipt_canonical: bytes,
        finalization: ExactRestoreOperationFinalization,
        finalization_canonical: bytes,
        *,
        finalization_identity: _FileIdentity,
    ) -> None:
        receipt_snapshot = self._load_snapshot(
            root_descriptor,
            receipt.receipt_sha256,
            allow_absent=False,
        )
        if receipt_snapshot is None:  # pragma: no cover - guarded by allow_absent=False
            raise ExactRestoreReceiptArchiveError(
                "finalized exact-restore operation has no receipt"
            )
        self._require_exact_snapshot(receipt_snapshot, receipt, receipt_canonical)
        self._durably_confirm_snapshot(
            root_descriptor,
            receipt.receipt_sha256,
            receipt,
            receipt_canonical,
            expected_identity=receipt_snapshot.identity,
        )
        self._durably_confirm_finalization_snapshot(
            root_descriptor,
            finalization,
            finalization_canonical,
            expected_identity=finalization_identity,
        )
        confirmed = self._load_finalization_snapshot(
            root_descriptor,
            finalization.operation_sha256,
            allow_absent=False,
        )
        if confirmed is None:  # pragma: no cover - guarded by allow_absent=False
            raise ExactRestoreReceiptArchiveError(
                "exact-restore operation finalization disappeared"
            )
        self._require_exact_finalization_snapshot(
            confirmed,
            finalization,
            finalization_canonical,
        )
        self._require_receipt_backing_finalization(root_descriptor, confirmed.finalization)

    def _claim(self) -> _ReceiptArchiveClaimContext:
        return _ReceiptArchiveClaimContext(self)

    def _acquire_claim(self) -> None:
        root_descriptor = -1
        lock_descriptor = -1
        locked = False
        try:
            with self._thread_lock:
                self._require_creation_process()
                if self._claim_active:
                    raise ExactRestoreReceiptArchiveClaimError(
                        "exact-restore receipt claim is already active in this process"
                    )
                try:
                    root_descriptor = self._open_root()
                    lock_descriptor = self._open_claim_file(root_descriptor)
                    try:
                        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except BlockingIOError as error:
                        raise ExactRestoreReceiptArchiveClaimError(
                            "exact-restore receipt archive is claimed by another process"
                        ) from error
                    self._validate_root(root_descriptor)
                    self._validate_named_file(
                        root_descriptor,
                        lock_descriptor,
                        _LOCK_NAME,
                        enforce_size=False,
                    )
                    self._claim_root_descriptor = root_descriptor
                    self._claim_lock_descriptor = lock_descriptor
                    self._claim_pid = os.getpid()
                    self._claim_thread_id = get_ident()
                    self._claim_active = True
                    root_descriptor = -1
                    lock_descriptor = -1
                    locked = False
                except (
                    ExactRestoreReceiptArchiveClaimError,
                    ExactRestoreReceiptArchiveError,
                ):
                    raise
                except OSError as error:
                    raise ExactRestoreReceiptArchiveError(
                        "cannot acquire exact-restore receipt archive claim"
                    ) from error
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

    def _release_claim(self) -> None:
        release_error: ExactRestoreReceiptArchiveClaimError | None = None
        with self._thread_lock:
            self._validate_owner_fields()
            root_descriptor = self._claim_root_descriptor
            lock_descriptor = self._claim_lock_descriptor
            try:
                self._validate_root(root_descriptor)
                self._validate_named_file(
                    root_descriptor,
                    lock_descriptor,
                    _LOCK_NAME,
                    enforce_size=False,
                )
            except ExactRestoreReceiptArchiveError as error:
                release_error = ExactRestoreReceiptArchiveClaimError(
                    "exact-restore receipt claim changed while active"
                )
                release_error.__cause__ = error
            self._claim_active = False
            self._claim_root_descriptor = -1
            self._claim_lock_descriptor = -1
            self._claim_pid = -1
            self._claim_thread_id = -1
        try:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        finally:
            try:
                os.close(lock_descriptor)
            finally:
                os.close(root_descriptor)
        if release_error is not None:
            raise release_error

    def _require_active_claim(self) -> int:
        self._validate_active_claim()
        return self._claim_root_descriptor

    def _validate_active_claim(self) -> None:
        self._require_creation_process()
        self._validate_owner_fields()
        try:
            self._validate_root(self._claim_root_descriptor)
            self._validate_named_file(
                self._claim_root_descriptor,
                self._claim_lock_descriptor,
                _LOCK_NAME,
                enforce_size=False,
            )
        except ExactRestoreReceiptArchiveError as error:
            raise ExactRestoreReceiptArchiveClaimError(
                "exact-restore receipt claim changed while active"
            ) from error

    def _validate_owner_fields(self) -> None:
        if (
            not self._claim_active
            or self._claim_root_descriptor < 0
            or self._claim_lock_descriptor < 0
            or self._claim_pid != os.getpid()
            or self._claim_thread_id != get_ident()
        ):
            raise ExactRestoreReceiptArchiveClaimError(
                "exact-restore receipt operation requires its owner claim"
            )

    def _require_creation_process(self) -> None:
        if self._creation_pid != os.getpid():
            raise ExactRestoreReceiptArchiveClaimError(
                "forked process must construct a new exact-restore receipt archive"
            )

    def _open_root(self) -> int:
        if not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise ExactRestoreReceiptArchiveError(
                "safe receipt archive access requires O_NOFOLLOW and O_DIRECTORY"
            )
        if self._validate_fixed_root:
            if self.root != hardware_safety_root().absolute():
                raise ExactRestoreReceiptArchiveError(
                    "exact-restore receipt archive left the fixed hardware-safety root"
                )
            try:
                return _open_hardware_safety_root()
            except HardwareSafetyRootError as error:
                raise ExactRestoreReceiptArchiveError(
                    "fixed hardware-safety root is unavailable for receipt archive"
                ) from error

        try:
            metadata = self.root.lstat()
        except OSError as error:
            raise ExactRestoreReceiptArchiveError("receipt archive root is unavailable") from error
        self._require_safe_root_metadata(metadata)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = -1
        try:
            descriptor = os.open(self.root, flags)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ExactRestoreReceiptArchiveError("receipt archive root changed while opening")
            self._validate_root(descriptor)
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _validate_root(self, descriptor: int) -> None:
        if self._validate_fixed_root:
            if self.root != hardware_safety_root().absolute():
                raise ExactRestoreReceiptArchiveError(
                    "exact-restore receipt archive left the fixed hardware-safety root"
                )
            try:
                _validate_hardware_safety_root_descriptor(descriptor)
            except HardwareSafetyRootError as error:
                raise ExactRestoreReceiptArchiveError(
                    "receipt archive root changed while in use"
                ) from error
            return
        try:
            opened = os.fstat(descriptor)
            current = self.root.lstat()
        except OSError as error:
            raise ExactRestoreReceiptArchiveError(
                "receipt archive root changed while in use"
            ) from error
        self._require_safe_root_metadata(opened)
        self._require_safe_root_metadata(current)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ExactRestoreReceiptArchiveError("receipt archive root changed while in use")

    def _open_claim_file(self, root_descriptor: int) -> int:
        flags = os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = -1
        try:
            descriptor = os.open(_LOCK_NAME, flags, 0o600, dir_fd=root_descriptor)
            self._validate_named_file(
                root_descriptor,
                descriptor,
                _LOCK_NAME,
                enforce_size=False,
            )
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _load_snapshot(
        self,
        root_descriptor: int,
        digest: str,
        *,
        allow_absent: bool,
    ) -> _ReceiptSnapshot | None:
        self._validate_active_claim()
        name = self._receipt_name(digest)
        descriptor = self._open_existing(root_descriptor, name, allow_absent=allow_absent)
        if descriptor is None:
            self._validate_active_claim()
            return None
        try:
            before = os.fstat(descriptor)
            payload = self._read_bounded(descriptor)
            after = os.fstat(descriptor)
            self._validate_named_file(root_descriptor, descriptor, name)
            if self._metadata_generation(before) != self._metadata_generation(after):
                raise ExactRestoreReceiptArchiveError(
                    "exact-restore receipt changed while it was being read"
                )
            receipt, canonical = self._decode_receipt(payload, expected_digest=digest)
            self._validate_active_claim()
            return _ReceiptSnapshot(
                receipt=receipt,
                canonical=canonical,
                identity=_FileIdentity(device=after.st_dev, inode=after.st_ino),
            )
        except ExactRestoreReceiptArchiveError:
            raise
        except OSError as error:
            raise ExactRestoreReceiptArchiveError("cannot read exact-restore receipt") from error
        finally:
            os.close(descriptor)

    def _load_finalization_snapshot(
        self,
        root_descriptor: int,
        operation_digest: str,
        *,
        allow_absent: bool,
    ) -> _FinalizationSnapshot | None:
        self._validate_active_claim()
        name = self._finalization_name(operation_digest)
        descriptor = self._open_existing(root_descriptor, name, allow_absent=allow_absent)
        if descriptor is None:
            self._validate_active_claim()
            return None
        try:
            before = os.fstat(descriptor)
            payload = self._read_bounded(descriptor)
            after = os.fstat(descriptor)
            self._validate_named_file(root_descriptor, descriptor, name)
            if self._metadata_generation(before) != self._metadata_generation(after):
                raise ExactRestoreReceiptArchiveError(
                    "exact-restore operation finalization changed while it was being read"
                )
            finalization, canonical = self._decode_finalization(
                payload,
                expected_operation_digest=operation_digest,
            )
            self._validate_active_claim()
            return _FinalizationSnapshot(
                finalization=finalization,
                canonical=canonical,
                identity=_FileIdentity(device=after.st_dev, inode=after.st_ino),
            )
        except ExactRestoreReceiptArchiveError:
            raise
        except OSError as error:
            raise ExactRestoreReceiptArchiveError(
                "cannot read exact-restore operation finalization"
            ) from error
        finally:
            os.close(descriptor)

    def _open_existing(
        self,
        root_descriptor: int,
        name: str,
        *,
        allow_absent: bool,
    ) -> int | None:
        try:
            metadata = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            if allow_absent:
                return None
            raise ExactRestoreReceiptArchiveError("exact-restore receipt disappeared") from None
        except OSError as error:
            raise ExactRestoreReceiptArchiveError(
                "exact-restore receipt metadata is unavailable"
            ) from error
        self._require_safe_file_metadata(metadata)

        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = -1
        try:
            descriptor = os.open(name, flags, dir_fd=root_descriptor)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ExactRestoreReceiptArchiveError("exact-restore receipt changed while opening")
            self._validate_named_file(root_descriptor, descriptor, name)
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _write_temporary(
        self,
        root_descriptor: int,
        namespace_prefix: str,
        digest: str,
        payload: bytes,
    ) -> str:
        if len(payload) > self._max_bytes:
            raise ExactRestoreReceiptArchiveError("exact-restore receipt is too large to write")
        descriptor = -1
        temporary_name = ""
        for _ in range(128):
            temporary_name = f".{namespace_prefix}{digest}.{secrets.token_hex(12)}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            try:
                descriptor = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=root_descriptor,
                )
                break
            except FileExistsError:
                continue
        else:  # pragma: no cover - cryptographically improbable without injected faults
            raise ExactRestoreReceiptArchiveError(
                "cannot allocate exact-restore receipt temporary file"
            )

        try:
            os.fchmod(descriptor, 0o600)
            self._validate_named_file(root_descriptor, descriptor, temporary_name)
            pending = memoryview(payload)
            while pending:
                written = os.write(descriptor, pending)
                if written <= 0:
                    raise OSError("short write while persisting exact-restore receipt")
                pending = pending[written:]
            os.fsync(descriptor)
            self._validate_named_file(root_descriptor, descriptor, temporary_name)
            self._validate_active_claim()
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._unlink_temporary(root_descriptor, temporary_name)
            raise
        try:
            os.close(descriptor)
        except OSError:
            self._unlink_temporary(root_descriptor, temporary_name)
            raise
        return temporary_name

    def _durably_confirm_snapshot(
        self,
        root_descriptor: int,
        digest: str,
        receipt: ExactRestoreReceipt,
        canonical: bytes,
        *,
        expected_identity: _FileIdentity,
    ) -> None:
        self._validate_active_claim()
        name = self._receipt_name(digest)
        descriptor = self._open_existing(root_descriptor, name, allow_absent=False)
        if descriptor is None:  # pragma: no cover - guarded by allow_absent=False
            raise ExactRestoreReceiptArchiveError("exact-restore receipt disappeared")
        try:
            metadata = os.fstat(descriptor)
            identity = _FileIdentity(device=metadata.st_dev, inode=metadata.st_ino)
            if identity != expected_identity:
                raise ExactRestoreReceiptArchiveError(
                    "exact-restore receipt was replaced before durable confirmation"
                )
            payload = self._read_bounded(descriptor)
            decoded, observed_canonical = self._decode_receipt(payload, expected_digest=digest)
            if decoded != receipt or observed_canonical != canonical:
                raise ExactRestoreReceiptArchiveError(
                    "conflicting exact-restore receipt already exists"
                )
            os.fsync(descriptor)
            self._validate_named_file(root_descriptor, descriptor, name)
            self._validate_active_claim()
        except ExactRestoreReceiptArchiveError:
            raise
        except OSError as error:
            raise ExactRestoreReceiptArchiveError("cannot fsync exact-restore receipt") from error
        finally:
            os.close(descriptor)

        try:
            self._fsync_root(root_descriptor)
            self._validate_active_claim()
        except ExactRestoreReceiptArchiveError:
            raise
        except OSError as error:
            raise ExactRestoreReceiptArchiveError(
                "cannot fsync exact-restore receipt namespace"
            ) from error
        confirmed = self._load_snapshot(root_descriptor, digest, allow_absent=False)
        if (
            confirmed is None
            or confirmed.identity != expected_identity
            or confirmed.canonical != canonical
            or confirmed.receipt != receipt
        ):
            raise ExactRestoreReceiptArchiveError(
                "exact-restore receipt changed during durable confirmation"
            )

    def _durably_confirm_finalization_snapshot(
        self,
        root_descriptor: int,
        finalization: ExactRestoreOperationFinalization,
        canonical: bytes,
        *,
        expected_identity: _FileIdentity,
    ) -> None:
        self._validate_active_claim()
        operation_digest = finalization.operation_sha256
        name = self._finalization_name(operation_digest)
        descriptor = self._open_existing(root_descriptor, name, allow_absent=False)
        if descriptor is None:  # pragma: no cover - guarded by allow_absent=False
            raise ExactRestoreReceiptArchiveError(
                "exact-restore operation finalization disappeared"
            )
        try:
            metadata = os.fstat(descriptor)
            identity = _FileIdentity(device=metadata.st_dev, inode=metadata.st_ino)
            if identity != expected_identity:
                raise ExactRestoreReceiptArchiveError(
                    "exact-restore operation finalization was replaced before confirmation"
                )
            payload = self._read_bounded(descriptor)
            decoded, observed_canonical = self._decode_finalization(
                payload,
                expected_operation_digest=operation_digest,
            )
            if decoded != finalization or observed_canonical != canonical:
                raise ExactRestoreOperationFinalizationConflictError(
                    "operation is already finalized by a different exact-restore receipt"
                )
            os.fsync(descriptor)
            self._validate_named_file(root_descriptor, descriptor, name)
            self._validate_active_claim()
        except ExactRestoreReceiptArchiveError:
            raise
        except OSError as error:
            raise ExactRestoreReceiptArchiveError(
                "cannot fsync exact-restore operation finalization"
            ) from error
        finally:
            os.close(descriptor)

        try:
            self._fsync_root(root_descriptor)
            self._validate_active_claim()
        except ExactRestoreReceiptArchiveError:
            raise
        except OSError as error:
            raise ExactRestoreReceiptArchiveError(
                "cannot fsync exact-restore finalization namespace"
            ) from error
        confirmed = self._load_finalization_snapshot(
            root_descriptor,
            operation_digest,
            allow_absent=False,
        )
        if (
            confirmed is None
            or confirmed.identity != expected_identity
            or confirmed.canonical != canonical
            or confirmed.finalization != finalization
        ):
            raise ExactRestoreReceiptArchiveError(
                "exact-restore operation finalization changed during confirmation"
            )

    @staticmethod
    def _require_exact_snapshot(
        snapshot: _ReceiptSnapshot,
        receipt: ExactRestoreReceipt,
        canonical: bytes,
    ) -> None:
        if snapshot.receipt != receipt or snapshot.canonical != canonical:
            raise ExactRestoreReceiptArchiveError(
                "conflicting exact-restore receipt already exists"
            )

    @staticmethod
    def _require_exact_finalization_snapshot(
        snapshot: _FinalizationSnapshot,
        finalization: ExactRestoreOperationFinalization,
        canonical: bytes,
    ) -> None:
        if snapshot.finalization != finalization or snapshot.canonical != canonical:
            raise ExactRestoreOperationFinalizationConflictError(
                "operation is already finalized by a different exact-restore receipt"
            )

    def _require_receipt_backing_finalization(
        self,
        root_descriptor: int,
        finalization: ExactRestoreOperationFinalization,
    ) -> _ReceiptSnapshot:
        snapshot = self._load_snapshot(
            root_descriptor,
            finalization.receipt_sha256,
            allow_absent=False,
        )
        if snapshot is None:  # pragma: no cover - guarded by allow_absent=False
            raise ExactRestoreReceiptArchiveError(
                "finalized exact-restore operation has no receipt"
            )
        if (
            self._operation_digest(snapshot.receipt.operation_id) != finalization.operation_sha256
            or snapshot.receipt.receipt_sha256 != finalization.receipt_sha256
            or snapshot.receipt.cycle is not finalization.cycle
        ):
            raise ExactRestoreReceiptArchiveError(
                "operation finalization does not match its archived receipt"
            )
        return snapshot

    def _decode_receipt(
        self,
        payload: bytes,
        *,
        expected_digest: str,
    ) -> tuple[ExactRestoreReceipt, bytes]:
        try:
            text = payload.decode("ascii")
            decoded = json.loads(text, object_pairs_hook=self._unique_object)
            if not isinstance(decoded, dict):
                raise ValueError("receipt root is not an object")
            receipt = ExactRestoreReceipt.model_validate(decoded)
            canonical = self._canonical_receipt(receipt)
        except ExactRestoreReceiptArchiveError:
            raise
        except (
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValidationError,
            ValueError,
        ) as error:
            raise ExactRestoreReceiptArchiveError("cannot decode exact-restore receipt") from error
        if canonical != payload:
            raise ExactRestoreReceiptArchiveError("exact-restore receipt is not canonical JSON")
        if receipt.receipt_sha256 != expected_digest:
            raise ExactRestoreReceiptArchiveError(
                "exact-restore receipt digest does not match its contents"
            )
        return receipt, canonical

    def _decode_finalization(
        self,
        payload: bytes,
        *,
        expected_operation_digest: str,
    ) -> tuple[ExactRestoreOperationFinalization, bytes]:
        try:
            text = payload.decode("ascii")
            decoded = json.loads(text, object_pairs_hook=self._unique_object)
            if not isinstance(decoded, dict):
                raise ValueError("operation finalization root is not an object")
            finalization = ExactRestoreOperationFinalization.model_validate(decoded)
            canonical = self._canonical_finalization(finalization)
        except ExactRestoreReceiptArchiveError:
            raise
        except (
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValidationError,
            ValueError,
        ) as error:
            raise ExactRestoreReceiptArchiveError(
                "cannot decode exact-restore operation finalization"
            ) from error
        if canonical != payload:
            raise ExactRestoreReceiptArchiveError(
                "exact-restore operation finalization is not canonical JSON"
            )
        if finalization.operation_sha256 != expected_operation_digest:
            raise ExactRestoreReceiptArchiveError(
                "operation finalization digest does not match its filename"
            )
        return finalization, canonical

    def _canonical_receipt(self, receipt: ExactRestoreReceipt) -> bytes:
        payload = (
            json.dumps(
                receipt.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )
        if len(payload) > self._max_bytes:
            raise ExactRestoreReceiptArchiveError("exact-restore receipt is too large")
        return payload

    def _canonical_finalization(
        self,
        finalization: ExactRestoreOperationFinalization,
    ) -> bytes:
        payload = (
            json.dumps(
                finalization.model_dump(mode="json"),
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )
        if len(payload) > self._max_bytes:
            raise ExactRestoreReceiptArchiveError(
                "exact-restore operation finalization is too large"
            )
        return payload

    @staticmethod
    def _validated_receipt(receipt: ExactRestoreReceipt) -> ExactRestoreReceipt:
        try:
            return ExactRestoreReceipt.model_validate(receipt)
        except (TypeError, ValidationError, ValueError) as error:
            raise ExactRestoreReceiptArchiveError("exact-restore receipt is invalid") from error

    def _finalization_from_receipt(
        self,
        receipt: ExactRestoreReceipt,
    ) -> ExactRestoreOperationFinalization:
        return ExactRestoreOperationFinalization(
            operation_sha256=self._operation_digest(receipt.operation_id),
            receipt_sha256=receipt.receipt_sha256,
            cycle=receipt.cycle,
        )

    def _validate_named_file(
        self,
        root_descriptor: int,
        descriptor: int,
        name: str,
        *,
        enforce_size: bool = True,
    ) -> None:
        try:
            opened = os.fstat(descriptor)
            current = os.stat(name, dir_fd=root_descriptor, follow_symlinks=False)
        except OSError as error:
            raise ExactRestoreReceiptArchiveError(
                "exact-restore receipt safety file changed while opening"
            ) from error
        self._require_safe_file_metadata(opened, enforce_size=enforce_size)
        self._require_safe_file_metadata(current, enforce_size=enforce_size)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ExactRestoreReceiptArchiveError(
                "exact-restore receipt safety file changed while opening"
            )

    def _read_bounded(self, descriptor: int) -> bytes:
        chunks: list[bytes] = []
        remaining = self._max_bytes + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > self._max_bytes:
            raise ExactRestoreReceiptArchiveError("exact-restore receipt is too large")
        return payload

    def _require_safe_file_metadata(
        self,
        metadata: os.stat_result,
        *,
        enforce_size: bool = True,
    ) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or (enforce_size and metadata.st_size > self._max_bytes)
        ):
            raise ExactRestoreReceiptArchiveError(
                "exact-restore receipt safety file has unsafe metadata"
            )

    @staticmethod
    def _require_safe_root_metadata(metadata: os.stat_result) -> None:
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
        ):
            raise ExactRestoreReceiptArchiveError(
                "receipt archive root must be owner-only directory mode 0700"
            )

    @staticmethod
    def _metadata_generation(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    @staticmethod
    def _validate_digest(receipt_sha256: str) -> str:
        if not isinstance(receipt_sha256, str) or _SHA256_PATTERN.fullmatch(receipt_sha256) is None:
            raise ExactRestoreReceiptArchiveError(
                "exact-restore receipt digest must be 64 lowercase hexadecimal characters"
            )
        return receipt_sha256

    @staticmethod
    def _receipt_name(digest: str) -> str:
        return f"{_RECEIPT_PREFIX}{digest}{_RECEIPT_SUFFIX}"

    @staticmethod
    def _operation_digest(operation_id: str) -> str:
        if (
            not isinstance(operation_id, str)
            or _OPERATION_ID_PATTERN.fullmatch(operation_id) is None
        ):
            raise ExactRestoreReceiptArchiveError("exact-restore operation id is invalid")
        return hashlib.sha256(_OPERATION_DIGEST_DOMAIN + operation_id.encode("ascii")).hexdigest()

    @staticmethod
    def _finalization_name(operation_digest: str) -> str:
        return f"{_FINALIZATION_PREFIX}{operation_digest}{_FINALIZATION_SUFFIX}"

    @staticmethod
    def _fsync_root(root_descriptor: int) -> None:
        os.fsync(root_descriptor)

    @staticmethod
    def _unlink_temporary(root_descriptor: int, temporary_name: str) -> None:
        try:
            os.unlink(temporary_name, dir_fd=root_descriptor)
        except FileNotFoundError:
            pass
        except OSError:
            # The next load rejects nlink != 1, so cleanup failure never promotes ambiguous bytes.
            pass


__all__ = [
    "ExactRestoreOperationFinalization",
    "ExactRestoreOperationFinalizationConflictError",
    "ExactRestoreReceiptArchive",
    "ExactRestoreReceiptArchiveClaimError",
    "ExactRestoreReceiptArchiveError",
]
