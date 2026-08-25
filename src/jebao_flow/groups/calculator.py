"""Pattern selection facade used by the future group manager."""

from __future__ import annotations

from jebao_flow.groups.models import GroupConfig, GroupRuntime, PatternKind
from jebao_flow.patterns import create_pattern
from jebao_flow.protocol.models import DeviceTarget


class PatternCalculator:
    def calculate(
        self,
        timestamp: float,
        group: GroupConfig,
        runtime: GroupRuntime,
    ) -> dict[str, DeviceTarget]:
        kind = runtime.pattern or group.default.pattern
        pattern = create_pattern(kind)
        return pattern.calculate(timestamp, group, runtime)

    @staticmethod
    def supported_patterns() -> frozenset[PatternKind]:
        return frozenset({PatternKind.CONSTANT, PatternKind.SYNC, PatternKind.ANTI_PHASE})

