"""Deterministic random envelopes inspired by common reef-pump modes."""

from __future__ import annotations

import hashlib
import math

from jebao_flow.groups.models import GroupConfig, GroupRuntime
from jebao_flow.patterns.base import (
    Pattern,
    effective_ceiling,
    effective_limits,
    effective_period,
    is_group_enabled,
    member_target,
)
from jebao_flow.protocol.models import DeviceTarget


def _stable_unit(seed: str, step: int) -> float:
    digest = hashlib.sha256(f"{seed}:{step}".encode()).digest()
    return int.from_bytes(digest[:8], "big") / (2**64 - 1)


def _smoothstep(value: float) -> float:
    return value * value * (3 - 2 * value)


class LagoonPattern(Pattern):
    """Slow, gentle, smoothly interpolated changes in the lower output range."""

    def calculate(
        self,
        timestamp: float,
        group: GroupConfig,
        runtime: GroupRuntime,
    ) -> dict[str, DeviceTarget]:
        limits = effective_limits(group, runtime)
        ceiling = effective_ceiling(group, runtime, limits)
        period = effective_period(group, runtime)
        enabled = is_group_enabled(group, runtime)
        elapsed_cycles = (timestamp - runtime.started_at) / period
        step = math.floor(elapsed_cycles)
        blend = _smoothstep(elapsed_cycles - step)
        targets: dict[str, DeviceTarget] = {}

        for member in group.members:
            seed = f"lagoon:{group.id}:{member.device}"
            start = _stable_unit(seed, step)
            end = _stable_unit(seed, step + 1)
            unit = start + (end - start) * blend
            gentle_unit = 0.15 + unit * 0.5
            requested = limits.min_power + (ceiling - limits.min_power) * gentle_unit
            targets[member.device] = member_target(
                requested,
                member,
                limits,
                group_enabled=enabled,
            )
        return targets


class ReefCrestPattern(Pattern):
    """Frequent, larger deterministic changes biased toward high output."""

    def calculate(
        self,
        timestamp: float,
        group: GroupConfig,
        runtime: GroupRuntime,
    ) -> dict[str, DeviceTarget]:
        limits = effective_limits(group, runtime)
        ceiling = effective_ceiling(group, runtime, limits)
        period = effective_period(group, runtime)
        enabled = is_group_enabled(group, runtime)
        step = math.floor((timestamp - runtime.started_at) / period)
        targets: dict[str, DeviceTarget] = {}

        for member in group.members:
            unit = _stable_unit(f"reef-crest:{group.id}:{member.device}", step)
            energetic_unit = 0.35 + unit * 0.65
            requested = limits.min_power + (ceiling - limits.min_power) * energetic_unit
            targets[member.device] = member_target(
                requested,
                member,
                limits,
                group_enabled=enabled,
            )
        return targets
