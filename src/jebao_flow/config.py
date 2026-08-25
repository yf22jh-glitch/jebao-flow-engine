"""Strict YAML configuration models."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from jebao_flow.groups.models import GroupConfig, Identifier, PatternKind
from jebao_flow.safety.limits import PowerLimits

NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
ProductKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]


class DeviceType(StrEnum):
    WAVEMAKER = "wavemaker"
    RETURN_PUMP = "return_pump"


class InstanceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Identifier
    name: NonEmptyString
    timezone: NonEmptyString = "UTC"


class MqttConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    host: NonEmptyString
    port: int = Field(default=1883, ge=1, le=65535)
    username: str | None = None
    password_env: NonEmptyString | None = None
    discovery_prefix: NonEmptyString = "homeassistant"
    topic_prefix: NonEmptyString

    def resolve_password(self) -> str | None:
        if self.password_env is None:
            return None
        return os.environ.get(self.password_env)


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state_path: Path = Path("/data/state.json")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    dry_run: bool = False


class DeviceControlConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimum_command_interval_ms: int = Field(default=1000, ge=100)
    restore_on_reconnect: bool = True
    allow_hardware_writes: bool = False
    readback_delay_ms: int = Field(default=500, ge=0)
    readback_attempts: int = Field(default=3, ge=1, le=10)


class DeviceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Identifier
    name: NonEmptyString
    type: DeviceType
    model: str = "unknown"
    product_key: ProductKey | None = None
    address: str | None = None
    discovery: Literal["auto"] | None = "auto"
    enabled: bool = True
    limits: PowerLimits = Field(default_factory=PowerLimits)
    control: DeviceControlConfig = Field(default_factory=DeviceControlConfig)

    @model_validator(mode="after")
    def validate_location(self) -> Self:
        if self.address is None and self.discovery is None:
            raise ValueError("a device requires address or discovery: auto")
        return self


class FeedModeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    duration_seconds: int = Field(default=600, gt=0)
    wavemaker_power: int = Field(default=0, ge=0, le=100)
    return_pump_power: int = Field(default=30, ge=0, le=100)
    restore_previous_state: bool = True
    restore_transition_seconds: int = Field(default=30, ge=0)


class NightModeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_power: int = Field(default=50, ge=0, le=100)
    pattern: PatternKind = PatternKind.CONSTANT


class ModesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    feed: FeedModeConfig = Field(default_factory=FeedModeConfig)
    night: NightModeConfig = Field(default_factory=NightModeConfig)


class AppConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instance: InstanceConfig
    mqtt: MqttConfig
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    devices: tuple[DeviceConfig, ...] = Field(min_length=1)
    groups: tuple[GroupConfig, ...] = Field(default=())
    modes: ModesConfig = Field(default_factory=ModesConfig)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        device_ids = [device.id for device in self.devices]
        if len(device_ids) != len(set(device_ids)):
            raise ValueError("device ids must be unique")

        group_ids = [group.id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("group ids must be unique")

        known_devices = set(device_ids)
        for group in self.groups:
            missing = {member.device for member in group.members} - known_devices
            if missing:
                missing_list = ", ".join(sorted(missing))
                raise ValueError(f"group {group.id!r} references unknown devices: {missing_list}")
        return self


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    return AppConfig.model_validate(raw)
