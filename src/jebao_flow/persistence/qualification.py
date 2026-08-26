"""Durable receipts for the mandatory single-device first-write qualification."""

from __future__ import annotations

import os
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from jebao_flow.devices.identity import PhysicalDeviceBinding, physical_identity_key


class QualificationStoreError(RuntimeError):
    """A qualification receipt could not be read or persisted safely."""


class DeviceQualificationReceipt(BaseModel):
    """Proof that one exact controller completed the attended minimal-write sequence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    operation_id: str = Field(min_length=1, max_length=128)
    device_id: str = Field(min_length=1)
    physical_binding: PhysicalDeviceBinding
    original_power: int = Field(ge=0, le=45)
    step_power: int = Field(ge=0, le=45)
    completed_at: datetime
    valid_until: datetime

    @model_validator(mode="after")
    def validate_bounded_step_and_lifetime(self) -> Self:
        delta = self.original_power - self.step_power
        if not 1 <= delta <= 5:
            raise ValueError("qualification step must lower power by 1..5 percentage points")
        if self.completed_at.tzinfo is None or self.valid_until.tzinfo is None:
            raise ValueError("qualification timestamps must be timezone-aware")
        if self.valid_until <= self.completed_at:
            raise ValueError("qualification validity must end after completion")
        if self.valid_until - self.completed_at > timedelta(hours=24):
            raise ValueError("qualification validity must not exceed 24 hours")
        return self

    def is_valid_for(
        self,
        binding: PhysicalDeviceBinding,
        *,
        now: datetime | None = None,
    ) -> bool:
        checked_at = now or datetime.now(UTC)
        return (
            self.physical_binding == binding
            and self.completed_at <= checked_at <= self.valid_until
        )


class JsonQualificationStore:
    """Atomic per-controller receipt store under the shared hardware-safety volume."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def path_for(self, binding: PhysicalDeviceBinding) -> Path:
        return self.directory / f"{physical_identity_key(binding)}.json"

    def load(self, binding: PhysicalDeviceBinding) -> DeviceQualificationReceipt | None:
        try:
            self.directory.lstat()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise QualificationStoreError("qualification directory is unavailable") from error
        else:
            self._validate_directory()
        path = self.path_for(binding)
        try:
            initial = path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise QualificationStoreError("qualification receipt is unreadable") from error
        self._require_safe_receipt_metadata(initial)
        descriptor = -1
        try:
            if not hasattr(os, "O_NOFOLLOW"):
                raise QualificationStoreError("O_NOFOLLOW is required for qualification receipts")
            flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
            descriptor = os.open(path, flags)
            opened = os.fstat(descriptor)
            current = os.stat(path, follow_symlinks=False)
            self._require_safe_receipt_metadata(opened)
            self._require_safe_receipt_metadata(current)
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise QualificationStoreError("qualification receipt changed while opening")
            with os.fdopen(descriptor, encoding="utf-8") as stream:
                descriptor = -1
                return DeviceQualificationReceipt.model_validate_json(stream.read())
        except QualificationStoreError:
            raise
        except (OSError, ValidationError, ValueError) as error:
            raise QualificationStoreError("qualification receipt is unreadable") from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def save(self, receipt: DeviceQualificationReceipt) -> None:
        temporary_path: Path | None = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            self._validate_directory()
            destination = self.path_for(receipt.physical_binding)
            if os.path.lexists(destination):
                metadata = destination.lstat()
                self._require_safe_receipt_metadata(metadata)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".qualification.",
                suffix=".tmp",
                dir=self.directory,
            )
            temporary_path = Path(temporary_name)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(receipt.model_dump_json(indent=2))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(destination)
            self._fsync_directory()
        except OSError as error:
            raise QualificationStoreError("cannot persist qualification receipt") from error
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

    def _fsync_directory(self) -> None:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _require_safe_receipt_metadata(metadata: os.stat_result) -> None:
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
        ):
            raise QualificationStoreError("qualification receipt has unsafe metadata")

    def _validate_directory(self) -> None:
        try:
            metadata = self.directory.lstat()
        except OSError as error:
            raise QualificationStoreError("qualification directory is unavailable") from error
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or self.directory.is_symlink()
            or metadata.st_uid != os.geteuid()
        ):
            raise QualificationStoreError("qualification directory has unsafe metadata")
        mode = stat.S_IMODE(metadata.st_mode)
        if mode != 0o700:
            raise QualificationStoreError("qualification directory must have mode 0700")


__all__ = [
    "DeviceQualificationReceipt",
    "JsonQualificationStore",
    "QualificationStoreError",
]
