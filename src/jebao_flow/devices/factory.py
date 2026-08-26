"""Construct physical adapters from validated application configuration."""

from __future__ import annotations

from jebao_flow.config import DeviceConfig, RuntimeConfig
from jebao_flow.devices.identity import PhysicalDeviceBinding, configuration_fingerprint
from jebao_flow.devices.lan import LanJebaoDevice, SessionFactory
from jebao_flow.protocol.session import GizwitsSession


def _physical_binding(
    config: DeviceConfig,
    *,
    product_key: str,
) -> PhysicalDeviceBinding | None:
    identity = config.identity
    if (
        identity is None
        or identity.device_id is None
        or identity.mac_address is None
    ):
        return None
    fingerprint_source = config.model_dump(
        mode="json",
        exclude={"address", "discovery", "name"},
    )
    # A discovered product key is authoritative when observer configuration intentionally omits
    # it.  The resulting binding remains identical when only the DHCP address changes.
    fingerprint_source["product_key"] = product_key
    return PhysicalDeviceBinding.from_identifiers(
        vendor_device_id=identity.device_id,
        mac_address=identity.mac_address,
        product_key=product_key,
        config_fingerprint=configuration_fingerprint(fingerprint_source),
    )


def create_lan_device(
    config: DeviceConfig,
    runtime: RuntimeConfig,
    *,
    session_factory: SessionFactory = GizwitsSession,
) -> LanJebaoDevice:
    if config.address is None:
        raise ValueError(f"device {config.id!r} needs an explicit address before LAN preflight")
    if config.product_key is None:
        raise ValueError(f"device {config.id!r} needs a product_key before LAN preflight")

    control = config.control
    return LanJebaoDevice(
        config.id,
        config.address,
        config.product_key,
        power_limits=config.limits,
        minimum_command_interval_ms=control.minimum_command_interval_ms,
        readback_delay_ms=control.readback_delay_ms,
        readback_attempts=control.readback_attempts,
        allow_hardware_writes=control.allow_hardware_writes and not runtime.dry_run,
        physical_binding=_physical_binding(config, product_key=config.product_key),
        session_factory=session_factory,
    )


def create_read_only_lan_device(
    config: DeviceConfig,
    address: str,
    product_key: str,
    *,
    session_factory: SessionFactory = GizwitsSession,
) -> LanJebaoDevice:
    """Build an observer adapter whose hardware-write gate cannot be opened by config."""

    control = config.control
    return LanJebaoDevice(
        config.id,
        address,
        product_key,
        power_limits=config.limits,
        minimum_command_interval_ms=control.minimum_command_interval_ms,
        readback_delay_ms=control.readback_delay_ms,
        readback_attempts=control.readback_attempts,
        allow_hardware_writes=False,
        physical_binding=_physical_binding(config, product_key=product_key),
        session_factory=session_factory,
    )
