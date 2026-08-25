"""Versioned JSON messages exchanged with Home Assistant."""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from jebao_flow.config import DeviceType
from jebao_flow.groups.models import GroupMemberRole, GroupState, PatternKind


class GroupAction(StrEnum):
    START_FEED = "start_feed"
    STOP_FEED = "stop_feed"
    EMERGENCY_STOP = "emergency_stop"
    CLEAR_EMERGENCY = "clear_emergency"
    RESUME_ALL_MEMBERS = "resume_all_members"


class DeviceAction(StrEnum):
    RESUME_GROUP = "resume_group"


class DeviceControlMode(StrEnum):
    GROUP = "group"
    MANUAL_OVERRIDE = "manual_override"
    STANDALONE = "standalone"


class GroupCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    source: str = Field(default="home_assistant", min_length=1, max_length=64)
    enabled: bool | None = None
    pattern: PatternKind | None = None
    power: int | None = Field(default=None, ge=0, le=100)
    min_power: int | None = Field(default=None, ge=0, le=100)
    max_power: int | None = Field(default=None, ge=0, le=100)
    period_seconds: float | None = Field(default=None, ge=1, le=86400)
    transition_seconds: float | None = Field(default=None, ge=0, le=3600)
    action: GroupAction | None = None

    @model_validator(mode="after")
    def validate_change(self) -> Self:
        changed = any(
            value is not None
            for name, value in self.__dict__.items()
            if name not in {"request_id", "source"}
        )
        if not changed:
            raise ValueError("group command must contain at least one change or action")
        return self


class GroupMemberState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: GroupMemberRole
    gain: float = Field(gt=0, le=10)
    phase: float = Field(ge=0, lt=360)
    control_mode: DeviceControlMode = DeviceControlMode.GROUP
    enabled: bool
    target_power: int = Field(ge=0, le=100)
    actual_enabled: bool | None = None
    actual_power: int | None = Field(default=None, ge=0, le=100)
    online: bool | None = None


class GroupStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    revision: int = Field(ge=0)
    group_id: str
    name: str
    status: GroupState
    enabled: bool
    pattern: PatternKind
    power: int = Field(ge=0, le=100)
    min_power: int = Field(ge=0, le=100)
    max_power: int = Field(ge=0, le=100)
    period_seconds: float = Field(ge=1, le=86400)
    transition_seconds: float = Field(ge=0, le=3600)
    hardware_writes_locked: bool
    members: dict[str, GroupMemberState]
    last_request_id: str | None = None


class GroupCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    request_id: str
    group_id: str
    accepted: bool
    revision: int = Field(ge=0)
    reason: str | None = None


class DeviceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    source: str = Field(default="home_assistant", min_length=1, max_length=64)
    enabled: bool | None = None
    power: int | None = Field(default=None, ge=0, le=100)
    action: DeviceAction | None = None

    @model_validator(mode="after")
    def validate_change(self) -> Self:
        if self.enabled is None and self.power is None and self.action is None:
            raise ValueError("device command must contain at least one change or action")
        return self


class DeviceStatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    revision: int = Field(ge=0)
    device_id: str
    name: str
    type: DeviceType
    enabled: bool
    power: int = Field(ge=0, le=100)
    actual_enabled: bool | None = None
    actual_power: int | None = Field(default=None, ge=0, le=100)
    online: bool | None = None
    status: str
    control_mode: DeviceControlMode
    group_ids: tuple[str, ...]
    hardware_writes_locked: bool
    last_request_id: str | None = None


class DeviceCommandResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    request_id: str
    device_id: str
    accepted: bool
    revision: int = Field(ge=0)
    reason: str | None = None


class GroupDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class DeviceDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    type: DeviceType
    grouped: bool
    ui: str
    controls: tuple[str, ...]
    min_power: int = Field(ge=0, le=100)
    max_power: int = Field(ge=0, le=100)


class SystemConfigPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    instance_id: str
    name: str
    groups: tuple[GroupDescriptor, ...]
    devices: tuple[DeviceDescriptor, ...]
    patterns: tuple[PatternKind, ...]
    features: tuple[str, ...] = (
        "feed",
        "emergency_stop",
        "hardware_write_lock",
        "individual_override",
    )
