"""Current-observation endpoint resolution for stable local-control bindings.

Addresses are deliberately short-lived transport results. Every resolution performs a new
Gizwits discovery and matches only the configured product and privacy-preserving physical key;
neither an install manifest nor an address cache is accepted by this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.physical_identity import (
    PhysicalDeviceBinding,
    configuration_fingerprint,
    physical_identity_key,
)
from jebao_flow.protocol.discovery import DiscoveryProvider
from jebao_flow.protocol.models import DiscoveredDevice

_RESOLUTION_FINGERPRINT = configuration_fingerprint(
    {"scope": "current-local-control-endpoint-resolution"}
)


class CurrentEndpointResolutionError(RuntimeError):
    """A fresh discovery did not resolve exactly one requested physical controller."""


@dataclass(frozen=True, slots=True)
class ResolvedEndpoint:
    """One current transport location bound to a stable physical identity."""

    target: LocalDeviceBinding
    address: str = field(repr=False)
    wifi_firmware_version: str | None = None
    gizwits_version: str | None = None
    method: str = "current_gizwits_discovery"

    def public_dict(self) -> dict[str, object]:
        return {
            "logical_member": self.target.logical_member,
            "product_key": self.target.product_key,
            "physical_identity_key": self.target.physical_identity_key,
            "method": self.method,
            "address_retained": False,
            "wifi_firmware_version": self.wifi_firmware_version,
            "gizwits_version": self.gizwits_version,
        }


class CurrentEndpointResolver:
    """Resolve bindings from one new discovery result per call."""

    def __init__(
        self,
        discovery: DiscoveryProvider,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("endpoint resolution timeout must be positive")
        self._discovery = discovery
        self._timeout_seconds = timeout_seconds

    async def resolve(self, target: LocalDeviceBinding) -> ResolvedEndpoint:
        devices = await self._discovery.discover(timeout_seconds=self._timeout_seconds)
        return resolve_current_observation(target, devices)

    async def resolve_many(
        self,
        targets: tuple[LocalDeviceBinding, ...],
    ) -> tuple[ResolvedEndpoint, ...]:
        if not targets:
            raise ValueError("endpoint resolution requires at least one target")
        physical = [target.physical_identity_key for target in targets]
        if len(physical) != len(set(physical)):
            raise ValueError("endpoint resolution targets must be physically distinct")
        devices = await self._discovery.discover(timeout_seconds=self._timeout_seconds)
        return tuple(resolve_current_observation(target, devices) for target in targets)


def resolve_current_observation(
    target: LocalDeviceBinding,
    devices: list[DiscoveredDevice],
) -> ResolvedEndpoint:
    """Match one stable binding against only the supplied current observation."""

    matches: list[DiscoveredDevice] = []
    for device in devices:
        if device.product_key != target.product_key or device.mac_address is None:
            continue
        binding = PhysicalDeviceBinding.from_identifiers(
            vendor_device_id=device.device_id,
            mac_address=device.mac_address,
            product_key=device.product_key,
            config_fingerprint=_RESOLUTION_FINGERPRINT,
        )
        if physical_identity_key(binding) == target.physical_identity_key:
            matches.append(device)
    if len(matches) != 1:
        raise CurrentEndpointResolutionError(
            "current discovery must contain exactly one matching physical controller"
        )
    matched = matches[0]
    return ResolvedEndpoint(
        target=target,
        address=matched.address,
        wifi_firmware_version=_ascii_optional(matched.wifi_firmware_version),
        gizwits_version=_ascii_optional(matched.gizwits_version),
    )


def _ascii_optional(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.isascii() or any(ord(character) < 0x20 for character in value):
        raise CurrentEndpointResolutionError("current firmware provenance is not printable ASCII")
    return value


__all__ = [
    "CurrentEndpointResolutionError",
    "CurrentEndpointResolver",
    "ResolvedEndpoint",
    "resolve_current_observation",
]
