from jebao_flow.groups.models import (
    GroupConfig,
    GroupDefaults,
    GroupMember,
    GroupRuntime,
    GroupState,
    PatternKind,
)
from jebao_flow.patterns import AntiPhasePattern, ConstantPattern, SyncPattern


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

