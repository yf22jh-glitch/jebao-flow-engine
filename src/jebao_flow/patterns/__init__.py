from jebao_flow.groups.models import PatternKind
from jebao_flow.patterns.anti_phase import AntiPhasePattern
from jebao_flow.patterns.base import Pattern
from jebao_flow.patterns.constant import ConstantPattern
from jebao_flow.patterns.gyre import GyrePattern
from jebao_flow.patterns.nutrient_transport import NutrientTransportPattern
from jebao_flow.patterns.randomized import LagoonPattern, ReefCrestPattern
from jebao_flow.patterns.sync import SyncPattern
from jebao_flow.patterns.tidal_swell import TidalSwellPattern

_PATTERNS: dict[PatternKind, type[Pattern]] = {
    PatternKind.CONSTANT: ConstantPattern,
    PatternKind.SYNC: SyncPattern,
    PatternKind.ANTI_PHASE: AntiPhasePattern,
    PatternKind.LAGOON: LagoonPattern,
    PatternKind.REEF_CREST: ReefCrestPattern,
    PatternKind.GYRE: GyrePattern,
    PatternKind.TIDAL_SWELL: TidalSwellPattern,
    PatternKind.NUTRIENT_TRANSPORT: NutrientTransportPattern,
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
    "GyrePattern",
    "LagoonPattern",
    "NutrientTransportPattern",
    "Pattern",
    "ReefCrestPattern",
    "SyncPattern",
    "TidalSwellPattern",
    "create_pattern",
]
