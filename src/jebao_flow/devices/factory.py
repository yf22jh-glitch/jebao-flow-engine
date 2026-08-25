"""Construct physical adapters from validated application configuration."""

from __future__ import annotations

from jebao_flow.config import DeviceConfig, RuntimeConfig
from jebao_flow.devices.lan import LanJebaoDevice, SessionFactory
from jebao_flow.protocol.session import GizwitsSession


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
        session_factory=session_factory,
    )
