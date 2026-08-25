"""Two-stage detritus suspension and transport envelope."""

from __future__ import annotations

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


class NutrientTransportPattern(Pattern):
    """Coordinate slow group envelopes; native device mode supplies any fast wave motion."""

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
        fraction = ((timestamp - runtime.started_at) / period) % 1
        targets: dict[str, DeviceTarget] = {}

        for member in group.members:
            if fraction < 0.5:
                wave_fraction = (fraction * 8 - (member.phase / 360)) % 1
                requested = upper if wave_fraction < 0.5 else limits.min_power
            else:
                surge = (fraction - 0.5) * 2
                requested = limits.min_power + (upper - limits.min_power) * (0.6 + 0.4 * surge)
            targets[member.device] = member_target(
                requested,
                member,
                limits,
                group_enabled=enabled,
            )
        return targets
