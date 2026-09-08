"""Owner-only append journal and content-addressed raw-frame storage."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jebao_flow.operator_capture.model import CaptureDirection, CaptureOrigin, ProtocolSummary
from jebao_flow.protocol.codec import GizwitsCommand

_ROOT_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600
_LOCK_NAME = ".operator-capture.lock"
_GENERATION_NAME = ".operator-capture-generation.json"
_EVENTS_NAME = "events.jsonl"
_MANIFEST_NAME = "manifest.json"
_BLOBS_NAME = "blobs"
_MAX_LEDGER_BYTES = 4096
_MAX_EVENTS_BYTES = 128 * 1024 * 1024
_MAX_BLOB_BYTES = 2 * 1024 * 1024
_SESSION_RE = re.compile(r"capture-(?P<generation>[0-9]{20})-(?P<token>[0-9a-f]{16})\Z")
_FLOW_RE = re.compile(r"flow-[0-9]{6}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_BLOB_RE = re.compile(r"(?P<digest>[0-9a-f]{64})\.blob\Z")
_TEMPORARY_BLOB_RE = re.compile(r"\.blob\.(?P<token>[0-9a-f]{24})\.tmp\Z")
_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")
_MAC_RE = re.compile(r"(?i)(?:[0-9a-f]{2}[:-]){5}[0-9a-f]{2}")
_DEVICE_ID_RE = re.compile(r"(?i)(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])")
_IPV4_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_SENSITIVE_FRAME_COMMANDS = {
    int(GizwitsCommand.DISCOVER_RESPONSE),
    int(GizwitsCommand.PASSCODE_RESPONSE),
    int(GizwitsCommand.LOGIN_REQUEST),
}
_EVENT_KEYS = {
    "schema_version",
    "session_id",
    "generation",
    "seq",
    "utc",
    "monotonic_ns",
    "kind",
    "payload",
    "previous_chain_sha256",
    "chain_sha256",
}
_TERMINAL_STATUSES = {"completed", "stopped", "desynchronized", "failed"}
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class CaptureStoreError(RuntimeError):
    """Capture evidence cannot be handled without weakening its contract."""


@dataclass(frozen=True, slots=True)
class VerifiedCapture:
    session_id: str
    generation: int
    status: str
    valid_event_count: int
    frame_event_count: int
    unique_blob_count: int
    orphan_blob_count: int
    temporary_blob_count: int
    terminal_chain_sha256: str
    journal_sha256: str

    def public_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "generation": self.generation,
            "status": self.status,
            "valid_event_count": self.valid_event_count,
            "frame_event_count": self.frame_event_count,
            "unique_blob_count": self.unique_blob_count,
            "orphan_blob_count": self.orphan_blob_count,
            "temporary_blob_count": self.temporary_blob_count,
            "terminal_chain_sha256": self.terminal_chain_sha256,
            "journal_sha256": self.journal_sha256,
        }


class DurableCaptureSession:
    """One exclusive, append-only capture generation.

    Raw frames are installed by content digest. Repeated wire-identical frames reuse the same blob;
    their individual occurrences remain distinct journal rows. A process crash leaves an unsealed
    generation whose last complete hash-chained line can still be verified.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        origin: CaptureOrigin,
        source_label: str,
    ) -> None:
        _validate_public_label(source_label, "source label")
        self._root_path = Path(root)
        self._root_fd = _open_private_directory(self._root_path)
        self._lock_fd = -1
        self._session_fd = -1
        self._blobs_fd = -1
        self._events_fd = -1
        self._mutex = threading.RLock()
        self._closed = False
        self._sealed = False
        self._event_count = 0
        self._frame_count = 0
        self._blob_digests: set[str] = set()
        self._chain_sha256 = "0" * 64
        self._started_utc = _utc_now()
        self._started_monotonic_ns = time.monotonic_ns()
        try:
            self._lock_fd = _claim_root(self._root_fd)
            self.generation = _advance_generation(self._root_fd)
            token = secrets.token_hex(8)
            self.session_id = f"capture-{self.generation:020d}-{token}"
            os.mkdir(self.session_id, _ROOT_MODE, dir_fd=self._root_fd)
            _fsync(self._root_fd)
            self._session_fd = os.open(
                self.session_id,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                dir_fd=self._root_fd,
            )
            _validate_directory_fd(self._session_fd)
            os.mkdir(_BLOBS_NAME, _ROOT_MODE, dir_fd=self._session_fd)
            _fsync(self._session_fd)
            self._blobs_fd = os.open(
                _BLOBS_NAME,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
                dir_fd=self._session_fd,
            )
            _validate_directory_fd(self._blobs_fd)
            self._events_fd = os.open(
                _EVENTS_NAME,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                _PRIVATE_FILE_MODE,
                dir_fd=self._session_fd,
            )
            _validate_private_file_fd(self._events_fd)
            _fsync(self._session_fd)
            self._append_event(
                "session_started",
                {
                    "origin": origin.value,
                    "source_label": source_label,
                },
            )
        except BaseException:
            self.close_incomplete()
            raise

    def __enter__(self) -> DurableCaptureSession:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        del exc_type, traceback
        if exc is None and not self._sealed:
            self.seal(status="completed")
        elif not self._closed:
            self.close_incomplete()
        return False

    @property
    def sealed(self) -> bool:
        return self._sealed

    def append_frame(
        self,
        wire_bytes: bytes,
        *,
        direction: CaptureDirection,
        flow_id: str,
        summary: ProtocolSummary,
        observed_utc: str | None = None,
        capture_elapsed_ns: int | None = None,
    ) -> int:
        if not isinstance(wire_bytes, bytes) or not wire_bytes:
            raise ValueError("a captured frame must contain bytes")
        if len(wire_bytes) > _MAX_BLOB_BYTES:
            raise ValueError("a captured frame exceeds the private blob size limit")
        if not _FLOW_RE.fullmatch(flow_id):
            raise ValueError("flow id must use the private-address-free flow-NNNNNN form")
        if not isinstance(direction, CaptureDirection):
            raise TypeError("capture direction must be a CaptureDirection")
        if not isinstance(summary, ProtocolSummary):
            raise TypeError("capture summary must be a ProtocolSummary")
        _validate_observation_clock(observed_utc, capture_elapsed_ns)
        with self._mutex:
            self._require_open()
            digest = hashlib.sha256(wire_bytes).hexdigest()
            self._install_or_confirm_blob(digest, wire_bytes)
            self._frame_count += 1
            return self._append_event(
                "frame",
                {
                    "flow_id": flow_id,
                    "direction": direction.value,
                    "observed_utc": observed_utc,
                    "capture_elapsed_ns": capture_elapsed_ns,
                    "wire_length": len(wire_bytes),
                    "wire_sha256": digest,
                    "protocol": summary.public_dict(),
                },
            )

    def append_diagnostic(
        self,
        *,
        kind: str,
        flow_id: str | None,
        reason: str,
        offset: int | None = None,
        length: int | None = None,
        raw_bytes: bytes | None = None,
        observed_utc: str | None = None,
        capture_elapsed_ns: int | None = None,
        direction: CaptureDirection | None = None,
    ) -> int:
        _validate_public_label(kind, "diagnostic kind")
        _validate_public_label(reason, "diagnostic reason")
        if flow_id is not None and not _FLOW_RE.fullmatch(flow_id):
            raise ValueError("diagnostic flow id is invalid")
        if offset is not None and offset < 0:
            raise ValueError("diagnostic offset must be non-negative")
        if length is not None and length < 0:
            raise ValueError("diagnostic length must be non-negative")
        if direction is not None and not isinstance(direction, CaptureDirection):
            raise TypeError("diagnostic direction must be a CaptureDirection")
        _validate_observation_clock(observed_utc, capture_elapsed_ns)
        payload: dict[str, object] = {
            "diagnostic_kind": kind,
            "flow_id": flow_id,
            "reason": reason,
            "offset": offset,
            "length": length,
            "observed_utc": observed_utc,
            "capture_elapsed_ns": capture_elapsed_ns,
            "direction": direction.value if direction is not None else None,
        }
        with self._mutex:
            self._require_open()
            if raw_bytes is not None:
                if not isinstance(raw_bytes, bytes) or not raw_bytes:
                    raise ValueError("diagnostic raw bytes must be non-empty bytes")
                if len(raw_bytes) > _MAX_BLOB_BYTES:
                    raise ValueError("diagnostic raw bytes exceed the private blob size limit")
                digest = hashlib.sha256(raw_bytes).hexdigest()
                self._install_or_confirm_blob(digest, raw_bytes)
                payload.update({"wire_length": len(raw_bytes), "wire_sha256": digest})
            return self._append_event("diagnostic", payload)

    def append_note(self, *, label: str, detail: str | None = None) -> int:
        _validate_public_label(label, "note label")
        if detail is not None:
            if not 1 <= len(detail) <= 256:
                raise ValueError("note detail must contain 1..256 characters")
            if not detail.isascii() or any(ord(character) < 0x20 for character in detail):
                raise ValueError("note detail must use printable ASCII only")
            if _MAC_RE.search(detail) or _DEVICE_ID_RE.search(detail) or _IPV4_RE.search(detail):
                raise ValueError("note detail looks like a private identifier")
        with self._mutex:
            self._require_open()
            return self._append_event("operator_note", {"label": label, "detail": detail})

    def seal(self, *, status: str) -> VerifiedCapture:
        if status not in _TERMINAL_STATUSES:
            raise ValueError("capture terminal status is invalid")
        with self._mutex:
            self._require_open()
            self._append_event("session_terminal", {"status": status})
            _fsync(self._events_fd)
            journal = _read_all_fd(self._events_fd, _MAX_EVENTS_BYTES)
            manifest = {
                "schema_version": 1,
                "session_id": self.session_id,
                "generation": self.generation,
                "status": status,
                "event_count": self._event_count,
                "frame_event_count": self._frame_count,
                "unique_blob_count": len(self._blob_digests),
                "started_utc": self._started_utc,
                "closed_utc": _utc_now(),
                "terminal_chain_sha256": self._chain_sha256,
                "journal_sha256": hashlib.sha256(journal).hexdigest(),
            }
            _install_no_clobber_json(self._session_fd, _MANIFEST_NAME, manifest)
            self._sealed = True
            self._close_descriptors()
        return verify_capture_session(self._root_path, self.session_id)

    def close_incomplete(self) -> None:
        with self._mutex:
            if self._closed:
                return
            if self._events_fd >= 0:
                try:
                    _fsync(self._events_fd)
                except OSError:
                    pass
            self._close_descriptors()

    def _append_event(self, kind: str, payload: Mapping[str, object]) -> int:
        self._event_count += 1
        record: dict[str, object] = {
            "schema_version": 1,
            "session_id": self.session_id,
            "generation": self.generation,
            "seq": self._event_count,
            "utc": _utc_now(),
            "monotonic_ns": time.monotonic_ns(),
            "kind": kind,
            "payload": dict(payload),
            "previous_chain_sha256": self._chain_sha256,
        }
        canonical = _canonical_json(record, newline=False)
        chain = hashlib.sha256(bytes.fromhex(self._chain_sha256) + canonical).hexdigest()
        record["chain_sha256"] = chain
        encoded = _canonical_json(record)
        _write_all(self._events_fd, encoded)
        _fsync(self._events_fd)
        self._chain_sha256 = chain
        return self._event_count

    def _install_or_confirm_blob(self, digest: str, content: bytes) -> None:
        name = f"{digest}.blob"
        temporary = f".blob.{secrets.token_hex(12)}.tmp"
        fd = -1
        linked = False
        try:
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
                _PRIVATE_FILE_MODE,
                dir_fd=self._blobs_fd,
            )
            _write_all(fd, content)
            _fsync(fd)
            os.close(fd)
            fd = -1
            try:
                os.link(
                    temporary,
                    name,
                    src_dir_fd=self._blobs_fd,
                    dst_dir_fd=self._blobs_fd,
                    follow_symlinks=False,
                )
                linked = True
                _fsync(self._blobs_fd)
            except FileExistsError:
                _confirm_blob(self._blobs_fd, name, content, digest)
            os.unlink(temporary, dir_fd=self._blobs_fd)
            _fsync(self._blobs_fd)
            if linked:
                _confirm_blob(self._blobs_fd, name, content, digest)
            self._blob_digests.add(digest)
        except BaseException as error:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temporary, dir_fd=self._blobs_fd)
                _fsync(self._blobs_fd)
            except OSError:
                pass
            if isinstance(error, CaptureStoreError):
                raise
            raise CaptureStoreError("could not durably install a captured frame") from error

    def _require_open(self) -> None:
        if self._closed or self._sealed:
            raise CaptureStoreError("capture session is no longer open")

    def _close_descriptors(self) -> None:
        for attribute in ("_events_fd", "_blobs_fd", "_session_fd", "_lock_fd", "_root_fd"):
            fd = getattr(self, attribute)
            if fd >= 0:
                try:
                    os.close(fd)
                finally:
                    setattr(self, attribute, -1)
        self._closed = True


def verify_capture_session(root: Path | str, session_id: str) -> VerifiedCapture:
    """Verify a sealed generation or the complete prefix of an interrupted generation."""

    match = _SESSION_RE.fullmatch(session_id)
    if match is None:
        raise CaptureStoreError("capture session id is invalid")
    generation = int(match.group("generation"))
    root_fd = _open_private_directory(Path(root))
    session_fd = blobs_fd = events_fd = -1
    try:
        session_fd = os.open(
            session_id,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            dir_fd=root_fd,
        )
        _validate_directory_fd(session_fd)
        blobs_fd = os.open(
            _BLOBS_NAME,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            dir_fd=session_fd,
        )
        _validate_directory_fd(blobs_fd)
        events_fd = os.open(
            _EVENTS_NAME,
            os.O_RDONLY | _NONBLOCK | _NOFOLLOW | _CLOEXEC,
            dir_fd=session_fd,
        )
        _validate_private_file_fd(events_fd)
        journal = _read_all_fd(events_fd, _MAX_EVENTS_BYTES)
        records, complete_file = _verify_event_prefix(journal, session_id, generation)
        referenced = {
            str(record["payload"]["wire_sha256"])
            for record in records
            if isinstance(record.get("payload"), dict) and "wire_sha256" in record["payload"]
        }
        frame_count = sum(record.get("kind") == "frame" for record in records)
        manifest = _read_optional_json(session_fd, _MANIFEST_NAME, _MAX_LEDGER_BYTES)
        blob_names = _list_blob_names(blobs_fd)
        final_blob_names = [name for name in blob_names if _BLOB_RE.fullmatch(name)]
        temporary_blob_names = [name for name in blob_names if _TEMPORARY_BLOB_RE.fullmatch(name)]
        unknown_names = [
            name
            for name in blob_names
            if _BLOB_RE.fullmatch(name) is None and _TEMPORARY_BLOB_RE.fullmatch(name) is None
        ]
        if unknown_names:
            raise CaptureStoreError("capture blob directory contains unexplained entries")
        if manifest is not None and temporary_blob_names:
            raise CaptureStoreError("sealed capture contains an unfinished blob installation")
        link_counts = _validate_blob_directory(
            blobs_fd,
            final_blob_names=final_blob_names,
            temporary_blob_names=temporary_blob_names,
        )
        blob_digests = {name[:-5] for name in final_blob_names}
        for name in final_blob_names:
            digest = name[:-5]
            _confirm_blob_digest(
                blobs_fd,
                name,
                digest,
                allowed_link_count=link_counts[name],
            )
        for digest in referenced:
            if not _SHA256_RE.fullmatch(digest):
                raise CaptureStoreError("capture event has an invalid frame digest")
            if digest not in blob_digests:
                raise CaptureStoreError("referenced capture blob is unavailable")
        orphan_count = len(blob_digests - referenced)

        terminal_chain = str(records[-1]["chain_sha256"]) if records else "0" * 64
        journal_digest = hashlib.sha256(journal).hexdigest()
        if manifest is None:
            status = "incomplete"
        else:
            _verify_manifest(
                manifest,
                session_id=session_id,
                generation=generation,
                event_count=len(records),
                frame_count=frame_count,
                blob_count=len(blob_digests),
                terminal_chain=terminal_chain,
                journal_digest=journal_digest,
                complete_file=complete_file,
                orphan_count=orphan_count,
                records=records,
            )
            status = str(manifest["status"])
        return VerifiedCapture(
            session_id=session_id,
            generation=generation,
            status=status,
            valid_event_count=len(records),
            frame_event_count=frame_count,
            unique_blob_count=len(blob_digests),
            orphan_blob_count=orphan_count,
            temporary_blob_count=len(temporary_blob_names),
            terminal_chain_sha256=terminal_chain,
            journal_sha256=journal_digest,
        )
    except CaptureStoreError:
        raise
    except OSError as error:
        raise CaptureStoreError("could not verify capture session") from error
    finally:
        for fd in (events_fd, blobs_fd, session_fd, root_fd):
            if fd >= 0:
                os.close(fd)


def read_public_capture_events(
    root: Path | str,
    session_id: str,
    *,
    start_seq: int = 1,
    limit: int = 100,
) -> tuple[dict[str, Any], ...]:
    """Return verified metadata rows; raw blob content and private paths never leave the store."""

    if start_seq < 1:
        raise ValueError("start sequence must be positive")
    if not 1 <= limit <= 1000:
        raise ValueError("event limit must be between 1 and 1000")
    match = _SESSION_RE.fullmatch(session_id)
    if match is None:
        raise CaptureStoreError("capture session id is invalid")
    generation = int(match.group("generation"))
    root_fd = _open_private_directory(Path(root))
    session_fd = events_fd = -1
    try:
        session_fd = os.open(
            session_id,
            os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC,
            dir_fd=root_fd,
        )
        _validate_directory_fd(session_fd)
        events_fd = os.open(
            _EVENTS_NAME,
            os.O_RDONLY | _NONBLOCK | _NOFOLLOW | _CLOEXEC,
            dir_fd=session_fd,
        )
        _validate_private_file_fd(events_fd)
        journal = _read_all_fd(events_fd, _MAX_EVENTS_BYTES)
        records, _complete = _verify_event_prefix(journal, session_id, generation)
        selected = [record for record in records if int(record["seq"]) >= start_seq][:limit]
        return tuple(_public_event_record(record) for record in selected)
    except CaptureStoreError:
        raise
    except OSError as error:
        raise CaptureStoreError("could not read public capture events") from error
    finally:
        for fd in (events_fd, session_fd, root_fd):
            if fd >= 0:
                os.close(fd)


def list_capture_sessions(root: Path | str) -> tuple[VerifiedCapture, ...]:
    """List generations in monotonic order without exposing the private root path."""

    root_path = Path(root)
    root_fd = _open_private_directory(root_path)
    try:
        with os.scandir(root_fd) as entries:
            names = sorted(entry.name for entry in entries)
    except OSError as error:
        raise CaptureStoreError("could not enumerate capture generations") from error
    finally:
        os.close(root_fd)
    unexplained = [
        name
        for name in names
        if name not in {_LOCK_NAME, _GENERATION_NAME} and _SESSION_RE.fullmatch(name) is None
    ]
    if unexplained:
        raise CaptureStoreError("capture root contains unexplained entries")
    return tuple(
        verify_capture_session(root_path, name)
        for name in names
        if _SESSION_RE.fullmatch(name) is not None
    )


def _open_private_directory(path: Path) -> int:
    try:
        fd = os.open(path, os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC)
    except OSError as error:
        raise CaptureStoreError("capture root is unavailable") from error
    try:
        _validate_directory_fd(fd)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _public_event_record(record: Mapping[str, Any]) -> dict[str, Any]:
    # Round-trip through JSON so callers cannot mutate verifier-owned nested containers.
    public = json.loads(json.dumps(record))
    payload = public.get("payload")
    if not isinstance(payload, dict):
        return public
    if public.get("kind") == "diagnostic" and "wire_sha256" in payload:
        payload.pop("wire_sha256", None)
        payload["private_blob_retained"] = True
        return public
    if public.get("kind") != "frame" or "wire_sha256" not in payload:
        return public
    protocol = payload.get("protocol")
    if isinstance(protocol, dict) and protocol.get("command") in _SENSITIVE_FRAME_COMMANDS:
        payload.pop("wire_sha256", None)
        payload["private_blob_retained"] = True
    return public


def _validate_directory_fd(fd: int) -> None:
    metadata = os.fstat(fd)
    if not stat.S_ISDIR(metadata.st_mode):
        raise CaptureStoreError("capture path is not a directory")
    if metadata.st_uid != os.geteuid():
        raise CaptureStoreError("capture directory is not owned by this process")
    if stat.S_IMODE(metadata.st_mode) != _ROOT_MODE:
        raise CaptureStoreError("capture directory must have mode 0700")


def _validate_private_file_fd(fd: int, *, allowed_link_counts: tuple[int, ...] = (1,)) -> None:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise CaptureStoreError("capture entry is not a regular file")
    if metadata.st_uid != os.geteuid():
        raise CaptureStoreError("capture entry is not owned by this process")
    if stat.S_IMODE(metadata.st_mode) != _PRIVATE_FILE_MODE:
        raise CaptureStoreError("capture entry must have mode 0600")
    if metadata.st_nlink not in allowed_link_counts:
        raise CaptureStoreError("capture entry has an unexpected link count")


def _claim_root(root_fd: int) -> int:
    fd = -1
    try:
        fd = os.open(
            _LOCK_NAME,
            os.O_RDWR | os.O_CREAT | _NONBLOCK | _NOFOLLOW | _CLOEXEC,
            _PRIVATE_FILE_MODE,
            dir_fd=root_fd,
        )
        _validate_private_file_fd(fd)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _fsync(fd)
        _fsync(root_fd)
        return fd
    except BlockingIOError as error:
        if fd >= 0:
            os.close(fd)
        raise CaptureStoreError("another capture process owns this artifact root") from error
    except BaseException:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        raise


def _advance_generation(root_fd: int) -> int:
    current = _read_optional_json(root_fd, _GENERATION_NAME, _MAX_LEDGER_BYTES)
    if current is None:
        committed = 0
    else:
        if set(current) != {"schema_version", "committed_generation"}:
            raise CaptureStoreError("capture generation ledger has an unexpected shape")
        if current["schema_version"] != 1:
            raise CaptureStoreError("capture generation ledger version is unsupported")
        committed = current["committed_generation"]
        if isinstance(committed, bool) or not isinstance(committed, int) or committed < 1:
            raise CaptureStoreError("capture generation ledger value is invalid")
    if committed >= 2**63 - 1:
        raise CaptureStoreError("capture generation ledger is exhausted")
    generation = committed + 1
    _replace_json(
        root_fd,
        _GENERATION_NAME,
        {"schema_version": 1, "committed_generation": generation},
    )
    return generation


def _install_no_clobber_json(parent_fd: int, name: str, value: Mapping[str, object]) -> None:
    temporary = f".{name}.{secrets.token_hex(12)}.tmp"
    fd = -1
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
            _PRIVATE_FILE_MODE,
            dir_fd=parent_fd,
        )
        _write_all(fd, _canonical_json(value))
        _fsync(fd)
        os.close(fd)
        fd = -1
        os.link(
            temporary,
            name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        _fsync(parent_fd)
        os.unlink(temporary, dir_fd=parent_fd)
        _fsync(parent_fd)
    except FileExistsError as error:
        raise CaptureStoreError(f"capture entry {name!r} already exists") from error
    except BaseException as error:
        if isinstance(error, CaptureStoreError):
            raise
        raise CaptureStoreError(f"could not install capture entry {name!r}") from error
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except OSError:
            pass


def _replace_json(parent_fd: int, name: str, value: Mapping[str, object]) -> None:
    temporary = f".{name}.{secrets.token_hex(12)}.tmp"
    fd = -1
    try:
        fd = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC,
            _PRIVATE_FILE_MODE,
            dir_fd=parent_fd,
        )
        _write_all(fd, _canonical_json(value))
        _fsync(fd)
        os.close(fd)
        fd = -1
        os.replace(temporary, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
        _fsync(parent_fd)
    except BaseException as error:
        raise CaptureStoreError(f"could not replace capture entry {name!r}") from error
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temporary, dir_fd=parent_fd)
        except OSError:
            pass


def _read_optional_json(parent_fd: int, name: str, maximum: int) -> dict[str, Any] | None:
    try:
        fd = os.open(
            name,
            os.O_RDONLY | _NONBLOCK | _NOFOLLOW | _CLOEXEC,
            dir_fd=parent_fd,
        )
    except FileNotFoundError:
        return None
    try:
        _validate_private_file_fd(fd)
        content = _read_all_fd(fd, maximum)
        decoded = json.loads(content)
        if not isinstance(decoded, dict):
            raise CaptureStoreError(f"capture entry {name!r} is not a JSON object")
        if _canonical_json(decoded) != content:
            raise CaptureStoreError(f"capture entry {name!r} is not canonical JSON")
        return decoded
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CaptureStoreError(f"capture entry {name!r} is not valid JSON") from error
    finally:
        os.close(fd)


def _confirm_blob(parent_fd: int, name: str, content: bytes, digest: str) -> None:
    try:
        fd = os.open(name, os.O_RDONLY | _NONBLOCK | _NOFOLLOW | _CLOEXEC, dir_fd=parent_fd)
    except OSError as error:
        raise CaptureStoreError("captured frame blob is unavailable") from error
    try:
        _validate_private_file_fd(fd)
        metadata = os.fstat(fd)
        if metadata.st_size != len(content):
            raise CaptureStoreError("captured frame digest names different-sized content")
        installed = _read_all_fd(fd, len(content) + 1)
        if installed != content or hashlib.sha256(installed).hexdigest() != digest:
            raise CaptureStoreError("captured frame digest collision or corruption")
    finally:
        os.close(fd)


def _confirm_blob_digest(
    parent_fd: int,
    name: str,
    digest: str,
    *,
    allowed_link_count: int = 1,
) -> None:
    try:
        fd = os.open(name, os.O_RDONLY | _NONBLOCK | _NOFOLLOW | _CLOEXEC, dir_fd=parent_fd)
    except OSError as error:
        raise CaptureStoreError("referenced capture blob is unavailable") from error
    try:
        _validate_private_file_fd(fd, allowed_link_counts=(allowed_link_count,))
        content = _read_all_fd(fd, _MAX_BLOB_BYTES)
        if hashlib.sha256(content).hexdigest() != digest:
            raise CaptureStoreError("referenced capture blob digest does not match")
    finally:
        os.close(fd)


def _list_blob_names(blobs_fd: int) -> list[str]:
    # ``/dev/fd`` is not portable enough for this contract. The descriptor was opened from an
    # owner-only root, and scandir accepts its integer descriptor on supported CPython platforms.
    try:
        with os.scandir(blobs_fd) as entries:
            return sorted(entry.name for entry in entries)
    except OSError as error:
        raise CaptureStoreError("could not enumerate captured frame blobs") from error


def _validate_blob_directory(
    blobs_fd: int,
    *,
    final_blob_names: list[str],
    temporary_blob_names: list[str],
) -> dict[str, int]:
    """Validate crash residues without deleting or treating them as sealed evidence."""

    metadata: dict[str, os.stat_result] = {}
    for name in (*final_blob_names, *temporary_blob_names):
        try:
            item = os.stat(name, dir_fd=blobs_fd, follow_symlinks=False)
        except OSError as error:
            raise CaptureStoreError("capture blob changed during verification") from error
        if not stat.S_ISREG(item.st_mode):
            raise CaptureStoreError("capture blob entry is not a regular file")
        if item.st_uid != os.geteuid() or stat.S_IMODE(item.st_mode) != _PRIVATE_FILE_MODE:
            raise CaptureStoreError("capture blob entry has unsafe ownership or mode")
        if item.st_size > _MAX_BLOB_BYTES:
            raise CaptureStoreError("capture blob entry exceeds its size limit")
        metadata[name] = item

    final_by_inode = {
        (item.st_dev, item.st_ino): name
        for name, item in metadata.items()
        if name in final_blob_names
    }
    paired_temporary_inodes: set[tuple[int, int]] = set()
    for name in temporary_blob_names:
        item = metadata[name]
        inode = (item.st_dev, item.st_ino)
        if item.st_nlink == 1:
            continue
        if item.st_nlink != 2 or inode not in final_by_inode or inode in paired_temporary_inodes:
            raise CaptureStoreError("temporary capture blob has unexplained links")
        paired_temporary_inodes.add(inode)

    link_counts: dict[str, int] = {}
    for name in final_blob_names:
        item = metadata[name]
        inode = (item.st_dev, item.st_ino)
        if item.st_nlink == 1:
            link_counts[name] = 1
            continue
        if item.st_nlink != 2 or inode not in paired_temporary_inodes:
            raise CaptureStoreError("captured blob has unexplained extra links")
        link_counts[name] = 2
    return link_counts


def _verify_event_prefix(
    content: bytes,
    session_id: str,
    generation: int,
) -> tuple[list[dict[str, Any]], bool]:
    complete_file = not content or content.endswith(b"\n")
    lines = content.splitlines(keepends=True)
    if lines and not lines[-1].endswith(b"\n"):
        lines.pop()
    records: list[dict[str, Any]] = []
    previous = "0" * 64
    previous_monotonic_ns: int | None = None
    terminal_seen = False
    for expected_seq, line in enumerate(lines, start=1):
        try:
            record = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CaptureStoreError("capture journal has a malformed complete line") from error
        if not isinstance(record, dict) or _canonical_json(record) != line:
            raise CaptureStoreError("capture journal line is not canonical JSON")
        if set(record) != _EVENT_KEYS:
            raise CaptureStoreError("capture journal event has an unexpected shape")
        chain = record.pop("chain_sha256", None)
        if (
            record.get("schema_version") != 1
            or record.get("session_id") != session_id
            or record.get("generation") != generation
            or record.get("seq") != expected_seq
            or record.get("previous_chain_sha256") != previous
            or not isinstance(chain, str)
            or not _SHA256_RE.fullmatch(chain)
        ):
            raise CaptureStoreError("capture journal sequence or identity is invalid")
        calculated = hashlib.sha256(
            bytes.fromhex(previous) + _canonical_json(record, newline=False)
        ).hexdigest()
        if calculated != chain:
            raise CaptureStoreError("capture journal hash chain does not match")
        monotonic_ns = record.get("monotonic_ns")
        if (
            isinstance(monotonic_ns, bool)
            or not isinstance(monotonic_ns, int)
            or monotonic_ns < 0
            or (previous_monotonic_ns is not None and monotonic_ns < previous_monotonic_ns)
        ):
            raise CaptureStoreError("capture journal monotonic clock is invalid")
        utc = record.get("utc")
        if not isinstance(utc, str):
            raise CaptureStoreError("capture journal UTC clock is invalid")
        _parse_canonical_utc(utc, "capture journal UTC clock")
        kind = record.get("kind")
        if expected_seq == 1 and kind != "session_started":
            raise CaptureStoreError("capture journal does not start with session metadata")
        if expected_seq > 1 and kind == "session_started":
            raise CaptureStoreError("capture journal repeats its session metadata")
        if terminal_seen:
            raise CaptureStoreError("capture journal contains an event after its terminal record")
        _validate_event_payload(kind, record.get("payload"))
        terminal_seen = kind == "session_terminal"
        record["chain_sha256"] = chain
        records.append(record)
        previous = chain
        previous_monotonic_ns = monotonic_ns
    return records, complete_file


def _verify_manifest(
    manifest: Mapping[str, Any],
    *,
    session_id: str,
    generation: int,
    event_count: int,
    frame_count: int,
    blob_count: int,
    terminal_chain: str,
    journal_digest: str,
    complete_file: bool,
    orphan_count: int,
    records: list[dict[str, Any]],
) -> None:
    expected_keys = {
        "schema_version",
        "session_id",
        "generation",
        "status",
        "event_count",
        "frame_event_count",
        "unique_blob_count",
        "started_utc",
        "closed_utc",
        "terminal_chain_sha256",
        "journal_sha256",
    }
    if set(manifest) != expected_keys:
        raise CaptureStoreError("capture manifest has an unexpected shape")
    numeric_fields = (
        manifest.get("generation"),
        manifest.get("event_count"),
        manifest.get("frame_event_count"),
        manifest.get("unique_blob_count"),
    )
    if any(isinstance(value, bool) or not isinstance(value, int) for value in numeric_fields):
        raise CaptureStoreError("capture manifest numeric fields are invalid")
    if not records or records[-1].get("kind") != "session_terminal":
        raise CaptureStoreError("sealed capture has no terminal journal record")
    terminal_payload = records[-1].get("payload")
    assert isinstance(terminal_payload, dict)
    if terminal_payload.get("status") != manifest.get("status"):
        raise CaptureStoreError("capture manifest status differs from its terminal record")
    started_utc = manifest.get("started_utc")
    closed_utc = manifest.get("closed_utc")
    if not isinstance(started_utc, str) or not isinstance(closed_utc, str):
        raise CaptureStoreError("capture manifest UTC span is invalid")
    started = _parse_canonical_utc(started_utc, "capture manifest start UTC")
    closed = _parse_canonical_utc(closed_utc, "capture manifest close UTC")
    first_event = _parse_canonical_utc(str(records[0]["utc"]), "capture first event UTC")
    last_event = _parse_canonical_utc(str(records[-1]["utc"]), "capture last event UTC")
    if started > first_event or first_event > last_event or last_event > closed:
        raise CaptureStoreError("capture manifest UTC span does not contain its journal")
    if (
        not complete_file
        or orphan_count
        or manifest["schema_version"] != 1
        or manifest["session_id"] != session_id
        or manifest["generation"] != generation
        or manifest["event_count"] != event_count
        or manifest["frame_event_count"] != frame_count
        or manifest["unique_blob_count"] != blob_count
        or manifest["terminal_chain_sha256"] != terminal_chain
        or manifest["journal_sha256"] != journal_digest
        or manifest["status"] not in _TERMINAL_STATUSES
    ):
        raise CaptureStoreError("capture manifest does not match durable evidence")


def _validate_event_payload(kind: object, payload: object) -> None:
    if not isinstance(kind, str) or kind not in {
        "session_started",
        "frame",
        "diagnostic",
        "operator_note",
        "session_terminal",
    }:
        raise CaptureStoreError("capture journal event kind is invalid")
    if not isinstance(payload, dict):
        raise CaptureStoreError("capture journal event payload is not an object")

    if kind == "session_started":
        if set(payload) != {"origin", "source_label"}:
            raise CaptureStoreError("capture session metadata has an unexpected shape")
        if payload["origin"] not in {origin.value for origin in CaptureOrigin}:
            raise CaptureStoreError("capture session origin is invalid")
        try:
            _validate_public_label(payload["source_label"], "source label")
        except (TypeError, ValueError) as error:
            raise CaptureStoreError("capture session source label is invalid") from error
        return

    if kind == "session_terminal":
        if set(payload) != {"status"} or payload["status"] not in _TERMINAL_STATUSES:
            raise CaptureStoreError("capture terminal payload is invalid")
        return

    if kind == "operator_note":
        if set(payload) != {"label", "detail"}:
            raise CaptureStoreError("capture note payload has an unexpected shape")
        try:
            _validate_public_label(payload["label"], "note label")
        except (TypeError, ValueError) as error:
            raise CaptureStoreError("capture note label is invalid") from error
        detail = payload["detail"]
        if detail is not None and (
            not isinstance(detail, str)
            or not 1 <= len(detail) <= 256
            or not detail.isascii()
            or any(ord(character) < 0x20 for character in detail)
            or _MAC_RE.search(detail)
            or _DEVICE_ID_RE.search(detail)
            or _IPV4_RE.search(detail)
        ):
            raise CaptureStoreError("capture note detail is invalid")
        return

    common_keys = {
        "flow_id",
        "direction",
        "observed_utc",
        "capture_elapsed_ns",
        "wire_length",
        "wire_sha256",
    }
    if kind == "frame":
        if set(payload) != common_keys | {"protocol"}:
            raise CaptureStoreError("capture frame payload has an unexpected shape")
        protocol = payload["protocol"]
        if not isinstance(protocol, dict) or set(protocol) != {
            "disposition",
            "command",
            "command_name",
            "action",
            "action_name",
            "payload_length",
            "semantic",
            "unknown_regions",
        }:
            raise CaptureStoreError("capture protocol summary has an unexpected shape")
        if protocol["disposition"] not in {"known", "unknown", "malformed"}:
            raise CaptureStoreError("capture protocol disposition is invalid")
        if not _is_optional_int(protocol["command"], minimum=0, maximum=0xFFFF):
            raise CaptureStoreError("capture protocol command is invalid")
        if not _is_optional_int(protocol["action"], minimum=0, maximum=0xFF):
            raise CaptureStoreError("capture protocol action is invalid")
        for value in (protocol["command_name"], protocol["action_name"]):
            if value is not None and (not isinstance(value, str) or not value.isascii()):
                raise CaptureStoreError("capture protocol name is invalid")
        if not _is_int(protocol["payload_length"], minimum=0):
            raise CaptureStoreError("capture protocol payload length is invalid")
        if not isinstance(protocol["semantic"], dict) or not isinstance(
            protocol["unknown_regions"], list
        ):
            raise CaptureStoreError("capture protocol diagnostic containers are invalid")
        _validate_public_json_ascii(protocol)
    else:
        diagnostic_keys = {
            "diagnostic_kind",
            "flow_id",
            "reason",
            "offset",
            "length",
            "observed_utc",
            "capture_elapsed_ns",
            "direction",
        }
        payload_keys = frozenset(payload)
        if payload_keys not in {
            frozenset(diagnostic_keys),
            frozenset(common_keys | diagnostic_keys),
        }:
            raise CaptureStoreError("capture diagnostic payload has an unexpected shape")
        for key in ("diagnostic_kind", "reason"):
            try:
                _validate_public_label(payload[key], key.replace("_", " "))
            except (TypeError, ValueError) as error:
                raise CaptureStoreError("capture diagnostic label is invalid") from error
        for key in ("offset", "length"):
            if not _is_optional_int(payload[key], minimum=0):
                raise CaptureStoreError("capture diagnostic range is invalid")

    flow_id = payload["flow_id"]
    if kind == "frame":
        if not isinstance(flow_id, str) or _FLOW_RE.fullmatch(flow_id) is None:
            raise CaptureStoreError("capture event flow id is invalid")
        if payload["direction"] not in {direction.value for direction in CaptureDirection}:
            raise CaptureStoreError("capture event direction is invalid")
    else:
        if flow_id is not None and (
            not isinstance(flow_id, str) or _FLOW_RE.fullmatch(flow_id) is None
        ):
            raise CaptureStoreError("capture diagnostic flow id is invalid")
        if payload["direction"] is not None and payload["direction"] not in {
            direction.value for direction in CaptureDirection
        }:
            raise CaptureStoreError("capture diagnostic direction is invalid")
    if "wire_length" in payload:
        if not _is_int(payload["wire_length"], minimum=1, maximum=_MAX_BLOB_BYTES):
            raise CaptureStoreError("capture event wire length is invalid")
        digest = payload["wire_sha256"]
        if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
            raise CaptureStoreError("capture event wire digest is invalid")
    try:
        _validate_observation_clock(payload["observed_utc"], payload["capture_elapsed_ns"])
    except (TypeError, ValueError) as error:
        raise CaptureStoreError("capture event observation clock is invalid") from error


def _validate_public_json_ascii(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key.isascii():
                raise CaptureStoreError("capture public metadata key is not ASCII")
            _validate_public_json_ascii(item)
    elif isinstance(value, list):
        for item in value:
            _validate_public_json_ascii(item)
    elif isinstance(value, str) and not value.isascii():
        raise CaptureStoreError("capture public metadata value is not ASCII")


def _is_int(value: object, *, minimum: int, maximum: int | None = None) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, int)
        and value >= minimum
        and (maximum is None or value <= maximum)
    )


def _is_optional_int(value: object, *, minimum: int, maximum: int | None = None) -> bool:
    return value is None or _is_int(value, minimum=minimum, maximum=maximum)


def _parse_canonical_utc(value: str, description: str) -> datetime:
    if _UTC_RE.fullmatch(value) is None:
        raise CaptureStoreError(f"{description} is not canonical")
    try:
        return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise CaptureStoreError(f"{description} is invalid") from error


def _read_all_fd(fd: int, maximum: int) -> bytes:
    metadata = os.fstat(fd)
    if metadata.st_size > maximum:
        raise CaptureStoreError("capture entry exceeds its size limit")
    os.lseek(fd, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining:
        chunk = os.read(fd, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    if len(content) > maximum:
        raise CaptureStoreError("capture entry exceeds its size limit")
    return content


def _canonical_json(value: object, *, newline: bool = True) -> bytes:
    encoded = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return encoded + (b"\n" if newline else b"")


def _write_all(fd: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise CaptureStoreError("capture write made no progress")
        view = view[written:]


def _fsync(fd: int) -> None:
    os.fsync(fd)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate_observation_clock(
    observed_utc: str | None,
    capture_elapsed_ns: int | None,
) -> None:
    if observed_utc is not None:
        if not isinstance(observed_utc, str) or _UTC_RE.fullmatch(observed_utc) is None:
            raise ValueError("observed UTC must use canonical microsecond UTC form")
        try:
            datetime.fromisoformat(observed_utc.removesuffix("Z") + "+00:00")
        except ValueError as error:
            raise ValueError("observed UTC is not a valid timestamp") from error
    if capture_elapsed_ns is not None:
        if (
            isinstance(capture_elapsed_ns, bool)
            or not isinstance(capture_elapsed_ns, int)
            or capture_elapsed_ns < 0
        ):
            raise ValueError("capture elapsed time must be a non-negative integer")


def _validate_public_label(value: str, description: str) -> None:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", value):
        raise ValueError(f"{description} must be a lowercase public token")


__all__ = [
    "CaptureStoreError",
    "DurableCaptureSession",
    "VerifiedCapture",
    "list_capture_sessions",
    "read_public_capture_events",
    "verify_capture_session",
]
