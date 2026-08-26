"""Atomic JSON journal for native-linkage recovery."""

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

from jebao_flow.devices.linkage import LinkageJournalClaimError, LinkageTransactionRecord


class LinkageJournalError(RuntimeError):
    pass


class JsonLinkageJournalStore:
    """Persist one unfinished operation with atomic replace and filesystem sync."""

    _MAX_JOURNAL_BYTES = 1024 * 1024

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._lock = RLock()

    def load(self) -> LinkageTransactionRecord | None:
        with self._lock:
            descriptor = self._open_existing(allow_absent=True)
            if descriptor is None:
                return None
            try:
                with os.fdopen(descriptor, encoding="utf-8") as stream:
                    descriptor = -1
                    payload = stream.read(self._MAX_JOURNAL_BYTES + 1)
                if len(payload.encode()) > self._MAX_JOURNAL_BYTES:
                    raise LinkageJournalError("linkage recovery journal is too large")
                return LinkageTransactionRecord.model_validate_json(payload)
            except LinkageJournalError:
                raise
            except (OSError, ValidationError, ValueError) as error:
                raise LinkageJournalError(
                    f"cannot read linkage recovery journal {self.path}"
                ) from error
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    @contextmanager
    def lease(self) -> Iterator[None]:
        """Own run/recovery for the journal lifetime across daemon processes."""

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_CREAT | os.O_RDWR
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(self.lock_path, flags, 0o600)
            self._validate_open_file(descriptor, self.lock_path)
        except OSError as error:
            raise LinkageJournalError(
                f"cannot open linkage journal lease {self.lock_path}"
            ) from error
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise LinkageJournalClaimError(
                    f"linkage journal {self.path} is owned by another daemon"
                ) from error
            self._validate_open_file(descriptor, self.lock_path)
            yield
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def create(self, record: LinkageTransactionRecord) -> None:
        """Claim an empty journal without a cross-process load/save race."""

        with self._lock:
            temporary_path: Path | None = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = self._write_temporary(record)
                os.link(temporary_path, self.path)
                self._fsync_parent()
                temporary_path.unlink()
                temporary_path = None
                # The destination and temporary name briefly refer to the same inode. Persist
                # removal of the temporary hardlink before returning authority for a physical
                # write; otherwise a power loss can replay nlink=2 and make recovery fail closed
                # on its own journal.
                self._fsync_parent()
                descriptor = self._open_existing(allow_absent=False)
                if descriptor is None:  # pragma: no cover - defensive type narrowing
                    raise LinkageJournalError(
                        "linkage recovery journal disappeared after creation"
                    )
                os.close(descriptor)
            except FileExistsError as error:
                raise LinkageJournalClaimError(
                    f"linkage recovery journal {self.path} is already claimed"
                ) from error
            except OSError as error:
                raise LinkageJournalError(
                    f"cannot claim linkage recovery journal {self.path}"
                ) from error
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def save(self, record: LinkageTransactionRecord) -> None:
        with self._lock:
            temporary_path: Path | None = None
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                existing = self._open_existing(allow_absent=True)
                if existing is not None:
                    os.close(existing)
                temporary_path = self._write_temporary(record)
                temporary_path.replace(self.path)
                self._fsync_parent()
            except OSError as error:
                raise LinkageJournalError(
                    f"cannot persist linkage recovery journal {self.path}"
                ) from error
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)

    def clear(self) -> None:
        with self._lock:
            try:
                existing = self._open_existing(allow_absent=True)
                if existing is not None:
                    os.close(existing)
                self.path.unlink(missing_ok=True)
                self._fsync_parent()
            except OSError as error:
                raise LinkageJournalError(
                    f"cannot clear linkage recovery journal {self.path}"
                ) from error

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

    def _write_temporary(self, record: LinkageTransactionRecord) -> Path:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(record.model_dump_json(indent=2))
                stream.write("\n")
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

    def _open_existing(self, *, allow_absent: bool) -> int | None:
        """Open one private regular journal without following or blocking on special files."""

        if not hasattr(os, "O_NOFOLLOW"):
            raise LinkageJournalError("O_NOFOLLOW is required for linkage recovery state")
        try:
            metadata = self.path.lstat()
        except FileNotFoundError:
            if allow_absent:
                return None
            raise LinkageJournalError("linkage recovery journal disappeared") from None
        except OSError as error:
            raise LinkageJournalError("linkage recovery journal metadata is unavailable") from error
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

    @staticmethod
    def _require_safe_metadata(metadata: os.stat_result) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise LinkageJournalError("linkage recovery journal has unsafe metadata")

    @classmethod
    def _validate_open_file(cls, descriptor: int, path: Path) -> None:
        try:
            opened = os.fstat(descriptor)
            current = os.stat(path, follow_symlinks=False)
        except OSError as error:
            raise LinkageJournalError("linkage safety file changed while opening") from error
        cls._require_safe_metadata(opened)
        cls._require_safe_metadata(current)
        if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise LinkageJournalError("linkage safety file changed while opening")
