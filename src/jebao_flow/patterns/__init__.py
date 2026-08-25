from jebao_flow.groups.models import PatternKind
from jebao_flow.patterns.anti_phase import AntiPhasePattern
from jebao_flow.patterns.base import Pattern
from jebao_flow.patterns.constant import ConstantPattern
from jebao_flow.patterns.sync import SyncPattern

_PATTERNS: dict[PatternKind, type[Pattern]] = {
    PatternKind.CONSTANT: ConstantPattern,
    PatternKind.SYNC: SyncPattern,
    PatternKind.ANTI_PHASE: AntiPhasePattern,
}


def create_pattern(kind: PatternKind) -> Pattern:
    try:
        pattern_type = _PATTERNS[kind]
    except KeyError as error:
        raise NotImplementedError(f"pattern {kind.value!r} is not implemented") from error
    return pattern_type()


__all__ = [
    "AntiPhasePattern",
    "ConstantPattern",
    "Pattern",
    "SyncPattern",
    "create_pattern",
]
