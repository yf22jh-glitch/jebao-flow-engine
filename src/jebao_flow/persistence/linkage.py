"""Atomic JSON journal for native-linkage recovery."""

from __future__ import annotations

import fcntl
import os
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

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._lock = RLock()

    def load(self) -> LinkageTransactionRecord | None:
        with self._lock:
            if not self.path.exists():
                return None
            try:
                return LinkageTransactionRecord.model_validate_json(
                    self.path.read_text(encoding="utf-8")
                )
            except (OSError, ValidationError, ValueError) as error:
                raise LinkageJournalError(
                    f"cannot read linkage recovery journal {self.path}"
                ) from error

    @contextmanager
    def lease(self) -> Iterator[None]:
        """Own run/recovery for the journal lifetime across daemon processes."""

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
            os.fchmod(descriptor, 0o600)
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
