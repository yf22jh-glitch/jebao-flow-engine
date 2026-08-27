"""Strict YAML configuration models."""

from __future__ import annotations

import os
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from jebao_flow.groups.models import (
    GroupConfig,
    GroupExecutionStrategy,
    Identifier,
    PatternKind,
)
from jebao_flow.safety.limits import PowerLimits

NonEmptyString = Annotated[str, StringConstraints(min_length=1)]
ProductKey = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{32}$")]


class DeviceType(StrEnum):
    WAVEMAKER = "wavemaker"
    RETURN_PUMP = "return_pump"
    DOSING_PUMP = "dosing_pump"


class RuntimeMode(StrEnum):
    """Top-level safety mode for the daemon."""

    OBSERVER = "observer"
    CONTROL = "control"


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
    mode: RuntimeMode = RuntimeMode.OBSERVER
    dry_run: bool = True


class ObserverConfig(BaseModel):
    """Conservative LAN polling settings used by the read-only observer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    journal_path: Path = Path("/data/observations.jsonl")
    targets: tuple[NonEmptyString, ...] = ("255.255.255.255",)
    bind_address: NonEmptyString = "0.0.0.0"
    discovery_timeout_seconds: float = Field(default=3, gt=0, le=30)
    rediscovery_interval_seconds: float = Field(default=30, ge=5, le=3600)
    poll_interval_seconds: float = Field(default=5, ge=1, le=3600)
    publish_heartbeat_seconds: float = Field(default=300, ge=30, le=86400)
    reconnect_initial_seconds: float = Field(default=2, ge=0.1, le=300)
    reconnect_max_seconds: float = Field(default=60, ge=1, le=3600)

    @model_validator(mode="after")
    def validate_backoff(self) -> Self:
        if self.reconnect_initial_seconds > self.reconnect_max_seconds:
            raise ValueError("observer reconnect initial delay must not exceed maximum delay")
        if not self.targets:
            raise ValueError("observer requires at least one discovery target")
        return self


class DeviceIdentityConfig(BaseModel):
    """Stable vendor identity used to bind a logical device after discovery.

    Values belong in the private deployment config. They must not be inferred from product type,
    discovery order, or product key because multiple physical pumps can share those values.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    device_id: NonEmptyString | None = None
    mac_address: NonEmptyString | None = None

    @field_validator("mac_address")
    @classmethod
    def normalize_mac_address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        compact = value.replace(":", "").replace("-", "").lower()
        if len(compact) != 12 or any(character not in "0123456789abcdef" for character in compact):
            raise ValueError("mac_address must contain exactly 12 hexadecimal characters")
        return compact

    @model_validator(mode="after")
    def validate_selector(self) -> Self:
        if self.device_id is None and self.mac_address is None:
            raise ValueError("device identity requires device_id or mac_address")
        return self


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
    identity: DeviceIdentityConfig | None = None
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
    observer: ObserverConfig = Field(default_factory=ObserverConfig)
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
        group_memberships: dict[str, list[str]] = {}
        for group in self.groups:
            missing = {member.device for member in group.members} - known_devices
            if missing:
                missing_list = ", ".join(sorted(missing))
                raise ValueError(f"group {group.id!r} references unknown devices: {missing_list}")
            for member in group.members:
                group_memberships.setdefault(member.device, []).append(group.id)

        repeated_memberships = {
            device_id: group_ids
            for device_id, group_ids in group_memberships.items()
            if len(group_ids) > 1
        }
        if repeated_memberships:
            device_id, memberships = next(iter(sorted(repeated_memberships.items())))
            raise ValueError(
                f"device {device_id!r} belongs to multiple groups: "
                + ", ".join(sorted(memberships))
            )

        devices_by_id = {device.id: device for device in self.devices}
        for group in self.groups:
            pair = group.native_pair
            if pair is None:
                continue
            pair_devices = (devices_by_id[pair.master], devices_by_id[pair.slave])
            if any(device.type is not DeviceType.WAVEMAKER for device in pair_devices):
                raise ValueError(f"group {group.id!r} native pair must contain only wavemakers")
            product_keys = {device.product_key for device in pair_devices}
            if None not in product_keys and len(product_keys) != 1:
                raise ValueError(
                    f"group {group.id!r} native pair must use the same product family"
                )

        vendor_ids = [
            device.identity.device_id
            for device in self.devices
            if device.identity is not None and device.identity.device_id is not None
        ]
        if len(vendor_ids) != len(set(vendor_ids)):
            raise ValueError("device identity device_ids must be unique")
        mac_addresses = [
            device.identity.mac_address
            for device in self.devices
            if device.identity is not None and device.identity.mac_address is not None
        ]
        if len(mac_addresses) != len(set(mac_addresses)):
            raise ValueError("device identity mac_addresses must be unique")

        if self.runtime.mode is RuntimeMode.CONTROL:
            native_groups = [
                group.id
                for group in self.groups
                if group.execution_strategy is GroupExecutionStrategy.NATIVE_LINKED
            ]
            if native_groups:
                raise ValueError(
                    "control mode cannot enable unqualified native-linked groups: "
                    + ", ".join(native_groups)
                )
            # Future pattern names remain part of the public model, but a control-mode
            # deployment must never start with a calculator that does not exist yet.
            from jebao_flow.groups.calculator import PatternCalculator

            supported_patterns = PatternCalculator.supported_patterns()
            unsupported = [
                f"{group.id}:{group.default.pattern.value}"
                for group in self.groups
                if group.default.pattern not in supported_patterns
            ]
            if unsupported:
                raise ValueError(
                    "control mode group defaults use unimplemented patterns: "
                    + ", ".join(unsupported)
                )
        return self


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open(encoding="utf-8") as stream:
        raw = yaml.safe_load(stream)
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")
    return AppConfig.model_validate(raw)
