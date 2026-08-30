"""Generic, durable JSON journal storage for an exact-restore workflow.

The store knows nothing about pumps, restore phases, or controller models.  Callers provide a
JSON-compatible mapping and retain responsibility for validating its domain schema.  This module
only establishes the filesystem boundary required before a physical write: one exclusive owner,
canonical bytes, atomic successors, and fail-closed metadata checks.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import secrets
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock, get_ident
from types import TracebackType

from jebao_flow.hardware_safety import (
    HardwareSafetyRootError,
    _open_hardware_safety_root,
    _validate_hardware_safety_root_descriptor,
    exact_restore_journal_path,
    hardware_safety_root,
)


class ExactRestoreJournalError(RuntimeError):
    """The exact-restore journal could not be handled without weakening safety."""


class ExactRestoreJournalClaimError(ExactRestoreJournalError):
    """Another owner or an existing first record prevents this journal claim."""


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int


@dataclass(frozen=True)
class _Snapshot:
    record: dict[str, object] | None
    canonical: bytes | None
    identity: _FileIdentity | None


class _JournalClaimContext:
    """Release a journal claim only from the process and thread that acquired it."""

    def __init__(self, store: ExactRestoreJournalStore) -> None:
        self._store = store
        self._active = False
        self._owner_pid = -1
        self._owner_thread_id = -1

    def __enter__(self) -> None:
        if self._active:
            raise ExactRestoreJournalClaimError("exact-restore claim context is already active")
        self._store._acquire_claim()
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
            raise ExactRestoreJournalClaimError("exact-restore claim context is not active")
        if self._owner_pid != os.getpid() or self._owner_thread_id != get_ident():
            raise ExactRestoreJournalClaimError(
                "exact-restore claim must be released by its owner process and thread"
            )
        try:
            self._store._release_claim()
        finally:
            self._active = False
        return False


class ExactRestoreJournalStore:
    """Persist one generic exact-restore record behind an exclusive claim.

    ``create``, ``save``, and ``clear`` require ``claim``.  The first record is installed with an
    exclusive hard-link boundary; later records use a same-directory atomic replace.  A mutation
    can have taken effect even when the final directory ``fsync`` reports an error.  In that case
    the caller may use ``reload_and_confirm_successor`` while retaining the claim.  That method
    fsyncs and reloads the candidate before accepting it as the claim's exact successor.
    """

    _DEFAULT_MAX_BYTES = 1024 * 1024

    def __init__(
        self,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> None:
        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        selected = exact_restore_journal_path()
        self._validate_fixed_path = True
        self._initialize(selected, max_bytes=max_bytes)

    @classmethod
    def _for_test(
        cls,
        path: str | Path,
        *,
        max_bytes: int = _DEFAULT_MAX_BYTES,
    ) -> ExactRestoreJournalStore:
        """Build an isolated test store; production callers use the fixed safety volume."""

        if type(max_bytes) is not int or max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        store = cls.__new__(cls)
        store._validate_fixed_path = False
        store._initialize(Path(path), max_bytes=max_bytes)
        return store

    def _initialize(self, selected: Path, *, max_bytes: int) -> None:
        if selected.name in {"", ".", ".."}:
            raise ValueError("journal path must name a file")

        self.path = selected.absolute()
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._max_bytes = max_bytes
        self._thread_lock = RLock()
        self._claim_active = False
        self._claim_parent_descriptor = -1
        self._claim_lock_descriptor = -1
        self._claim_pid = -1
        self._claim_thread_id = -1
        self._claim_expected = _Snapshot(record=None, canonical=None, identity=None)

    def load(self) -> dict[str, object] | None:
        """Load a canonical record, rejecting unsafe or non-canonical filesystem content."""

        with self._thread_lock:
            if self._claim_active:
                self._require_active_claim()
                snapshot = self._load_snapshot(self._claim_parent_descriptor)
            else:
                parent_descriptor = self._open_parent()
                try:
                    snapshot = self._load_snapshot(parent_descriptor)
                finally:
                    os.close(parent_descriptor)
            return self._copy_record(snapshot.record)

    def claim(self) -> _JournalClaimContext:
        """Return a nonblocking exclusive claim bound to its entering process and thread."""

        return _JournalClaimContext(self)

    def _acquire_claim(self) -> None:
        parent_descriptor = -1
        lock_descriptor = -1
        locked = False
        try:
            with self._thread_lock:
                if self._claim_active:
                    raise ExactRestoreJournalClaimError(
                        "exact-restore journal claim is already active in this process"
                    )
                try:
                    parent_descriptor = self._open_parent()
                    lock_descriptor = self._open_claim_file(parent_descriptor)
                    try:
                        fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        locked = True
                    except BlockingIOError as error:
                        raise ExactRestoreJournalClaimError(
                            "exact-restore journal is claimed by another process"
                        ) from error
                    self._validate_parent(parent_descriptor)
                    self._validate_named_file(
                        parent_descriptor,
                        lock_descriptor,
                        self.lock_path.name,
                        enforce_size=False,
                    )
                    self._claim_expected = self._load_snapshot(parent_descriptor)
                    self._claim_parent_descriptor = parent_descriptor
                    self._claim_lock_descriptor = lock_descriptor
                    self._claim_pid = os.getpid()
                    self._claim_thread_id = get_ident()
                    self._claim_active = True
                    parent_descriptor = -1
                    lock_descriptor = -1
                    locked = False
                except (ExactRestoreJournalClaimError, ExactRestoreJournalError):
                    raise
                except OSError as error:
                    raise ExactRestoreJournalError(
                        "cannot acquire exact-restore journal claim"
                    ) from error
        finally:
            if locked and lock_descriptor >= 0:
                try:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
            if lock_descriptor >= 0:
                os.close(lock_descriptor)
            if parent_descriptor >= 0:
                os.close(parent_descriptor)

    def _release_claim(self) -> None:
        with self._thread_lock:
            if (
                not self._claim_active
                or self._claim_parent_descriptor < 0
                or self._claim_lock_descriptor < 0
                or self._claim_pid != os.getpid()
                or self._claim_thread_id != get_ident()
            ):
                raise ExactRestoreJournalClaimError(
                    "exact-restore claim must be released by its owner process and thread"
                )
            parent_descriptor = self._claim_parent_descriptor
            lock_descriptor = self._claim_lock_descriptor
            self._claim_active = False
            self._claim_parent_descriptor = -1
            self._claim_lock_descriptor = -1
            self._claim_pid = -1
            self._claim_thread_id = -1
            self._claim_expected = _Snapshot(
                record=None,
                canonical=None,
                identity=None,
            )
        try:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        finally:
            try:
                os.close(lock_descriptor)
            finally:
                os.close(parent_descriptor)

    def create(self, record: Mapping[str, object]) -> None:
        """Install the first journal record without replacing an existing claimant."""

        normalized, payload = self._canonical_record(record)
        with self._thread_lock:
            parent_descriptor = self._assert_claim_successor()
            if self._claim_expected.record is not None:
                raise ExactRestoreJournalClaimError(
                    "exact-restore journal already has a first record"
                )

            temporary_name: str | None = None
            try:
                temporary_name = self._write_temporary(parent_descriptor, payload)
                self._validate_active_claim()
                os.link(
                    temporary_name,
                    self.path.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                    follow_symlinks=False,
                )
                self._validate_active_claim()
                self._fsync_parent(parent_descriptor)
                self._validate_active_claim()
                os.unlink(temporary_name, dir_fd=parent_descriptor)
                temporary_name = None
                # Persist removal of the second link.  Returning with nlink=2 would make the
                # recovery journal intentionally unreadable after a crash.
                self._fsync_parent(parent_descriptor)
                self._validate_active_claim()
                self._accept_verified_successor(parent_descriptor, payload)
            except FileExistsError as error:
                raise ExactRestoreJournalClaimError(
                    "exact-restore journal already has a first record"
                ) from error
            except (ExactRestoreJournalClaimError, ExactRestoreJournalError):
                raise
            except OSError as error:
                raise ExactRestoreJournalError(
                    "cannot create exact-restore journal durably"
                ) from error
            finally:
                if temporary_name is not None:
                    self._unlink_temporary(parent_descriptor, temporary_name)

    def save(self, record: Mapping[str, object]) -> None:
        """Atomically replace an existing record with a canonical successor."""

        normalized, payload = self._canonical_record(record)
        with self._thread_lock:
            parent_descriptor = self._assert_claim_successor()
            if self._claim_expected.record is None:
                raise ExactRestoreJournalError(
                    "exact-restore journal must be created before it can be updated"
                )

            temporary_name: str | None = None
            try:
                temporary_name = self._write_temporary(parent_descriptor, payload)
                self._validate_active_claim()
                os.replace(
                    temporary_name,
                    self.path.name,
                    src_dir_fd=parent_descriptor,
                    dst_dir_fd=parent_descriptor,
                )
                temporary_name = None
                self._validate_active_claim()
                self._fsync_parent(parent_descriptor)
                self._validate_active_claim()
                self._accept_verified_successor(parent_descriptor, payload)
            except ExactRestoreJournalError:
                raise
            except OSError as error:
                raise ExactRestoreJournalError(
                    "cannot save exact-restore journal durably"
                ) from error
            finally:
                if temporary_name is not None:
                    self._unlink_temporary(parent_descriptor, temporary_name)

    def clear(self) -> None:
        """Remove the current journal and durably persist the directory successor."""

        with self._thread_lock:
            parent_descriptor = self._assert_claim_successor()
            try:
                if self._claim_expected.record is not None:
                    self._validate_active_claim()
                    os.unlink(self.path.name, dir_fd=parent_descriptor)
                    self._validate_active_claim()
                self._fsync_parent(parent_descriptor)
                self._validate_active_claim()
                snapshot = self._load_snapshot(parent_descriptor)
                if snapshot.record is not None:
                    raise ExactRestoreJournalError("exact-restore journal remained after clear")
                self._claim_expected = snapshot
            except ExactRestoreJournalError:
                raise
            except OSError as error:
                raise ExactRestoreJournalError(
                    "cannot clear exact-restore journal durably"
                ) from error

    def reload_and_confirm_successor(
        self,
        expected: Mapping[str, object] | None,
    ) -> bool:
        """Durably accept an exact successor after a mutation reported a late error.

        ``False`` means the current journal is a different valid successor.  Unsafe metadata,
        malformed bytes, and another fsync failure remain errors rather than being mistaken for a
        mismatch.  ``None`` is the exact successor expected after ``clear``.
        """

        canonical: bytes | None
        if expected is None:
            canonical = None
        else:
            _, canonical = self._canonical_record(expected)

        with self._thread_lock:
            parent_descriptor = self._require_active_claim()
            self._validate_parent(parent_descriptor)
            candidate = self._load_snapshot(parent_descriptor)
            if candidate.canonical != canonical:
                return False

            try:
                if candidate.record is not None:
                    descriptor = self._open_existing(parent_descriptor, allow_absent=False)
                    if descriptor is None:  # pragma: no cover - guarded by allow_absent=False
                        raise ExactRestoreJournalError(
                            "exact-restore successor disappeared before confirmation"
                        )
                    try:
                        os.fsync(descriptor)
                        self._validate_active_claim()
                        self._validate_named_file(
                            parent_descriptor,
                            descriptor,
                            self.path.name,
                        )
                    finally:
                        os.close(descriptor)
                self._fsync_parent(parent_descriptor)
                self._validate_active_claim()
            except ExactRestoreJournalError:
                raise
            except OSError as error:
                raise ExactRestoreJournalError(
                    "cannot durably confirm exact-restore journal successor"
                ) from error

            confirmed = self._load_snapshot(parent_descriptor)
            if confirmed.canonical != canonical or confirmed.identity != candidate.identity:
                return False
            self._claim_expected = confirmed
            return True

    def _assert_claim_successor(self) -> int:
        parent_descriptor = self._require_active_claim()
        self._validate_parent(parent_descriptor)
        current = self._load_snapshot(parent_descriptor)
        if current != self._claim_expected:
            raise ExactRestoreJournalClaimError(
                "exact-restore journal changed outside the active claim"
            )
        return parent_descriptor

    def _require_active_claim(self) -> int:
        self._validate_active_claim()
        return self._claim_parent_descriptor

    def _validate_active_claim(self) -> None:
        if (
            not self._claim_active
            or self._claim_parent_descriptor < 0
            or self._claim_lock_descriptor < 0
            or self._claim_pid != os.getpid()
            or self._claim_thread_id != get_ident()
        ):
            raise ExactRestoreJournalClaimError(
                "exact-restore journal mutation requires its exclusive claim"
            )
        try:
            self._validate_parent(self._claim_parent_descriptor)
            self._validate_named_file(
                self._claim_parent_descriptor,
                self._claim_lock_descriptor,
                self.lock_path.name,
                enforce_size=False,
            )
        except ExactRestoreJournalError as error:
            raise ExactRestoreJournalClaimError(
                "exact-restore journal claim changed while active"
            ) from error

    def _accept_verified_successor(
        self,
        parent_descriptor: int,
        canonical: bytes,
    ) -> None:
        self._validate_active_claim()
        snapshot = self._load_snapshot(parent_descriptor)
        if snapshot.canonical != canonical:
            raise ExactRestoreJournalError(
                "exact-restore journal does not contain the requested successor"
            )
        self._claim_expected = snapshot

    def _load_snapshot(self, parent_descriptor: int) -> _Snapshot:
        self._validate_parent(parent_descriptor)
        descriptor: int | None = None
        try:
            descriptor = self._open_existing(parent_descriptor, allow_absent=True)
            if descriptor is None:
                self._validate_parent(parent_descriptor)
                return _Snapshot(record=None, canonical=None, identity=None)
            before = os.fstat(descriptor)
            payload = self._read_bounded(descriptor)
            after = os.fstat(descriptor)
            self._validate_named_file(parent_descriptor, descriptor, self.path.name)
            if self._metadata_generation(before) != self._metadata_generation(after):
                raise ExactRestoreJournalError(
                    "exact-restore journal changed while it was being read"
                )
            record = self._decode_canonical_payload(payload)
            self._validate_parent(parent_descriptor)
            return _Snapshot(
                record=record,
                canonical=payload,
                identity=_FileIdentity(device=after.st_dev, inode=after.st_ino),
            )
        except ExactRestoreJournalError:
            raise
        except OSError as error:
            raise ExactRestoreJournalError("cannot read exact-restore journal") from error
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _open_parent(self) -> int:
        if not hasattr(os, "O_NOFOLLOW"):
            raise ExactRestoreJournalError("O_NOFOLLOW is required for exact-restore state")
        if self._validate_fixed_path:
            if self.path.parent != hardware_safety_root():
                raise ExactRestoreJournalError(
                    "exact-restore journal left the fixed hardware-safety root"
                )
            try:
                return _open_hardware_safety_root()
            except HardwareSafetyRootError as error:
                raise ExactRestoreJournalError(
                    "fixed hardware-safety root is unavailable for exact restore"
                ) from error
        try:
            metadata = self.path.parent.lstat()
        except OSError as error:
            raise ExactRestoreJournalError("exact-restore journal parent is unavailable") from error
        self._require_safe_parent_metadata(metadata)

        flags = os.O_RDONLY | os.O_NOFOLLOW
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = -1
        try:
            descriptor = os.open(self.path.parent, flags)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ExactRestoreJournalError("exact-restore journal parent changed while opening")
            self._validate_parent(descriptor)
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _validate_parent(self, descriptor: int) -> None:
        if self._validate_fixed_path:
            if self.path.parent != hardware_safety_root():
                raise ExactRestoreJournalError(
                    "exact-restore journal left the fixed hardware-safety root"
                )
            try:
                _validate_hardware_safety_root_descriptor(descriptor)
            except HardwareSafetyRootError as error:
                raise ExactRestoreJournalError(
                    "exact-restore journal parent changed while in use"
                ) from error
            return
        try:
            opened = os.fstat(descriptor)
            current = os.stat(self.path.parent, follow_symlinks=False)
        except OSError as error:
            raise ExactRestoreJournalError(
                "exact-restore journal parent changed while in use"
            ) from error
        self._require_safe_parent_metadata(opened)
        self._require_safe_parent_metadata(current)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ExactRestoreJournalError("exact-restore journal parent changed while in use")

    def _open_claim_file(self, parent_descriptor: int) -> int:
        flags = os.O_CREAT | os.O_RDWR | os.O_NONBLOCK | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = -1
        try:
            descriptor = os.open(
                self.lock_path.name,
                flags,
                0o600,
                dir_fd=parent_descriptor,
            )
            self._validate_named_file(
                parent_descriptor,
                descriptor,
                self.lock_path.name,
                enforce_size=False,
            )
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _open_existing(self, parent_descriptor: int, *, allow_absent: bool) -> int | None:
        try:
            metadata = os.stat(
                self.path.name,
                dir_fd=parent_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            if allow_absent:
                return None
            raise ExactRestoreJournalError("exact-restore journal disappeared") from None
        except OSError as error:
            raise ExactRestoreJournalError(
                "exact-restore journal metadata is unavailable"
            ) from error
        self._require_safe_file_metadata(metadata)

        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        descriptor = -1
        try:
            descriptor = os.open(self.path.name, flags, dir_fd=parent_descriptor)
            opened = os.fstat(descriptor)
            if (opened.st_dev, opened.st_ino) != (metadata.st_dev, metadata.st_ino):
                raise ExactRestoreJournalError("exact-restore journal changed while opening")
            self._validate_named_file(parent_descriptor, descriptor, self.path.name)
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _validate_named_file(
        self,
        parent_descriptor: int,
        descriptor: int,
        name: str,
        *,
        enforce_size: bool = True,
    ) -> None:
        try:
            opened = os.fstat(descriptor)
            current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError as error:
            raise ExactRestoreJournalError(
                "exact-restore safety file changed while opening"
            ) from error
        self._require_safe_file_metadata(opened, enforce_size=enforce_size)
        self._require_safe_file_metadata(current, enforce_size=enforce_size)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ExactRestoreJournalError("exact-restore safety file changed while opening")

    def _write_temporary(self, parent_descriptor: int, payload: bytes) -> str:
        if len(payload) > self._max_bytes:
            raise ExactRestoreJournalError("exact-restore journal is too large to write")

        descriptor = -1
        temporary_name = ""
        for _ in range(128):
            temporary_name = f".{self.path.name}.{secrets.token_hex(12)}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
            if hasattr(os, "O_CLOEXEC"):
                flags |= os.O_CLOEXEC
            try:
                descriptor = os.open(
                    temporary_name,
                    flags,
                    0o600,
                    dir_fd=parent_descriptor,
                )
                break
            except FileExistsError:
                continue
        else:  # pragma: no cover - cryptographically improbable without injected faults
            raise ExactRestoreJournalError("cannot allocate exact-restore temporary journal")

        try:
            os.fchmod(descriptor, 0o600)
            self._validate_named_file(
                parent_descriptor,
                descriptor,
                temporary_name,
            )
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write while persisting exact-restore journal")
                view = view[written:]
            os.fsync(descriptor)
            self._validate_named_file(
                parent_descriptor,
                descriptor,
                temporary_name,
            )
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            self._unlink_temporary(parent_descriptor, temporary_name)
            raise
        try:
            os.close(descriptor)
        except OSError:
            self._unlink_temporary(parent_descriptor, temporary_name)
            raise
        return temporary_name

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
            raise ExactRestoreJournalError("exact-restore journal is too large")
        return payload

    def _canonical_record(
        self,
        record: Mapping[str, object],
    ) -> tuple[dict[str, object], bytes]:
        if not isinstance(record, Mapping):
            raise ExactRestoreJournalError("exact-restore journal must be a JSON object")
        try:
            normalized = self._normalize_json_object(record)
            encoded = (
                json.dumps(
                    normalized,
                    allow_nan=False,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
                + b"\n"
            )
        except (RecursionError, TypeError, ValueError, UnicodeError) as error:
            raise ExactRestoreJournalError(
                "exact-restore journal is not JSON-compatible"
            ) from error
        if len(encoded) > self._max_bytes:
            raise ExactRestoreJournalError("exact-restore journal is too large to write")
        return normalized, encoded

    def _decode_canonical_payload(self, payload: bytes) -> dict[str, object]:
        try:
            text = payload.decode("utf-8")
            decoded = json.loads(text, object_pairs_hook=self._unique_object)
            if not isinstance(decoded, dict):
                raise ValueError("journal root is not an object")
            normalized, canonical = self._canonical_record(decoded)
        except ExactRestoreJournalError:
            raise
        except (json.JSONDecodeError, RecursionError, TypeError, ValueError, UnicodeError) as error:
            raise ExactRestoreJournalError("cannot decode exact-restore journal") from error
        if canonical != payload:
            raise ExactRestoreJournalError("exact-restore journal is not canonical JSON")
        return normalized

    @classmethod
    def _normalize_json_object(cls, value: Mapping[str, object]) -> dict[str, object]:
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            normalized[key] = cls._normalize_json_value(item)
        return normalized

    @classmethod
    def _normalize_json_value(cls, value: object) -> object:
        if value is None or type(value) in {bool, int, str}:
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("non-finite floats are not JSON values")
            return value
        if isinstance(value, list):
            return [cls._normalize_json_value(item) for item in value]
        if isinstance(value, Mapping):
            return cls._normalize_json_object(value)
        raise TypeError(f"unsupported JSON value: {type(value).__name__}")

    @staticmethod
    def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    @staticmethod
    def _metadata_generation(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
        return (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
        )

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
            raise ExactRestoreJournalError("exact-restore safety file has unsafe metadata")

    @staticmethod
    def _require_safe_parent_metadata(metadata: os.stat_result) -> None:
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid():
            raise ExactRestoreJournalError(
                "exact-restore journal parent has unsafe ownership or type"
            )

    @staticmethod
    def _copy_record(record: dict[str, object] | None) -> dict[str, object] | None:
        if record is None:
            return None
        # The in-memory value was already validated.  A JSON round trip gives callers a deep copy
        # without exposing the claim's expected-successor state to mutation.
        return json.loads(json.dumps(record, allow_nan=False, ensure_ascii=False))

    @staticmethod
    def _fsync_parent(parent_descriptor: int) -> None:
        os.fsync(parent_descriptor)

    @staticmethod
    def _unlink_temporary(parent_descriptor: int, temporary_name: str) -> None:
        try:
            os.unlink(temporary_name, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass
        except OSError:
            # The primary operation must remain failed.  A leftover second link is rejected by
            # nlink validation rather than being silently trusted by recovery.
            pass


__all__ = [
    "ExactRestoreJournalClaimError",
    "ExactRestoreJournalError",
    "ExactRestoreJournalStore",
]
