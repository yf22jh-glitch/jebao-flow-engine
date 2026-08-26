"""Privacy-preserving physical identity used by durable recovery journals."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Sha256Digest = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]


def _digest(label: str, value: str) -> str:
    return hashlib.sha256(f"jebao-flow:{label}:{value}".encode()).hexdigest()


def normalize_mac_address(value: str) -> str:
    compact = value.replace(":", "").replace("-", "").lower()
    if len(compact) != 12 or any(character not in "0123456789abcdef" for character in compact):
        raise ValueError("mac_address must contain exactly 12 hexadecimal characters")
    return compact


def configuration_fingerprint(value: Mapping[str, Any]) -> str:
    """Hash canonical recovery-relevant configuration without retaining its raw values."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def physical_identity_key(binding: PhysicalDeviceBinding) -> str:
    """Return a stable opaque key for a physical controller across config revisions."""

    encoded = json.dumps(
        {
            "vendor": binding.vendor_device_id_digest,
            "mac": binding.mac_address_digest,
            "product": binding.product_key,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class PhysicalDeviceBinding(BaseModel):
    """Exact, durable binding without exposing raw vendor IDs or MAC addresses.

    The raw identifiers are reduced to domain-separated SHA-256 digests before they enter a
    device adapter or recovery journal.  Recovery can therefore reject a remapped controller
    without publishing private deployment identifiers in status payloads or logs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    vendor_device_id_digest: Sha256Digest
    mac_address_digest: Sha256Digest
    product_key: str = Field(min_length=1)
    config_fingerprint: Sha256Digest

    @classmethod
    def from_identifiers(
        cls,
        *,
        vendor_device_id: str,
        mac_address: str,
        product_key: str,
        config_fingerprint: str,
    ) -> Self:
        if not vendor_device_id:
            raise ValueError("vendor_device_id must not be empty")
        normalized_mac = normalize_mac_address(mac_address)
        return cls(
            vendor_device_id_digest=_digest("vendor-device-id", vendor_device_id),
            mac_address_digest=_digest("mac-address", normalized_mac),
            product_key=product_key,
            config_fingerprint=config_fingerprint,
        )


__all__ = [
    "PhysicalDeviceBinding",
    "configuration_fingerprint",
    "normalize_mac_address",
    "physical_identity_key",
]
