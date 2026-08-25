"""Long-interval alternating directional flow."""

from __future__ import annotations

from jebao_flow.groups.models import GroupConfig, GroupRuntime
from jebao_flow.patterns.base import (
    Pattern,
    cycle_fraction,
    effective_ceiling,
    effective_limits,
    effective_period,
    is_group_enabled,
    member_target,
)
from jebao_flow.protocol.models import DeviceTarget


class GyrePattern(Pattern):
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
        targets: dict[str, DeviceTarget] = {}

        for member in group.members:
            fraction = cycle_fraction(
                timestamp,
                runtime,
                period,
                phase_degrees=member.phase,
            )
            requested = upper if fraction < 0.5 else limits.min_power
            targets[member.device] = member_target(
                requested,
                member,
                limits,
                group_enabled=enabled,
            )
        return targets
