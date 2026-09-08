from datetime import UTC, datetime

import pytest

from jebao_flow.local_control.models import LocalDeviceBinding
from jebao_flow.local_control.resolver import (
    CurrentEndpointResolutionError,
    CurrentEndpointResolver,
)
from jebao_flow.physical_identity import (
    PhysicalDeviceBinding,
    configuration_fingerprint,
    physical_identity_key,
)
from jebao_flow.protocol.models import DiscoveredDevice
from jebao_flow.protocol.profiles import LOCAL_WAVEMAKER_PRO


def _device(address: str, *, vendor_id: str = "device-one") -> DiscoveredDevice:
    return DiscoveredDevice(
        address=address,
        device_id=vendor_id,
        mac_address=":".join(("aa", "bb", "cc", "dd", "ee", "ff")),
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        wifi_firmware_version="wifi-current",
        gizwits_version="gagent-current",
    )


def _target(device: DiscoveredDevice | None = None) -> LocalDeviceBinding:
    current = device or _device("first-current-address")
    binding = PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=current.device_id,
        mac_address=current.mac_address or "",
        product_key=current.product_key or "",
        config_fingerprint=configuration_fingerprint({"scope": "test-does-not-bind-key"}),
    )
    return LocalDeviceBinding(
        logical_member="wavemaker_left",
        product_key=LOCAL_WAVEMAKER_PRO.product_key,
        physical_identity_key=physical_identity_key(binding),
        logical_role="left",
        role_attested_by="on_site_operator",
        role_attested_at=datetime(2026, 9, 8, tzinfo=UTC),
    )


class _Discovery:
    def __init__(self, observations: list[list[DiscoveredDevice]]) -> None:
        self.observations = observations
        self.calls = 0

    async def discover(self, *, timeout_seconds: float = 5.0) -> list[DiscoveredDevice]:
        assert timeout_seconds == 2
        result = self.observations[self.calls]
        self.calls += 1
        return result


@pytest.mark.asyncio
async def test_resolver_reobserves_current_address_and_never_publishes_it() -> None:
    discovery = _Discovery(
        [[_device("current-address-one")], [_device("current-address-two")]]
    )
    resolver = CurrentEndpointResolver(discovery, timeout_seconds=2)
    target = _target()

    first = await resolver.resolve(target)
    second = await resolver.resolve(target)

    assert first.address == "current-address-one"
    assert second.address == "current-address-two"
    assert discovery.calls == 2
    assert "current-address" not in str(first.public_dict())
    assert "address" not in repr(first)


@pytest.mark.asyncio
async def test_resolver_rejects_current_absence_or_ambiguity_without_using_cache() -> None:
    target = _target()
    duplicate = _device("current-address-two")
    moved_duplicate = duplicate.model_copy(update={"address": "current-address-three"})
    resolver = CurrentEndpointResolver(
        _Discovery([[duplicate, moved_duplicate]]),
        timeout_seconds=2,
    )

    with pytest.raises(CurrentEndpointResolutionError, match="exactly one"):
        await resolver.resolve(target)


@pytest.mark.asyncio
async def test_resolve_many_uses_one_fresh_observation() -> None:
    left = _device("left-current", vendor_id="left-device")
    right = _device("right-current", vendor_id="right-device").model_copy(
        update={"mac_address": ":".join(("02", "11", "22", "33", "44", "55"))}
    )
    discovery = _Discovery([[left, right]])
    resolver = CurrentEndpointResolver(discovery, timeout_seconds=2)

    resolved = await resolver.resolve_many((_target(left), _target(right)))

    assert [item.address for item in resolved] == ["left-current", "right-current"]
    assert discovery.calls == 1
