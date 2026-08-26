"""Protocol-neutral value objects shared with upper layers."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from jebao_flow.safety.limits import PowerLimits

ScheduleSlotIndex = Annotated[int, Field(ge=0, lt=48)]
ScheduleStartTime = Annotated[
    str,
    StringConstraints(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$"),
]
ScheduleEndTime = Annotated[
    str,
    StringConstraints(pattern=r"^(?:(?:[01][0-9]|2[0-3]):[0-5][0-9]|24:00)$"),
]


class Capability(StrEnum):
    POWER = "power"
    ENABLED = "enabled"
    MODE = "mode"
    FREQUENCY = "frequency"
    LINKAGE = "linkage"
    TIMER = "timer"
    ERROR = "error"


class LinkageRole(StrEnum):
    """Native controller role used for Jebao master/slave wave timing."""

    INDEPENDENT = "independent"
    MASTER = "master"
    SLAVE = "slave"
    SYNC_SLAVE = "sync_slave"
    ASYNC_SLAVE = "async_slave"


class DeviceCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str = "unknown"
    product_key: str | None = None
    readable: frozenset[Capability] = frozenset()
    writable: frozenset[Capability] = frozenset()
    power_limits: PowerLimits = Field(default_factory=PowerLimits)
    power_step: int = Field(default=1, ge=1, le=100)
    native_modes: frozenset[str] = frozenset()
    linkage_roles: frozenset[LinkageRole] = frozenset()


class ScheduleEntry(BaseModel):
    """One decoded device-local timer slot with product-specific parameters."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: ScheduleSlotIndex
    start: ScheduleStartTime
    end: ScheduleEndTime
    mode: str = Field(min_length=1)
    mode_code: int = Field(ge=0, le=255)
    parameters: dict[str, int | bool] = Field(default_factory=dict)


class DeviceSchedule(BaseModel):
    """Read-only view of the controller's 48-slot wall-clock schedule."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool
    device_local_time: datetime | None = None
    slot_capacity: Literal[48] = 48
    entries: tuple[ScheduleEntry, ...] = ()
    invalid_slots: tuple[ScheduleSlotIndex, ...] = ()


class DeviceState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    online: bool
    enabled: bool
    power: int = Field(ge=0, le=100)
    mode: str = "constant"
    frequency: int | None = Field(default=None, ge=0, le=100)
    linkage: LinkageRole | None = None
    timer_enabled: bool | None = None
    error: str | None = None
    schedule: DeviceSchedule | None = None
    observed_attributes: dict[str, bool | int | float | str | None] = Field(
        default_factory=dict
    )
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DeviceTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    power: int = Field(ge=0, le=100)
    mode: str | None = None
    frequency: int | None = Field(default=None, ge=0, le=100)
    linkage: LinkageRole | None = None
    timer_enabled: bool | None = None


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
