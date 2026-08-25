"""Protocol-neutral value objects shared with upper layers."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from jebao_flow.safety.limits import PowerLimits


class Capability(StrEnum):
    POWER = "power"
    ENABLED = "enabled"
    MODE = "mode"
    FREQUENCY = "frequency"
    ERROR = "error"


class DeviceCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = "unknown"
    product_key: str | None = None
    readable: frozenset[Capability] = frozenset()
    writable: frozenset[Capability] = frozenset()
    power_limits: PowerLimits = Field(default_factory=PowerLimits)
    power_step: int = Field(default=1, ge=1, le=100)


class DeviceState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    online: bool
    enabled: bool
    power: int = Field(ge=0, le=100)
    mode: str = "constant"
    frequency: int | None = Field(default=None, ge=0, le=100)
    error: str | None = None
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeviceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    power: int = Field(ge=0, le=100)
    mode: str | None = None
    frequency: int | None = Field(default=None, ge=0, le=100)


class DiscoveredDevice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    address: str
    device_id: str
    mac_address: str | None = None
    product_key: str | None = None
    model: str = "unknown"
    wifi_firmware_version: str | None = None
    api_server: str | None = None
    gizwits_version: str | None = None
    mcu_attributes_hex: str = ""
    extra_hex: str = ""
