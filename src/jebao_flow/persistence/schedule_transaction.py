"""Private atomic journal for byte-exact temporary schedule recovery."""

from __future__ import annotations

import fcntl
import os
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from pydantic import ValidationError

from jebao_flow.devices.schedule_transaction import (
    TemporaryScheduleErrorCode,
    TemporaryScheduleJournalClaimError,
    TemporaryScheduleJournalError,
    TemporaryScheduleRecord,
)


class JsonTemporaryScheduleJournalStore:
    """Persist one private recovery record with replace+directory fsync semantics."""

    _MAX_BYTES = 1024 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._lock = RLock()
        self._lease_active = False
        self._lease_expected: TemporaryScheduleRecord | None = None

    def load(self) -> TemporaryScheduleRecord | None:
        with self._lock:
            descriptor = self._open_existing(allow_absent=True)
            if descriptor is None:
                return None
            try:
                with os.fdopen(descriptor, encoding="utf-8") as stream:
                    descriptor = -1
                    payload = stream.read(self._MAX_BYTES + 1)
                if len(payload.encode()) > self._MAX_BYTES:
                    raise self._error()
                return TemporaryScheduleRecord.model_validate_json(payload)
            except TemporaryScheduleJournalError:
                raise
            except (OSError, ValidationError, ValueError):
                raise self._error() from None
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    @contextmanager
    def lease(self) -> Iterator[None]:
        descriptor = -1
        owns_state = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not hasattr(os, "O_NOFOLLOW"):
                raise self._error()
            descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            self._validate_open_file(descriptor, self.lock_path)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise TemporaryScheduleJournalClaimError(
                    TemporaryScheduleErrorCode.JOURNAL_BUSY
                ) from None
            self._validate_open_file(descriptor, self.lock_path)
            with self._lock:
                if self._lease_active:
                    raise self._error()
                self._lease_expected = self.load()
                self._lease_active = True
                owns_state = True
            yield
        except (TemporaryScheduleJournalClaimError, TemporaryScheduleJournalError):
            raise
        except OSError:
            raise self._error() from None
        finally:
            if owns_state:
                with self._lock:
                    self._lease_expected = None
                    self._lease_active = False
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def create(self, record: TemporaryScheduleRecord) -> None:
        with self._lock:
            record = self._validated(record)
            temporary_path: Path | None = None
            try:
                self._assert_successor()
                if self._lease_expected is not None:
                    raise TemporaryScheduleJournalClaimError(
                        TemporaryScheduleErrorCode.JOURNAL_BUSY
                    )
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = self._write_temporary(record)
                os.link(temporary_path, self.path)
                self._fsync_parent()
                temporary_path.unlink()
                temporary_path = None
                self._fsync_parent()
                descriptor = self._open_existing(allow_absent=False)
                if descriptor is None:
                    raise self._error()
                os.close(descriptor)
                self._lease_expected = record
            except FileExistsError:
                raise TemporaryScheduleJournalClaimError(
                    TemporaryScheduleErrorCode.JOURNAL_BUSY
                ) from None
            except (TemporaryScheduleJournalClaimError, TemporaryScheduleJournalError):
                raise
            except OSError:
                raise self._error() from None
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def save(self, record: TemporaryScheduleRecord) -> None:
        with self._lock:
            record = self._validated(record)
            temporary_path: Path | None = None
            try:
                self._assert_successor()
                existing = self._open_existing(allow_absent=False)
                if existing is None:
                    raise self._error()
                os.close(existing)
                temporary_path = self._write_temporary(record)
                temporary_path.replace(self.path)
                temporary_path = None
                self._fsync_parent()
                descriptor = self._open_existing(allow_absent=False)
                if descriptor is None:
                    raise self._error()
                os.close(descriptor)
                self._lease_expected = record
            except TemporaryScheduleJournalError:
                raise
            except OSError:
                raise self._error() from None
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def confirms_lease_successor(self, record: TemporaryScheduleRecord) -> bool:
        with self._lock:
            record = self._validated(record)
            return self._lease_active and self._lease_expected == record and self.load() == record

    def clear(self) -> None:
        with self._lock:
            try:
                self._assert_successor()
                existing = self._open_existing(allow_absent=True)
                if existing is not None:
                    os.close(existing)
                self.path.unlink(missing_ok=True)
                self._fsync_parent()
                remaining = self._open_existing(allow_absent=True)
                if remaining is not None:
                    os.close(remaining)
                    raise self._error()
                self._lease_expected = None
            except TemporaryScheduleJournalError:
                raise
            except OSError:
                raise self._error() from None

    def _write_temporary(self, record: TemporaryScheduleRecord) -> Path:
        payload = record.model_dump_json(indent=2) + "\n"
        if len(payload.encode()) > self._MAX_BYTES:
            raise self._error()
        descriptor, name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary_path = Path(name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            temporary_path.unlink(missing_ok=True)
            raise
        return temporary_path

    def _assert_successor(self) -> None:
        if not self._lease_active or self.load() != self._lease_expected:
            raise self._error()

    def _open_existing(self, *, allow_absent: bool) -> int | None:
        if not hasattr(os, "O_NOFOLLOW"):
            raise self._error()
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            if allow_absent:
                return None
            raise self._error() from None
        except OSError:
            raise self._error() from None
        self._require_safe_metadata(metadata)
        descriptor = -1
        try:
            descriptor = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            self._validate_open_file(descriptor, self.path)
            return descriptor
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            raise

    def _fsync_parent(self) -> None:
        if not self.path.parent.exists():
            return
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        descriptor = os.open(self.path.parent, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _require_safe_metadata(metadata: os.stat_result) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise TemporaryScheduleJournalError(TemporaryScheduleErrorCode.JOURNAL_FAILED)

    @classmethod
    def _validate_open_file(cls, descriptor: int, path: Path) -> None:
        try:
            opened = os.fstat(descriptor)
            current = os.stat(path, follow_symlinks=False)
        except OSError:
            raise TemporaryScheduleJournalError(TemporaryScheduleErrorCode.JOURNAL_FAILED) from None
        cls._require_safe_metadata(opened)
        cls._require_safe_metadata(current)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise TemporaryScheduleJournalError(TemporaryScheduleErrorCode.JOURNAL_FAILED)

    @staticmethod
    def _validated(record: TemporaryScheduleRecord) -> TemporaryScheduleRecord:
        try:
            return TemporaryScheduleRecord.model_validate(record.model_dump())
        except (ValidationError, ValueError):
            raise TemporaryScheduleJournalError(TemporaryScheduleErrorCode.JOURNAL_FAILED) from None

    @staticmethod
    def _error() -> TemporaryScheduleJournalError:
        return TemporaryScheduleJournalError(TemporaryScheduleErrorCode.JOURNAL_FAILED)


__all__ = ["JsonTemporaryScheduleJournalStore"]
