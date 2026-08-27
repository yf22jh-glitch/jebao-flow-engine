import pytest

from jebao_flow.groups.calculator import PatternCalculator
from jebao_flow.groups.models import (
    GroupConfig,
    GroupDefaults,
    GroupExecutionStrategy,
    GroupMember,
    GroupRuntime,
    GroupState,
    NativeLinkageRelation,
    NativePairConfig,
    PatternKind,
)
from jebao_flow.patterns import (
    AntiPhasePattern,
    ConstantPattern,
    GyrePattern,
    LagoonPattern,
    NutrientTransportPattern,
    ReefCrestPattern,
    SyncPattern,
    TidalSwellPattern,
)


def make_group(*, pattern: PatternKind = PatternKind.CONSTANT) -> GroupConfig:
    return GroupConfig(
        id="main_flow",
        name="Main flow",
        members=(
            GroupMember(device="left", gain=1.0, phase=0),
            GroupMember(device="right", gain=0.85, phase=180),
        ),
        default=GroupDefaults(
            pattern=pattern,
            power=70,
            min_power=35,
            max_power=75,
            period_seconds=8,
        ),
    )


def test_constant_applies_member_gain() -> None:
    targets = ConstantPattern().calculate(123, make_group(), GroupRuntime())

    assert targets["left"].power == 70
    assert targets["right"].power == 60


def test_constant_clamps_after_gain() -> None:
    runtime = GroupRuntime(power=35)

    targets = ConstantPattern().calculate(0, make_group(), runtime)

    assert targets["left"].power == 35
    assert targets["right"].power == 35


def test_disabled_group_generates_explicit_off_targets() -> None:
    targets = ConstantPattern().calculate(0, make_group(), GroupRuntime(enabled=False))

    assert all(not target.enabled and target.power == 0 for target in targets.values())


def test_anti_phase_switches_members_halfway_through_cycle() -> None:
    group = make_group(pattern=PatternKind.ANTI_PHASE)
    runtime = GroupRuntime(state=GroupState.RUNNING, started_at=100)
    pattern = AntiPhasePattern()

    phase_a = pattern.calculate(100, group, runtime)
    phase_b = pattern.calculate(104, group, runtime)
    next_cycle = pattern.calculate(108, group, runtime)

    assert (phase_a["left"].power, phase_a["right"].power) == (75, 35)
    assert (phase_b["left"].power, phase_b["right"].power) == (35, 64)
    assert next_cycle == phase_a


def test_sync_ignores_member_phase_but_preserves_gain() -> None:
    group = make_group(pattern=PatternKind.SYNC)
    pattern = SyncPattern()

    high = pattern.calculate(0, group, GroupRuntime())
    low = pattern.calculate(4, group, GroupRuntime())

    assert (high["left"].power, high["right"].power) == (75, 64)
    assert (low["left"].power, low["right"].power) == (35, 35)


def test_lagoon_is_deterministic_smooth_and_lower_energy() -> None:
    group = make_group(pattern=PatternKind.LAGOON)
    runtime = GroupRuntime(started_at=100)
    pattern = LagoonPattern()

    start = pattern.calculate(100, group, runtime)
    middle = pattern.calculate(104, group, runtime)
    repeat = pattern.calculate(104, group, runtime)

    assert middle == repeat
    assert all(35 <= target.power <= 61 for target in middle.values())
    assert start != middle


def test_reef_crest_changes_by_period_and_stays_in_limits() -> None:
    group = make_group(pattern=PatternKind.REEF_CREST)
    pattern = ReefCrestPattern()

    first = pattern.calculate(0, group, GroupRuntime())
    same_step = pattern.calculate(7.9, group, GroupRuntime())
    next_step = pattern.calculate(8, group, GroupRuntime())

    assert first == same_step
    assert first != next_step
    assert all(35 <= target.power <= 75 for target in next_step.values())


def test_gyre_uses_requested_ceiling_and_member_phase() -> None:
    group = make_group(pattern=PatternKind.GYRE)
    runtime = GroupRuntime(power=65)
    pattern = GyrePattern()

    first = pattern.calculate(0, group, runtime)
    second = pattern.calculate(4, group, runtime)

    assert (first["left"].power, first["right"].power) == (65, 35)
    assert (second["left"].power, second["right"].power) == (35, 55)


def test_tidal_swell_reverses_dominant_side_each_cycle() -> None:
    group = make_group(pattern=PatternKind.TIDAL_SWELL)
    pattern = TidalSwellPattern()

    first_cycle = pattern.calculate(7.6, group, GroupRuntime())
    next_cycle = pattern.calculate(15.6, group, GroupRuntime())

    assert first_cycle["left"].power > first_cycle["right"].power
    assert next_cycle["right"].power > next_cycle["left"].power


def test_nutrient_transport_moves_from_opposed_wave_to_group_surge() -> None:
    group = make_group(pattern=PatternKind.NUTRIENT_TRANSPORT)
    pattern = NutrientTransportPattern()

    suspension = pattern.calculate(0, group, GroupRuntime())
    transport = pattern.calculate(6, group, GroupRuntime())

    assert suspension["left"].power > suspension["right"].power
    assert transport["left"].power > 35
    assert transport["right"].power > 35


def test_native_linked_group_never_enters_ordinary_pattern_calculator() -> None:
    group = make_group().model_copy(
        update={
            "execution_strategy": GroupExecutionStrategy.NATIVE_LINKED,
            "native_pair": NativePairConfig(
                master="left",
                slave="right",
                relation=NativeLinkageRelation.ASYNC,
            ),
        }
    )

    with pytest.raises(ValueError, match="guarded native actuator"):
        PatternCalculator().calculate(0, group, GroupRuntime())
