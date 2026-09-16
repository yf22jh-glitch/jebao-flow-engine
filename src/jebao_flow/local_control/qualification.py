"""Owner-only qualification receipt bindings for the local-control catalog."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_serializer,
    model_validator,
)

from jebao_flow.hardware_safety import qualification_directory
from jebao_flow.local_control.models import QualificationBinding
from jebao_flow.operator_capture.paths import durable_private_path

_QUALIFICATIONS = TypeAdapter(tuple[QualificationBinding, ...])
_MAX_RECEIPT_BYTES = 1024 * 1024
_RECEIPT_PREFIX = "local-control-receipt-"
_RECEIPT_SUFFIX = ".json"


class QualificationStoreError(RuntimeError):
    """Qualification bindings are missing, unsafe, or malformed."""


class LocalControlQualificationReceipt(BaseModel):
    """One control intent with ACK and function-specific readback evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    product_key: str = Field(pattern=r"^[0-9a-f]{32}$")
    function: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,95}$")
    physical_identity_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    app_version: str = Field(min_length=1, max_length=128)
    app_catalog_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    template_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    firmware_version: str = Field(min_length=1, max_length=256)
    request_id: str = Field(pattern=r"^[A-Za-z0-9_-]{8,128}$")
    intent_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    control_payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledgement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    readback_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    completed_at: datetime
    outcome: Literal["acknowledged_and_verified"] = "acknowledged_and_verified"
    verification_kind: Literal["exact_readback", "advancing_clock"] = "exact_readback"

    @model_serializer(mode="wrap")
    def omit_legacy_default(self, handler):
        value = handler(self)
        if self.verification_kind == "exact_readback":
            value.pop("verification_kind", None)
        return value

    @model_validator(mode="after")
    def validate_public_evidence(self) -> Self:
        if (self.verification_kind == "advancing_clock") != (self.function == "device_clock_sync"):
            raise ValueError("clock receipts must declare advancing-clock evidence")
        if self.completed_at.tzinfo is None or self.completed_at.utcoffset() is None:
            raise ValueError("qualification receipt completion time must be timezone-aware")
        for value in (self.app_version, self.firmware_version):
            if not value.isascii() or any(ord(character) < 0x20 for character in value):
                raise ValueError("qualification receipt versions must use printable ASCII")
        return self


def load_current_qualifications(
    path: Path | str | None,
    *,
    _allow_ephemeral_for_test: bool = False,
) -> tuple[QualificationBinding, ...]:
    """Load only the explicitly supplied current receipt set; absence means no controls."""

    if path is None:
        return ()
    source = (
        Path(path).resolve()
        if _allow_ephemeral_for_test
        else durable_private_path(path, description="local-control qualification file")
    )
    if not hasattr(os, "O_NOFOLLOW"):
        raise QualificationStoreError("O_NOFOLLOW is required for qualification input")
    try:
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise QualificationStoreError("qualification input is unavailable") from error
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_size > 1024 * 1024
        ):
            raise QualificationStoreError("qualification input metadata is unsafe")
        payload = os.read(descriptor, 1024 * 1024 + 1)
        if len(payload) > 1024 * 1024 or os.read(descriptor, 1):
            raise QualificationStoreError("qualification input exceeds its size bound")
        named = source.stat(follow_symlinks=False)
        if (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino):
            raise QualificationStoreError("qualification input changed while reading")
    finally:
        os.close(descriptor)
    try:
        decoded = json.loads(payload)
        if isinstance(decoded, dict):
            decoded = decoded.get("qualifications")
        result = _QUALIFICATIONS.validate_python(decoded)
    except (json.JSONDecodeError, ValidationError, TypeError) as error:
        raise QualificationStoreError("qualification input is invalid") from error
    keys = [(item.product_key, item.function) for item in result]
    if len(keys) != len(set(keys)):
        raise QualificationStoreError("qualification input contains duplicate functions")
    return result


def verify_qualification_receipts(
    qualification: QualificationBinding,
    *,
    _receipt_root_for_test: Path | None = None,
) -> tuple[LocalControlQualificationReceipt, ...]:
    """Resolve every claimed digest to an immutable current artifact and hash its bytes."""

    root = (
        qualification_directory()
        if _receipt_root_for_test is None
        else Path(_receipt_root_for_test).resolve()
    )
    try:
        metadata = root.lstat()
    except OSError as error:
        raise QualificationStoreError("qualification receipt directory is unavailable") from error
    if (
        root.is_symlink()
        or not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise QualificationStoreError("qualification receipt directory metadata is unsafe")
    receipts: list[LocalControlQualificationReceipt] = []
    for digest in qualification.receipt_sha256:
        payload = _read_receipt(root / f"{_RECEIPT_PREFIX}{digest}{_RECEIPT_SUFFIX}")
        if not payload or not hmac.compare_digest(hashlib.sha256(payload).hexdigest(), digest):
            raise QualificationStoreError("qualification receipt artifact digest mismatch")
        try:
            receipt = LocalControlQualificationReceipt.model_validate_json(payload)
        except (ValidationError, ValueError) as error:
            raise QualificationStoreError("qualification receipt artifact is invalid") from error
        if payload != qualification_receipt_bytes(receipt):
            raise QualificationStoreError("qualification receipt artifact is not canonical")
        if any(
            getattr(receipt, field) != getattr(qualification, field)
            for field in (
                "product_key",
                "function",
                "app_version",
                "app_catalog_sha256",
                "template_sha256",
                "schema_sha256",
                "firmware_version",
            )
        ):
            raise QualificationStoreError(
                "qualification receipt does not match its capability binding"
            )
        receipts.append(receipt)
    if len({receipt.request_id for receipt in receipts}) != len(receipts):
        raise QualificationStoreError("qualification receipts must represent distinct requests")
    return tuple(receipts)


def qualified_physical_identity_keys(
    qualification: QualificationBinding,
    *,
    _receipt_root_for_test: Path | None = None,
) -> frozenset[str]:
    """Return only device keys backed by two independently verified requests.

    The key set is derived from receipt artifacts rather than declared by the qualification
    binding. A product/function binding may carry receipts for several physical devices, while a
    singleton receipt for one device never qualifies that device and does not invalidate another
    device's complete pair.
    """

    receipts = verify_qualification_receipts(
        qualification,
        _receipt_root_for_test=_receipt_root_for_test,
    )
    counts = Counter(receipt.physical_identity_key for receipt in receipts)
    return frozenset(key for key, count in counts.items() if count >= 2)


def qualification_receipt_bytes(receipt: LocalControlQualificationReceipt) -> bytes:
    return (
        json.dumps(
            receipt.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("ascii")
        + b"\n"
    )


def _read_receipt(path: Path) -> bytes:
    if not hasattr(os, "O_NOFOLLOW"):
        raise QualificationStoreError("O_NOFOLLOW is required for qualification receipts")
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_nlink != 1
            or opened.st_size > _MAX_RECEIPT_BYTES
        ):
            raise QualificationStoreError("qualification receipt artifact metadata is unsafe")
        payload = bytearray()
        while chunk := os.read(descriptor, 65536):
            payload.extend(chunk)
            if len(payload) > _MAX_RECEIPT_BYTES:
                raise QualificationStoreError("qualification receipt artifact is too large")
        named = path.stat(follow_symlinks=False)
        if (
            not stat.S_ISREG(named.st_mode)
            or named.st_uid != os.geteuid()
            or stat.S_IMODE(named.st_mode) != 0o600
            or named.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise QualificationStoreError("qualification receipt changed while reading")
        return bytes(payload)
    except QualificationStoreError:
        raise
    except OSError as error:
        raise QualificationStoreError("qualification receipt artifact is unavailable") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


__all__ = [
    "LocalControlQualificationReceipt",
    "QualificationStoreError",
    "load_current_qualifications",
    "qualified_physical_identity_keys",
    "qualification_receipt_bytes",
    "verify_qualification_receipts",
]
