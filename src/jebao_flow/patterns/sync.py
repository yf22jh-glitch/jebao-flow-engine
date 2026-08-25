from __future__ import annotations

from jebao_flow.groups.models import GroupConfig, GroupRuntime
from jebao_flow.patterns.base import (
    Pattern,
    cycle_fraction,
    effective_limits,
    effective_period,
    is_group_enabled,
    member_target,
)
from jebao_flow.protocol.models import DeviceTarget


class SyncPattern(Pattern):
    def calculate(
        self,
        timestamp: float,
        group: GroupConfig,
        runtime: GroupRuntime,
    ) -> dict[str, DeviceTarget]:
        limits = effective_limits(group, runtime)
        period = effective_period(group, runtime)
        power = (
            limits.max_power
            if cycle_fraction(timestamp, runtime, period) < 0.5
            else limits.min_power
        )
        enabled = is_group_enabled(group, runtime)
        return {
            member.device: member_target(power, member, limits, group_enabled=enabled)
            for member in group.members
        }

