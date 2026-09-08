"""Stable public models for the isolated local-control product path.

Persistent binding is intentionally limited to physical identity, product family and an
operator-attested logical member. Runtime addresses, process/container identifiers and file
metadata are transport provenance and never appear in these models.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

_PUBLIC_TOKEN_RE = re.compile(r"[a-z][a-z0-9_.-]{0,95}\Z")
_LOGICAL_MEMBER_RE = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_PRODUCT_KEY_RE = re.compile(r"[0-9a-f]{32}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")


class CapabilityStage(StrEnum):
    """Evidence maturity for one product-scoped control."""

    UNKNOWN = "unknown"
    APP_STATIC = "app_static"
    OBSERVED = "observed"
    REPRODUCED = "reproduced"
    DIFFERENTIAL = "differential"
    QUALIFIED = "qualified"
    LOCAL_ONLY = "local_only"


class LocalDeviceBinding(BaseModel):
    """Stable device reference safe to retain in product state and MQTT payloads."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    logical_member: str
    product_key: str
    physical_identity_key: str
    logical_role: str
    role_evidence_grade: str = "c"
    role_attested_by: str
    role_attested_at: datetime

    @field_validator("logical_member")
    @classmethod
    def validate_member(cls, value: str) -> str:
        if _LOGICAL_MEMBER_RE.fullmatch(value) is None:
            raise ValueError("logical member must be a bounded lowercase ASCII token")
        return value

    @field_validator("product_key")
    @classmethod
    def validate_product_key(cls, value: str) -> str:
        if _PRODUCT_KEY_RE.fullmatch(value) is None:
            raise ValueError("product key must be lowercase 32-hex")
        return value

    @field_validator("physical_identity_key")
    @classmethod
    def validate_physical_key(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("physical identity key must be lowercase SHA-256")
        return value

    @field_validator("logical_role", "role_attested_by")
    @classmethod
    def validate_ascii_label(cls, value: str) -> str:
        if not value or not value.isascii() or any(ord(character) < 0x20 for character in value):
            raise ValueError("role evidence labels must use printable ASCII")
        return value

    @field_validator("role_evidence_grade")
    @classmethod
    def validate_role_grade(cls, value: str) -> str:
        if value != "c":
            raise ValueError("operator-confirmed logical roles use evidence grade c")
        return value

    @field_validator("role_attested_at")
    @classmethod
    def validate_attested_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("role attestation timestamp must be timezone-aware")
        return value


class QualificationBinding(BaseModel):
    """Version/content-bound authority for exposing one hardware control."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_key: str
    function: str
    app_version: str
    app_catalog_sha256: str
    template_sha256: str
    schema_sha256: str
    firmware_version: str
    receipt_sha256: tuple[str, ...] = Field(min_length=2)

    @field_validator("product_key")
    @classmethod
    def validate_product_key(cls, value: str) -> str:
        if _PRODUCT_KEY_RE.fullmatch(value) is None:
            raise ValueError("qualification product key must be lowercase 32-hex")
        return value

    @field_validator("function")
    @classmethod
    def validate_function(cls, value: str) -> str:
        if _PUBLIC_TOKEN_RE.fullmatch(value) is None:
            raise ValueError("qualification function must be a public ASCII token")
        return value

    @field_validator("app_catalog_sha256", "template_sha256", "schema_sha256")
    @classmethod
    def validate_content_digest(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("qualification content digest must be lowercase SHA-256")
        return value

    @field_validator("receipt_sha256")
    @classmethod
    def validate_receipts(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("qualification receipts must be distinct")
        if any(_SHA256_RE.fullmatch(value) is None for value in values):
            raise ValueError("qualification receipt must be lowercase SHA-256")
        return values

    @field_validator("app_version", "firmware_version")
    @classmethod
    def validate_versions(cls, value: str) -> str:
        if not value or not value.isascii() or any(ord(character) < 0x20 for character in value):
            raise ValueError("qualification versions must use printable ASCII")
        return value


class LocalControlIntent(BaseModel):
    """One idempotently named, stable-device-bound local control request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    request_id: str
    target: LocalDeviceBinding
    function: str
    value: JsonValue = None

    @field_validator("request_id")
    @classmethod
    def validate_request_id(cls, value: str) -> str:
        if _REQUEST_ID_RE.fullmatch(value) is None:
            raise ValueError("request id must be 8..128 ASCII token characters")
        return value

    @field_validator("function")
    @classmethod
    def validate_function(cls, value: str) -> str:
        if _PUBLIC_TOKEN_RE.fullmatch(value) is None:
            raise ValueError("control function must be a public ASCII token")
        return value


class InstallContentBindings(BaseModel):
    """Content identities; timestamps and cache paths deliberately do not participate."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    app_catalog_sha256: str
    protocol_schema_sha256: str
    template_sha256: tuple[str, ...] = ()

    @field_validator("app_catalog_sha256", "protocol_schema_sha256")
    @classmethod
    def validate_required_digest(cls, value: str) -> str:
        if _SHA256_RE.fullmatch(value) is None:
            raise ValueError("install content binding must be lowercase SHA-256")
        return value

    @field_validator("template_sha256")
    @classmethod
    def validate_template_digests(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(values)) != len(values):
            raise ValueError("template content digests must be unique")
        if any(_SHA256_RE.fullmatch(value) is None for value in values):
            raise ValueError("template content digest must be lowercase SHA-256")
        return tuple(sorted(values))


class FreshInstallManifest(BaseModel):
    """A complete current observation; never merged with a previous manifest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    generation: str
    rederived_at: datetime
    method: str = "current_private_config_and_content_sha256"
    devices: tuple[LocalDeviceBinding, ...] = Field(min_length=1)
    content: InstallContentBindings

    @field_validator("generation")
    @classmethod
    def validate_generation(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{32}", value) is None:
            raise ValueError("install generation must be lowercase 128-bit hex")
        return value

    @field_validator("rederived_at")
    @classmethod
    def validate_rederived_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("install rederivation timestamp must be timezone-aware")
        return value

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: str) -> str:
        if _PUBLIC_TOKEN_RE.fullmatch(value) is None:
            raise ValueError("install method must be a public ASCII token")
        return value

    @model_validator(mode="after")
    def validate_device_set(self) -> Self:
        logical = [device.logical_member for device in self.devices]
        physical = [device.physical_identity_key for device in self.devices]
        if len(logical) != len(set(logical)):
            raise ValueError("install logical members must be unique")
        if len(physical) != len(set(physical)):
            raise ValueError("install physical identities must be unique")
        return self


__all__ = [
    "CapabilityStage",
    "FreshInstallManifest",
    "InstallContentBindings",
    "LocalControlIntent",
    "LocalDeviceBinding",
    "QualificationBinding",
]
