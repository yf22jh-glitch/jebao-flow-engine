"""Base contract and shared pattern calculations."""

from __future__ import annotations

from abc import ABC, abstractmethod

from jebao_flow.groups.models import GroupConfig, GroupMember, GroupRuntime
from jebao_flow.protocol.models import DeviceTarget
from jebao_flow.safety.limits import PowerLimits, clamp_enabled_power


class Pattern(ABC):
    @abstractmethod
    def calculate(
        self,
        timestamp: float,
        group: GroupConfig,
        runtime: GroupRuntime,
    ) -> dict[str, DeviceTarget]: ...


def is_group_enabled(group: GroupConfig, runtime: GroupRuntime) -> bool:
    return group.enabled if runtime.enabled is None else runtime.enabled


def effective_limits(group: GroupConfig, runtime: GroupRuntime) -> PowerLimits:
    return PowerLimits(
        min_power=(
            group.default.min_power if runtime.min_power is None else runtime.min_power
        ),
        max_power=(
            group.default.max_power if runtime.max_power is None else runtime.max_power
        ),
    )


def effective_period(group: GroupConfig, runtime: GroupRuntime) -> float:
    if runtime.period_seconds is None:
        return group.default.period_seconds
    return runtime.period_seconds


def effective_power(group: GroupConfig, runtime: GroupRuntime) -> int:
    return group.default.power if runtime.power is None else runtime.power


def cycle_fraction(
    timestamp: float,
    runtime: GroupRuntime,
    period_seconds: float,
    *,
    phase_degrees: float = 0,
) -> float:
    """Return [0, 1), treating positive phase as a delay."""

    elapsed = timestamp - runtime.started_at
    return ((elapsed / period_seconds) - (phase_degrees / 360)) % 1


def member_target(
    requested_power: float,
    member: GroupMember,
    limits: PowerLimits,
    *,
    group_enabled: bool,
) -> DeviceTarget:
    enabled = group_enabled and member.enabled
    power = clamp_enabled_power(requested_power * member.gain, limits, enabled=enabled)
    return DeviceTarget(enabled=enabled, power=power)
