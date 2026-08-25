from __future__ import annotations

from jebao_flow.groups.models import GroupConfig, GroupRuntime
from jebao_flow.patterns.base import (
    Pattern,
    effective_limits,
    effective_power,
    is_group_enabled,
    member_target,
)
from jebao_flow.protocol.models import DeviceTarget


class ConstantPattern(Pattern):
    def calculate(
        self,
        timestamp: float,
        group: GroupConfig,
        runtime: GroupRuntime,
    ) -> dict[str, DeviceTarget]:
        del timestamp
        limits = effective_limits(group, runtime)
        power = effective_power(group, runtime)
        enabled = is_group_enabled(group, runtime)
        return {
            member.device: member_target(power, member, limits, group_enabled=enabled)
            for member in group.members
        }

