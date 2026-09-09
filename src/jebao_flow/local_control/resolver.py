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


@dataclass(frozen=True, slots=True)
class CurrentEndpointObservation:
    """One target's outcome from a shared current discovery observation."""

    target: LocalDeviceBinding
    endpoint: ResolvedEndpoint | None = field(default=None, repr=False)
    diagnostic_reason: str | None = None

    def __post_init__(self) -> None:
        if self.endpoint is None:
            if self.diagnostic_reason not in {
                "current_endpoint_absent",
                "current_endpoint_ambiguous",
            }:
                raise ValueError("unresolved endpoint observation requires a bounded reason")
            return
        if self.diagnostic_reason is not None:
            raise ValueError("resolved endpoint observation cannot carry a diagnostic reason")


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
        _validate_targets(targets)
        devices = await self._discovery.discover(timeout_seconds=self._timeout_seconds)
        return tuple(resolve_current_observation(target, devices) for target in targets)

    async def observe_many(
        self,
        targets: tuple[LocalDeviceBinding, ...],
    ) -> tuple[CurrentEndpointObservation, ...]:
        """Classify each target independently from exactly one current discovery."""

        _validate_targets(targets)
        devices = await self._discovery.discover(timeout_seconds=self._timeout_seconds)
        return tuple(classify_current_observation(target, devices) for target in targets)


def resolve_current_observation(
    target: LocalDeviceBinding,
    devices: list[DiscoveredDevice],
) -> ResolvedEndpoint:
    """Match one stable binding against only the supplied current observation."""

    observation = classify_current_observation(target, devices)
    if observation.endpoint is None:
        raise CurrentEndpointResolutionError(
            "current discovery must contain exactly one matching physical controller"
        )
    return observation.endpoint


def classify_current_observation(
    target: LocalDeviceBinding,
    devices: list[DiscoveredDevice],
) -> CurrentEndpointObservation:
    """Classify one stable binding without collapsing absence and ambiguity."""

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
    if not matches:
        return CurrentEndpointObservation(
            target=target,
            diagnostic_reason="current_endpoint_absent",
        )
    if len(matches) > 1:
        return CurrentEndpointObservation(
            target=target,
            diagnostic_reason="current_endpoint_ambiguous",
        )
    matched = matches[0]
    return CurrentEndpointObservation(
        target=target,
        endpoint=ResolvedEndpoint(
            target=target,
            address=matched.address,
            wifi_firmware_version=_ascii_optional(matched.wifi_firmware_version),
            gizwits_version=_ascii_optional(matched.gizwits_version),
        ),
    )


def _validate_targets(targets: tuple[LocalDeviceBinding, ...]) -> None:
    if not targets:
        raise ValueError("endpoint resolution requires at least one target")
    physical = [target.physical_identity_key for target in targets]
    if len(physical) != len(set(physical)):
        raise ValueError("endpoint resolution targets must be physically distinct")


def _ascii_optional(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.isascii() or any(ord(character) < 0x20 for character in value):
        raise CurrentEndpointResolutionError("current firmware provenance is not printable ASCII")
    return value


__all__ = [
    "CurrentEndpointObservation",
    "CurrentEndpointResolutionError",
    "CurrentEndpointResolver",
    "ResolvedEndpoint",
    "classify_current_observation",
    "resolve_current_observation",
]
