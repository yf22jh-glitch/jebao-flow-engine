"""Configuration and runtime models for logical pump groups."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from jebao_flow.safety.limits import PowerLimits

Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]


class PatternKind(StrEnum):
    CONSTANT = "constant"
    SYNC = "sync"
    ANTI_PHASE = "anti_phase"
    LAGOON = "lagoon"
    REEF_CREST = "reef_crest"
    GYRE = "gyre"
    TIDAL_SWELL = "tidal_swell"
    NUTRIENT_TRANSPORT = "nutrient_transport"
    SINE = "sine"
    RANDOM_REEF = "random_reef"
    TIDE = "tide"
    NATIVE = "native"


class GroupState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    FEEDING = "feeding"
    MAINTENANCE = "maintenance"
    DEGRADED = "degraded"
    ERROR = "error"
    EMERGENCY_STOP = "emergency_stop"


class OfflinePolicy(StrEnum):
    STOP_GROUP = "stop_group"
    CONTINUE = "continue"
    CONTINUE_LIMITED = "continue_limited"
    FALLBACK_CONSTANT = "fallback_constant"


class GroupMember(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    device: Identifier
    gain: float = Field(default=1.0, gt=0, le=10)
    phase: float = Field(default=0, ge=0, lt=360)
    invert: bool = False
    enabled: bool = True

    @field_validator("phase", mode="before")
    @classmethod
    def normalize_phase(cls, value: object) -> object:
        if isinstance(value, int | float):
            return value % 360
        return value


class GroupDefaults(PowerLimits):
    pattern: PatternKind = PatternKind.CONSTANT
    power: int = Field(default=50, ge=0, le=100)
    period_seconds: float = Field(default=10, gt=0)
    transition_seconds: float = Field(default=0, ge=0)


class FailurePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    on_member_offline: OfflinePolicy = OfflinePolicy.CONTINUE_LIMITED
    remaining_member_max_power: int = Field(default=50, ge=0, le=100)


class GroupConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: Identifier
    name: str = Field(min_length=1)
    enabled: bool = True
    members: tuple[GroupMember, ...] = Field(min_length=1)
    default: GroupDefaults = Field(default_factory=GroupDefaults)
    failure_policy: FailurePolicy = Field(default_factory=FailurePolicy)

    @model_validator(mode="after")
    def validate_unique_members(self) -> Self:
        member_ids = [member.device for member in self.members]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError(f"group {self.id!r} contains duplicate device members")
        return self


class GroupRuntime(BaseModel):
    """Mutable desired inputs captured as an immutable calculation snapshot."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: GroupState = GroupState.STOPPED
    enabled: bool | None = None
    pattern: PatternKind | None = None
    power: int | None = Field(default=None, ge=0, le=100)
    min_power: int | None = Field(default=None, ge=0, le=100)
    max_power: int | None = Field(default=None, ge=0, le=100)
    period_seconds: float | None = Field(default=None, gt=0)
    started_at: float = 0

    @model_validator(mode="after")
    def validate_runtime_range(self) -> Self:
        if (
            self.min_power is not None
            and self.max_power is not None
            and self.min_power > self.max_power
        ):
            raise ValueError("runtime min_power must not exceed max_power")
        return self
