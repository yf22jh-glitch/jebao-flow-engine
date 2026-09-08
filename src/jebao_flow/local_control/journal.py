"""Durable, separate write-ahead state for local-control transactions.

The store never opens or advances the exact-restore journal. Payloads and baselines are retained
as private content-addressed blobs; the request-id record is replaced atomically after every
state transition. A persisted ``*_attempting`` phase means the corresponding frame may have
crossed the wire and therefore can never be retried automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from jebao_flow.hardware_safety import hardware_safety_root, validate_hardware_safety_root
from jebao_flow.local_control.models import LocalDeviceBinding


class LocalControlJournalError(RuntimeError):
    """The private local-control write-ahead state is unsafe or inconsistent."""


class ActionPhase(StrEnum):
    PREPARED = "prepared"
    NO_CHANGE = "no_change"
    FORWARD_ATTEMPTING = "forward_attempting"
    FORWARD_ACKNOWLEDGED = "forward_acknowledged"
    VERIFIED = "verified"
    UNCERTAIN = "uncertain"
    ROLLBACK_ATTEMPTING = "rollback_attempting"
    ROLLED_BACK = "rolled_back"
    ROLLBACK_UNCERTAIN = "rollback_uncertain"


class TransactionPhase(StrEnum):
    PREPARED = "prepared"
    EXECUTING = "executing"
    VERIFIED = "verified"
    RECOVERY_REQUIRED = "recovery_required"
    ROLLED_BACK = "rolled_back"


class JournalAction(BaseModel):
    """One physical frame, its exact baseline, and its durable recovery policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,95}$")
    target: LocalDeviceBinding
    function: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    forward_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    rollback_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_status_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_readback_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_readback_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_kind: str = Field(pattern=r"^(scalar_fields|schedule_image)$")
    restore_on_recovery: StrictBool = True
    phase: ActionPhase = ActionPhase.PREPARED
    acknowledgement_sha256: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    diagnostic: str | None = Field(
        default=None,
        pattern=r"^[a-z][a-z0-9_.-]{0,95}$",
    )

    @model_validator(mode="after")
    def validate_recovery_policy(self) -> Self:
        if not self.restore_on_recovery and self.function != "power":
            raise ValueError("only power actions may retain a safer forward state")
        return self


class LocalControlJournalRecord(BaseModel):
    """Current durable state for one idempotent request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    phase: TransactionPhase = TransactionPhase.PREPARED
    revision: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime
    actions: tuple[JournalAction, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_consistency(self) -> Self:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("journal timestamps must be timezone-aware")
        action_ids = [action.action_id for action in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("journal action ids must be unique")
        if self.phase is TransactionPhase.VERIFIED and any(
            action.phase not in {ActionPhase.VERIFIED, ActionPhase.NO_CHANGE}
            for action in self.actions
        ):
            raise ValueError("verified transaction contains an unverified action")
        if self.phase is TransactionPhase.ROLLED_BACK and any(
            action.phase not in {ActionPhase.ROLLED_BACK, ActionPhase.NO_CHANGE}
            for action in self.actions
        ):
            raise ValueError("rolled-back transaction contains an unrecovered action")
        return self

    def evolve(
        self,
        *,
        phase: TransactionPhase | None = None,
        actions: tuple[JournalAction, ...] | None = None,
        now: datetime | None = None,
    ) -> LocalControlJournalRecord:
        return type(self).model_validate(
            {
                **self.model_dump(),
                "phase": self.phase if phase is None else phase,
                "actions": self.actions if actions is None else actions,
                "revision": self.revision + 1,
                "updated_at": now or datetime.now(UTC),
            }
        )


class LocalControlJournalStore:
    """Owner-only atomic record and immutable blob store at one fixed deployment path."""

    def __init__(self) -> None:
        # Production callers cannot select or override this root. Otherwise one uncertain
        # FORWARD_ATTEMPTING record could be hidden simply by changing process environment.
        validate_hardware_safety_root()
        self._initialize(hardware_safety_root() / "local-control")

    def _initialize(self, root: Path) -> None:
        # Do not resolve the production child: the direct-child spelling is part of the fixed
        # safety namespace, and _require_private_directory rejects a substituted symlink.
        self.root = root.absolute()
        self.operations = self.root / "operations"
        self.blobs = self.root / "blobs"
        self._ensure_layout()

    @classmethod
    def for_test(cls, root: Path) -> LocalControlJournalStore:
        store = cls.__new__(cls)
        store._initialize(Path(root).resolve())
        return store

    def install_blob(self, payload: bytes | bytearray | memoryview) -> str:
        if not isinstance(payload, (bytes, bytearray, memoryview)):
            raise TypeError("journal blob must be bytes-like")
        exact = bytes(payload)
        digest = hashlib.sha256(exact).hexdigest()
        target = self.blobs / digest
        if target.exists():
            if self.load_blob(digest) != exact:
                raise LocalControlJournalError("content-addressed journal blob mismatch")
            return digest
        temporary = self.blobs / f".blob-{secrets.token_hex(16)}"
        _write_new_private_file(temporary, exact)
        try:
            try:
                os.link(temporary, target, follow_symlinks=False)
            except FileExistsError:
                pass
            _fsync_directory(self.blobs)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            _fsync_directory(self.blobs)
        if self.load_blob(digest) != exact:
            raise LocalControlJournalError("journal blob install did not reload exactly")
        return digest

    def load_blob(self, digest: str) -> bytes:
        _require_sha256(digest)
        payload = _read_private_file(self.blobs / digest)
        if hashlib.sha256(payload).hexdigest() != digest:
            raise LocalControlJournalError("journal blob digest mismatch")
        return payload

    def create(self, record: LocalControlJournalRecord) -> None:
        if record.revision != 0 or record.phase is not TransactionPhase.PREPARED:
            raise LocalControlJournalError("new journal record must be revision-zero PREPARED")
        self._require_referenced_blobs(record)
        target = self._record_path(record.request_id)
        try:
            _write_new_private_file(target, _canonical_record(record))
        except FileExistsError as error:
            raise LocalControlJournalError("journal request id already exists") from error
        _fsync_directory(self.operations)
        if self.load(record.request_id) != record:
            raise LocalControlJournalError("new journal record did not reload exactly")

    def load(self, request_id: str) -> LocalControlJournalRecord | None:
        target = self._record_path(request_id)
        try:
            payload = _read_private_file(target)
        except FileNotFoundError:
            return None
        try:
            return LocalControlJournalRecord.model_validate_json(payload)
        except ValueError as error:
            raise LocalControlJournalError("journal record is invalid") from error

    def save(
        self,
        expected: LocalControlJournalRecord,
        replacement: LocalControlJournalRecord,
    ) -> None:
        if replacement.request_id != expected.request_id:
            raise LocalControlJournalError("journal replacement changed request identity")
        if replacement.intent_sha256 != expected.intent_sha256:
            raise LocalControlJournalError("journal replacement changed intent identity")
        if replacement.revision != expected.revision + 1:
            raise LocalControlJournalError("journal replacement revision is not consecutive")
        current = self.load(expected.request_id)
        if current != expected:
            raise LocalControlJournalError("journal changed before replacement")
        self._require_referenced_blobs(replacement)
        target = self._record_path(expected.request_id)
        temporary = self.operations / f".{target.name}.new-{secrets.token_hex(16)}"
        _write_new_private_file(temporary, _canonical_record(replacement))
        try:
            os.replace(temporary, target)
            _fsync_directory(self.operations)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
        if self.load(expected.request_id) != replacement:
            raise LocalControlJournalError("journal replacement did not reload exactly")

    def record_digest(self, record: LocalControlJournalRecord) -> str:
        return hashlib.sha256(_canonical_record(record)).hexdigest()

    def unfinished_records(self) -> tuple[LocalControlJournalRecord, ...]:
        """Return every non-terminal transaction after validating the operation directory."""

        records: list[LocalControlJournalRecord] = []
        for entry in sorted(self.operations.iterdir(), key=lambda item: item.name):
            if not entry.name.endswith(".json") or entry.name.startswith("."):
                raise LocalControlJournalError("journal operation directory contains residue")
            request_id = entry.name.removesuffix(".json")
            record = self.load(request_id)
            if record is None:
                raise LocalControlJournalError("journal operation disappeared during inventory")
            if record.phase not in {TransactionPhase.VERIFIED, TransactionPhase.ROLLED_BACK}:
                records.append(record)
        return tuple(records)

    def _record_path(self, request_id: str) -> Path:
        if (
            not 8 <= len(request_id) <= 128
            or any(
                character
                not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-"
                for character in request_id
            )
        ):
            raise ValueError("journal request id is invalid")
        return self.operations / f"{request_id}.json"

    def _ensure_layout(self) -> None:
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        _require_private_directory(self.root)
        for directory in (self.operations, self.blobs):
            directory.mkdir(mode=0o700, exist_ok=True)
            _require_private_directory(directory)
        _fsync_directory(self.root)

    def _require_referenced_blobs(self, record: LocalControlJournalRecord) -> None:
        for action in record.actions:
            for digest in (
                action.forward_payload_sha256,
                action.rollback_payload_sha256,
                action.baseline_status_sha256,
                action.expected_readback_sha256,
                action.baseline_readback_sha256,
            ):
                self.load_blob(digest)
            if action.acknowledgement_sha256 is not None:
                self.load_blob(action.acknowledgement_sha256)


def _canonical_record(record: LocalControlJournalRecord) -> bytes:
    payload = record.model_dump(mode="json")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "ascii"
    ) + b"\n"


def _write_new_private_file(path: Path, payload: bytes) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        raise LocalControlJournalError("O_NOFOLLOW is required for journal files")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(descriptor, 0o600)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short journal write")
            view = view[written:]
        os.fsync(descriptor)
        _require_private_file_metadata(os.fstat(descriptor))
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_private_file(path: Path) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        raise LocalControlJournalError("O_NOFOLLOW is required for journal files")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        _require_private_file_metadata(os.fstat(descriptor))
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
        named = path.stat(follow_symlinks=False)
        opened = os.fstat(descriptor)
        _require_private_file_metadata(named)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise LocalControlJournalError("journal file changed while reading")
        return bytes(payload)
    finally:
        os.close(descriptor)


def _require_private_file_metadata(metadata: os.stat_result) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_nlink != 1
    ):
        raise LocalControlJournalError("journal file has unsafe metadata")


def _require_private_directory(path: Path) -> None:
    metadata = path.lstat()
    if (
        path.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise LocalControlJournalError("journal directory must be owner-only mode 0700")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _require_sha256(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("journal digest must be lowercase SHA-256")


__all__ = [
    "ActionPhase",
    "JournalAction",
    "LocalControlJournalError",
    "LocalControlJournalRecord",
    "LocalControlJournalStore",
    "TransactionPhase",
]
