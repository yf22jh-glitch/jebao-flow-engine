"""Multi-stage, direction-reversing long-period flow envelope."""

from __future__ import annotations

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


def _tidal_intensity(fraction: float) -> float:
    if fraction < 0.4:
        return 0.55 + 0.35 * abs(math.sin(fraction * math.tau * 5))
    if fraction < 0.7:
        return 0.3
    if fraction < 0.9:
        return 0.3 + ((fraction - 0.7) / 0.2) * 0.7
    return 1.0


class TidalSwellPattern(Pattern):
    def calculate(
        self,
        timestamp: float,
        group: GroupConfig,
        runtime: GroupRuntime,
    ) -> dict[str, DeviceTarget]:
        limits = effective_limits(group, runtime)
        upper = effective_ceiling(group, runtime, limits)
        period = effective_period(group, runtime)
        enabled = is_group_enabled(group, runtime)
        elapsed_cycles = (timestamp - runtime.started_at) / period
        cycle = math.floor(elapsed_cycles)
        fraction = elapsed_cycles % 1
        intensity = _tidal_intensity(fraction)
        dominant_side = cycle % 2
        targets: dict[str, DeviceTarget] = {}

        for member in group.members:
            member_side = 0 if member.phase < 180 else 1
            dominance = 1.0 if member_side == dominant_side else 0.5
            scaled = intensity * dominance
            requested = limits.min_power + (upper - limits.min_power) * scaled
            targets[member.device] = member_target(
                requested,
                member,
                limits,
                group_enabled=enabled,
            )
        return targets
