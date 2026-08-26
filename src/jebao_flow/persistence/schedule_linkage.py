"""Private atomic journal for the linkage-only schedule diagnostic."""

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

from jebao_flow.devices.schedule_linkage import (
    ScheduleLinkageJournalClaimError,
    ScheduleLinkageRecord,
)


class ScheduleLinkageJournalError(RuntimeError):
    """The schedule-linkage recovery journal could not be handled safely."""


class JsonScheduleLinkageJournalStore:
    """Persist one role-only transaction with atomic replace and directory fsync."""

    _MAX_BYTES = 1024 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._lock = RLock()
        self._lease_active = False
        self._lease_expected: ScheduleLinkageRecord | None = None

    def load(self) -> ScheduleLinkageRecord | None:
        with self._lock:
            descriptor = self._open_existing(allow_absent=True)
            if descriptor is None:
                return None
            try:
                with os.fdopen(descriptor, encoding="utf-8") as stream:
                    descriptor = -1
                    payload = stream.read(self._MAX_BYTES + 1)
                if len(payload.encode()) > self._MAX_BYTES:
                    raise ScheduleLinkageJournalError("schedule-linkage journal is too large")
                return ScheduleLinkageRecord.model_validate_json(payload)
            except ScheduleLinkageJournalError:
                raise
            except (OSError, ValidationError, ValueError) as error:
                raise ScheduleLinkageJournalError(
                    "cannot read schedule-linkage recovery journal"
                ) from error
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    @contextmanager
    def lease(self) -> Iterator[None]:
        descriptor = -1
        owns_lease_state = False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            if not hasattr(os, "O_NOFOLLOW"):
                raise ScheduleLinkageJournalError(
                    "O_NOFOLLOW is required for schedule-linkage recovery state"
                )
            descriptor = os.open(
                self.lock_path,
                os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
                0o600,
            )
            self._validate_open_file(descriptor, self.lock_path)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ScheduleLinkageJournalClaimError(
                    "schedule-linkage journal is owned by another process"
                ) from error
            self._validate_open_file(descriptor, self.lock_path)
            with self._lock:
                if self._lease_active:
                    raise ScheduleLinkageJournalError(
                        "schedule-linkage store lease is already active"
                    )
                expected = self.load()
                self._lease_active = True
                self._lease_expected = expected
                owns_lease_state = True
            yield
        except ScheduleLinkageJournalClaimError:
            raise
        except ScheduleLinkageJournalError:
            raise
        except OSError as error:
            raise ScheduleLinkageJournalError(
                "cannot open schedule-linkage journal lease"
            ) from error
        finally:
            if owns_lease_state:
                with self._lock:
                    self._lease_active = False
                    self._lease_expected = None
            if descriptor >= 0:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)

    def create(self, record: ScheduleLinkageRecord) -> None:
        with self._lock:
            record = self._validated_record(record)
            temporary_path: Path | None = None
            try:
                self._assert_lease_successor()
                if self._lease_expected is not None:
                    raise ScheduleLinkageJournalClaimError(
                        "schedule-linkage recovery journal is already claimed"
                    )
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = self._write_temporary(record)
                os.link(temporary_path, self.path)
                self._fsync_parent()
                temporary_path.unlink()
                temporary_path = None
                # Persist removal of the temporary hardlink before returning authority for the
                # first physical write.  Otherwise crash replay could leave nlink=2 and make the
                # recovery journal deliberately unreadable.
                self._fsync_parent()
                descriptor = self._open_existing(allow_absent=False)
                if descriptor is None:
                    raise ScheduleLinkageJournalError(
                        "schedule-linkage journal disappeared after creation"
                    )
                os.close(descriptor)
                self._lease_expected = record
            except FileExistsError as error:
                raise ScheduleLinkageJournalClaimError(
                    "schedule-linkage recovery journal is already claimed"
                ) from error
            except ScheduleLinkageJournalError:
                self._accept_durable_successor(record)
                raise
            except OSError as error:
                self._accept_durable_successor(record)
                raise ScheduleLinkageJournalError(
                    "cannot claim schedule-linkage recovery journal"
                ) from error
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def save(self, record: ScheduleLinkageRecord) -> None:
        with self._lock:
            record = self._validated_record(record)
            temporary_path: Path | None = None
            try:
                self._assert_lease_successor()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                existing = self._open_existing(allow_absent=True)
                if existing is not None:
                    os.close(existing)
                temporary_path = self._write_temporary(record)
                temporary_path.replace(self.path)
                self._fsync_parent()
                descriptor = self._open_existing(allow_absent=False)
                if descriptor is None:
                    raise ScheduleLinkageJournalError(
                        "schedule-linkage journal disappeared after save"
                    )
                os.close(descriptor)
                self._lease_expected = record
            except ScheduleLinkageJournalError:
                self._accept_durable_successor(record)
                raise
            except OSError as error:
                self._accept_durable_successor(record)
                raise ScheduleLinkageJournalError(
                    "cannot persist schedule-linkage recovery journal"
                ) from error
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def confirms_lease_successor(self, record: ScheduleLinkageRecord) -> bool:
        """Prove that an uncertain save durably became this lease's exact successor."""

        with self._lock:
            record = self._validated_record(record)
            if not self._lease_active or self._lease_expected != record:
                return False
            return self.load() == record

    def clear(self) -> None:
        with self._lock:
            try:
                self._assert_lease_successor()
                existing = self._open_existing(allow_absent=True)
                if existing is not None:
                    os.close(existing)
                self.path.unlink(missing_ok=True)
                self._fsync_parent()
                remaining = self._open_existing(allow_absent=True)
                if remaining is not None:
                    os.close(remaining)
                    raise ScheduleLinkageJournalError(
                        "schedule-linkage journal remained after clear"
                    )
                self._lease_expected = None
            except ScheduleLinkageJournalError:
                self._accept_durable_clear()
                raise
            except OSError as error:
                self._accept_durable_clear()
                raise ScheduleLinkageJournalError(
                    "cannot clear schedule-linkage recovery journal"
                ) from error

    def _write_temporary(self, record: ScheduleLinkageRecord) -> Path:
        payload = record.model_dump_json(indent=2) + "\n"
        if len(payload.encode()) > self._MAX_BYTES:
            raise ScheduleLinkageJournalError("schedule-linkage journal is too large to write")
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary_path = Path(temporary_name)
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

    @staticmethod
    def _validated_record(record: ScheduleLinkageRecord) -> ScheduleLinkageRecord:
        try:
            return ScheduleLinkageRecord.model_validate(record.model_dump())
        except (ValidationError, ValueError) as error:
            raise ScheduleLinkageJournalError(
                "refusing to persist an invalid schedule-linkage journal"
            ) from error

    def _assert_lease_successor(self) -> None:
        if not self._lease_active:
            raise ScheduleLinkageJournalError(
                "schedule-linkage journal mutation requires its exclusive lease"
            )
        if self.load() != self._lease_expected:
            raise ScheduleLinkageJournalError(
                "schedule-linkage journal changed outside the active lease"
            )

    def _accept_durable_successor(self, record: ScheduleLinkageRecord) -> None:
        if not self._lease_active:
            return
        try:
            if self.load() == record:
                self._lease_expected = record
        except ScheduleLinkageJournalError:
            return

    def _accept_durable_clear(self) -> None:
        if not self._lease_active:
            return
        try:
            if self.load() is None:
                self._lease_expected = None
        except ScheduleLinkageJournalError:
            return

    def _open_existing(self, *, allow_absent: bool) -> int | None:
        if not hasattr(os, "O_NOFOLLOW"):
            raise ScheduleLinkageJournalError(
                "O_NOFOLLOW is required for schedule-linkage recovery state"
            )
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            if allow_absent:
                return None
            raise ScheduleLinkageJournalError(
                "schedule-linkage recovery journal disappeared"
            ) from None
        except OSError as error:
            raise ScheduleLinkageJournalError(
                "schedule-linkage journal metadata is unavailable"
            ) from error
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
            raise ScheduleLinkageJournalError(
                "schedule-linkage journal has unsafe metadata"
            )

    @classmethod
    def _validate_open_file(cls, descriptor: int, path: Path) -> None:
        try:
            opened = os.fstat(descriptor)
            current = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise ScheduleLinkageJournalError(
                "schedule-linkage safety file changed while opening"
            ) from error
        cls._require_safe_metadata(opened)
        cls._require_safe_metadata(current)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise ScheduleLinkageJournalError(
                "schedule-linkage safety file changed while opening"
            )


__all__ = ["JsonScheduleLinkageJournalStore", "ScheduleLinkageJournalError"]
