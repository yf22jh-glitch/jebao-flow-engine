"""Reusable output-limit primitives."""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PowerLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    min_power: int = Field(default=30, ge=0, le=100)
    max_power: int = Field(default=100, ge=0, le=100)

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.min_power > self.max_power:
            raise ValueError("min_power must not exceed max_power")
        return self


def clamp(value: float, lower: float, upper: float) -> float:
    if lower > upper:
        raise ValueError("lower limit must not exceed upper limit")
    return max(lower, min(value, upper))


def clamp_enabled_power(value: float, limits: PowerLimits, *, enabled: bool = True) -> int:
    """Clamp power using conventional half-up rounding.

    A disabled target stays at zero instead of being raised to ``min_power``.
    """

    if not enabled:
        return 0
    bounded = clamp(value, limits.min_power, limits.max_power)
    return int(bounded + 0.5)

