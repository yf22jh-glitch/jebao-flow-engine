from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jebao_flow.groups.manager import GroupManager
from jebao_flow.groups.models import (
    FailurePolicy,
    GroupConfig,
    GroupDefaults,
    GroupExecutionStrategy,
    GroupMember,
    GroupRuntime,
    GroupState,
    NativeLinkageRelation,
    NativePairConfig,
    OfflinePolicy,
    PatternKind,
)
from jebao_flow.groups.plan import (
    DeviceActuationLimits,
    GroupPlanningError,
    GroupTickInput,
    MemberAction,
    MemberStatus,
    PatternEpoch,
    plan_group_tick,
)


def _group(
    *,
    pattern: PatternKind = PatternKind.ANTI_PHASE,
    policy: OfflinePolicy = OfflinePolicy.CONTINUE_LIMITED,
) -> GroupConfig:
    return GroupConfig(
        id="main_flow",
        name="Main flow",
        members=(
            GroupMember(device="left", gain=1.0, phase=0),
            GroupMember(device="right", gain=0.85, phase=180),
            GroupMember(device="crossflow", gain=0.7, phase=90),
        ),
        default=GroupDefaults(
            pattern=pattern,
            power=65,
            min_power=35,
            max_power=75,
            period_seconds=8,
        ),
        failure_policy=FailurePolicy(
            on_member_offline=policy,
            remaining_member_max_power=50,
        ),
    )


def _limits(
    *,
    left: DeviceActuationLimits | None = None,
) -> dict[str, DeviceActuationLimits]:
    return {
        "left": left or DeviceActuationLimits(min_power=30, max_power=80),
        "right": DeviceActuationLimits(min_power=30, max_power=75),
        "crossflow": DeviceActuationLimits(min_power=20, max_power=60),
    }


def _ready(manager: GroupManager) -> None:
    manager.update_availability({"left": True, "right": True, "crossflow": True})


def _decisions(plan):
    return {decision.device: decision for decision in plan.decisions}


def test_three_member_tick_uses_one_timestamp_and_gain_phase() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)

    first = manager.plan_tick(now=100.0)
    second = manager.plan_tick(now=104.0)

    assert first.timestamp == 100.0
    assert {decision.device for decision in first.decisions} == {
        "left",
        "right",
        "crossflow",
    }
    assert tuple(decision.action for decision in first.decisions) == (
        MemberAction.DISPATCH,
        MemberAction.DISPATCH,
        MemberAction.DISPATCH,
    )
    assert tuple(decision.normalized_power for decision in first.decisions) == (75, 35, 35)
    assert tuple(decision.normalized_power for decision in second.decisions) == (35, 64, 53)


def test_pattern_and_period_changes_restart_epoch_but_power_change_does_not() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    started_at = manager.epoch.started_at

    manager.update_runtime(GroupRuntime(state=GroupState.RUNNING, power=55), now=101.0)
    assert manager.epoch.started_at == started_at

    manager.update_runtime(
        GroupRuntime(state=GroupState.RUNNING, pattern=PatternKind.SYNC, power=55),
        now=102.0,
    )
    assert manager.epoch.started_at == 102.0

    manager.update_runtime(
        GroupRuntime(
            state=GroupState.RUNNING,
            pattern=PatternKind.SYNC,
            period_seconds=12,
            power=55,
        ),
        now=103.0,
    )
    assert manager.epoch.started_at == 103.0


def test_off_to_on_restarts_epoch_and_stales_an_existing_plan() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)
    old_plan = manager.plan_tick(now=100.0)

    manager.update_runtime(GroupRuntime(state=GroupState.STOPPED, enabled=False), now=101.0)
    manager.update_runtime(GroupRuntime(state=GroupState.RUNNING, enabled=True), now=102.0)

    assert manager.epoch.started_at == 102.0
    assert not manager.is_current(old_plan.generation)


@pytest.mark.parametrize("held_state", [GroupState.STARTING, GroupState.MAINTENANCE])
def test_waveform_inactive_to_active_state_change_restarts_epoch(
    held_state: GroupState,
) -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)

    manager.update_runtime(GroupRuntime(state=held_state, enabled=True), now=101.0)
    manager.update_runtime(GroupRuntime(state=GroupState.RUNNING, enabled=True), now=102.0)

    assert manager.epoch.started_at == 102.0


def test_state_only_stopped_to_running_restarts_epoch() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)

    manager.update_runtime(GroupRuntime(state=GroupState.STOPPED), now=101.0)
    manager.update_runtime(GroupRuntime(state=GroupState.RUNNING), now=102.0)

    assert manager.epoch.started_at == 102.0


@pytest.mark.parametrize(
    ("state", "expected_action", "expected_status"),
    [
        (GroupState.STOPPED, MemberAction.DISPATCH, GroupState.STOPPED),
        (GroupState.STARTING, MemberAction.SKIP_HELD, GroupState.STARTING),
        (GroupState.RUNNING, MemberAction.DISPATCH, GroupState.RUNNING),
        (GroupState.FEEDING, MemberAction.DISPATCH, GroupState.FEEDING),
        (GroupState.MAINTENANCE, MemberAction.SKIP_HELD, GroupState.MAINTENANCE),
        (GroupState.DEGRADED, MemberAction.DISPATCH, GroupState.DEGRADED),
        (GroupState.ERROR, MemberAction.DISPATCH, GroupState.ERROR),
        (GroupState.EMERGENCY_STOP, MemberAction.DISPATCH, GroupState.EMERGENCY_STOP),
    ],
)
def test_group_state_action_table(
    state: GroupState,
    expected_action: MemberAction,
    expected_status: GroupState,
) -> None:
    manager = GroupManager(_group(pattern=PatternKind.CONSTANT), _limits(), clock=lambda: 100.0)
    _ready(manager)
    manager.update_runtime(GroupRuntime(state=state, enabled=True), now=100.0)

    plan = manager.plan_tick(now=100.0)

    assert all(decision.action is expected_action for decision in plan.decisions)
    assert plan.derived_status is expected_status
    if state in {GroupState.STOPPED, GroupState.EMERGENCY_STOP}:
        assert all(
            decision.target is not None
            and not decision.target.enabled
            and decision.target.power == 0
            for decision in plan.decisions
        )


def test_group_off_overrides_manual_ownership_with_explicit_off_for_every_member() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)
    initial_epoch = _decisions(manager.plan_tick(now=100.0))["right"].control_epoch
    manager.set_manual_override("right")
    manager.update_runtime(GroupRuntime(state=GroupState.STOPPED, enabled=False), now=101.0)

    plan = manager.plan_tick(now=101.0)

    assert len(plan.dispatch_decisions) == 3
    assert all(
        decision.target is not None and not decision.target.enabled and decision.target.power == 0
        for decision in plan.decisions
    )
    assert _decisions(plan)["right"].control_epoch > initial_epoch


def test_manual_override_is_skipped_and_resume_rejoins_without_restarting_phase() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)
    started_at = manager.epoch.started_at
    initial_control_epoch = _decisions(manager.plan_tick(now=100.0))["right"].control_epoch
    manager.set_manual_override("right")

    overridden = manager.plan_tick(now=101.0)
    manager.resume_member("right")
    resumed = manager.plan_tick(now=102.0)

    assert _decisions(overridden)["right"].action is MemberAction.SKIP_MANUAL_OVERRIDE
    assert _decisions(overridden)["right"].control_epoch > initial_control_epoch
    assert _decisions(resumed)["right"].action is MemberAction.DISPATCH
    assert (
        _decisions(resumed)["right"].control_epoch > _decisions(overridden)["right"].control_epoch
    )
    assert manager.epoch.started_at == started_at


def test_resume_without_intervening_plan_still_changes_control_epoch() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)
    before = _decisions(manager.plan_tick(now=100.0))["right"]

    manager.set_manual_override("right")
    manager.resume_member("right")
    resumed = _decisions(manager.plan_tick(now=101.0))["right"]

    assert resumed.action is MemberAction.DISPATCH
    assert resumed.control_epoch > before.control_epoch


def test_offline_online_without_intervening_plan_still_changes_control_epoch() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)
    before = _decisions(manager.plan_tick(now=100.0))["right"]

    manager.update_availability({"right": False})
    manager.update_availability({"right": True})
    reconnected = _decisions(manager.plan_tick(now=101.0))["right"]

    assert reconnected.action is MemberAction.DISPATCH
    assert reconnected.control_epoch > before.control_epoch


def test_emergency_stop_advances_every_member_control_epoch() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)
    before = _decisions(manager.plan_tick(now=100.0))

    manager.update_runtime(GroupRuntime(state=GroupState.EMERGENCY_STOP), now=101.0)
    emergency = _decisions(manager.plan_tick(now=101.0))

    assert all(
        emergency[device_id].control_epoch > before[device_id].control_epoch for device_id in before
    )


def test_stopped_to_emergency_reasserts_every_member_control_epoch() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)
    manager.update_runtime(GroupRuntime(state=GroupState.STOPPED), now=101.0)
    stopped = _decisions(manager.plan_tick(now=101.0))

    manager.update_runtime(GroupRuntime(state=GroupState.EMERGENCY_STOP), now=102.0)
    emergency = _decisions(manager.plan_tick(now=102.0))

    assert all(
        emergency[device_id].control_epoch > stopped[device_id].control_epoch
        for device_id in stopped
    )


def test_repeated_emergency_stop_keeps_the_same_control_epoch() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)
    manager.update_runtime(GroupRuntime(state=GroupState.EMERGENCY_STOP), now=101.0)
    first = _decisions(manager.plan_tick(now=101.0))

    manager.update_runtime(GroupRuntime(state=GroupState.EMERGENCY_STOP), now=102.0)
    repeated = _decisions(manager.plan_tick(now=102.0))

    assert all(
        repeated[device_id].control_epoch == first[device_id].control_epoch for device_id in first
    )


def test_emergency_stop_does_not_depend_on_waveform_arithmetic() -> None:
    group = _group(pattern=PatternKind.LAGOON).model_copy(
        update={
            "default": GroupDefaults(
                pattern=PatternKind.LAGOON,
                power=65,
                min_power=35,
                max_power=75,
                period_seconds=5e-324,
            )
        }
    )
    manager = GroupManager(group, _limits(), clock=lambda: 0.0)
    _ready(manager)
    manager.update_runtime(GroupRuntime(state=GroupState.EMERGENCY_STOP), now=1.0)

    plan = manager.plan_tick(now=1.0)

    assert all(
        decision.action is MemberAction.DISPATCH
        and decision.target is not None
        and not decision.target.enabled
        and decision.target.power == 0
        for decision in plan.decisions
    )


def test_emergency_stop_accepts_an_irrelevant_unimplemented_pattern() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)

    manager.update_runtime(
        GroupRuntime(state=GroupState.EMERGENCY_STOP, pattern=PatternKind.SINE),
        now=101.0,
    )
    plan = manager.plan_tick(now=101.0)

    assert plan.derived_status is GroupState.EMERGENCY_STOP
    assert all(
        decision.target is not None and not decision.target.enabled and decision.target.power == 0
        for decision in plan.decisions
    )
    with pytest.raises(GroupPlanningError, match="not implemented"):
        manager.update_runtime(
            GroupRuntime(state=GroupState.RUNNING, pattern=PatternKind.SINE),
            now=102.0,
        )


def test_continue_limited_caps_only_remaining_online_members_after_gain() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    manager.update_availability({"left": True, "right": False, "crossflow": True})

    plan = manager.plan_tick(now=100.0)
    decisions = _decisions(plan)

    assert decisions["right"].action is MemberAction.SKIP_OFFLINE
    assert decisions["right"].control_epoch > 0
    assert decisions["left"].normalized_power == 50
    assert decisions["crossflow"].normalized_power <= 50
    assert "offline_policy_cap" in decisions["left"].clamp_reasons
    assert plan.derived_status is GroupState.DEGRADED


def test_manual_override_is_reflected_as_degraded_plan_diagnostics() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    _ready(manager)
    manager.set_manual_override("right")

    plan = manager.plan_tick(now=100.0)

    assert plan.manual_override_devices == ("right",)
    assert plan.derived_status is GroupState.DEGRADED


def test_failure_cap_below_first_valid_device_power_fails_closed_to_off() -> None:
    group = _group().model_copy(
        update={
            "failure_policy": FailurePolicy(
                on_member_offline=OfflinePolicy.CONTINUE_LIMITED,
                remaining_member_max_power=31,
            )
        }
    )
    manager = GroupManager(
        group,
        _limits(left=DeviceActuationLimits(min_power=32, max_power=80, power_step=5)),
        clock=lambda: 100.0,
    )
    manager.update_availability({"left": True, "right": False, "crossflow": True})

    decision = _decisions(manager.plan_tick(now=100.0))["left"]

    assert decision.target is not None
    assert not decision.target.enabled and decision.target.power == 0
    assert decision.planning_failure == "no_valid_enabled_power"
    assert decision.effective_max_power == 31


def test_power_step_uses_absolute_grid_and_never_exceeds_effective_maximum() -> None:
    manager = GroupManager(
        _group(pattern=PatternKind.CONSTANT),
        _limits(left=DeviceActuationLimits(min_power=32, max_power=73, power_step=5)),
        clock=lambda: 100.0,
    )
    _ready(manager)

    decision = _decisions(manager.plan_tick(now=100.0))["left"]

    assert decision.requested_power == 65
    assert decision.normalized_power == 65
    assert decision.normalized_power % 5 == 0
    assert decision.normalized_power <= decision.effective_max_power


def test_absolute_step_grid_ceilings_a_device_minimum() -> None:
    group = _group(pattern=PatternKind.CONSTANT).model_copy(
        update={
            "default": GroupDefaults(
                pattern=PatternKind.CONSTANT,
                power=32,
                min_power=0,
                max_power=75,
                period_seconds=8,
            )
        }
    )
    manager = GroupManager(
        group,
        _limits(left=DeviceActuationLimits(min_power=32, max_power=73, power_step=5)),
        clock=lambda: 100.0,
    )
    _ready(manager)

    decision = _decisions(manager.plan_tick(now=100.0))["left"]

    assert (decision.requested_power, decision.normalized_power) == (32, 35)
    assert decision.clamp_reasons == ("step_ceiling_to_min",)


def test_device_max_and_step_adjustments_are_preserved_for_diagnostics() -> None:
    manager = GroupManager(
        _group(pattern=PatternKind.CONSTANT),
        _limits(left=DeviceActuationLimits(min_power=32, max_power=62, power_step=5)),
        clock=lambda: 100.0,
    )
    _ready(manager)

    decision = _decisions(manager.plan_tick(now=100.0))["left"]

    assert (decision.requested_power, decision.normalized_power) == (65, 60)
    assert decision.clamp_reasons == ("device_max", "step_floor")
    assert decision.limits_used == DeviceActuationLimits(32, 62, 5)


@pytest.mark.parametrize(
    "runtime",
    [
        GroupRuntime(state=GroupState.RUNNING, min_power=80),
        GroupRuntime(state=GroupState.RUNNING, max_power=30),
    ],
)
def test_one_sided_runtime_limit_is_composed_with_group_default(
    runtime: GroupRuntime,
) -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    original_runtime = manager.runtime

    with pytest.raises(GroupPlanningError, match="group 'main_flow' effective"):
        manager.update_runtime(runtime, now=101.0)

    assert manager.runtime == original_runtime
    manager.plan_tick(now=100.5)


def test_all_planner_controlled_members_failing_reports_error_with_an_override() -> None:
    manager = GroupManager(
        _group(pattern=PatternKind.CONSTANT),
        {
            "left": DeviceActuationLimits(min_power=32, max_power=33, power_step=5),
            "right": DeviceActuationLimits(min_power=32, max_power=33, power_step=5),
            "crossflow": DeviceActuationLimits(min_power=32, max_power=33, power_step=5),
        },
        clock=lambda: 100.0,
    )
    _ready(manager)
    manager.set_manual_override("right")

    plan = manager.plan_tick(now=100.0)
    decisions = _decisions(plan)

    assert decisions["right"].action is MemberAction.SKIP_MANUAL_OVERRIDE
    assert decisions["left"].planning_failure == "no_valid_enabled_power"
    assert decisions["crossflow"].planning_failure == "no_valid_enabled_power"
    assert plan.derived_status is GroupState.ERROR


def test_unknown_startup_holds_on_writes_and_reports_starting() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)

    plan = manager.plan_tick(now=100.0)

    assert all(decision.action is MemberAction.SKIP_UNKNOWN for decision in plan.decisions)
    assert all(decision.normalized_power is None for decision in plan.decisions)
    assert plan.derived_status is GroupState.STARTING


def test_mixed_offline_and_unknown_with_no_online_member_reports_error() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    manager.update_availability({"left": False})

    plan = manager.plan_tick(now=100.0)

    assert plan.derived_status is GroupState.ERROR


def test_stop_group_policy_turns_online_members_off_and_skips_offline_member() -> None:
    manager = GroupManager(
        _group(policy=OfflinePolicy.STOP_GROUP),
        _limits(),
        clock=lambda: 100.0,
    )
    manager.update_availability({"left": True, "right": False, "crossflow": True})

    plan = manager.plan_tick(now=100.0)
    decisions = _decisions(plan)

    assert decisions["right"].action is MemberAction.SKIP_OFFLINE
    assert all(
        decisions[device].target is not None
        and not decisions[device].target.enabled
        and decisions[device].target.power == 0
        for device in ("left", "crossflow")
    )
    assert plan.failure_policy_stopped
    assert plan.derived_status is GroupState.ERROR


def test_stop_group_policy_does_not_depend_on_waveform_arithmetic() -> None:
    group = _group(pattern=PatternKind.LAGOON, policy=OfflinePolicy.STOP_GROUP).model_copy(
        update={
            "default": GroupDefaults(
                pattern=PatternKind.LAGOON,
                power=65,
                min_power=35,
                max_power=75,
                period_seconds=5e-324,
            )
        }
    )
    manager = GroupManager(group, _limits(), clock=lambda: 0.0)
    manager.update_availability({"left": True, "right": False, "crossflow": True})

    decisions = _decisions(manager.plan_tick(now=1.0))

    assert decisions["right"].action is MemberAction.SKIP_OFFLINE
    assert all(
        decisions[device_id].target is not None
        and not decisions[device_id].target.enabled
        and decisions[device_id].target.power == 0
        for device_id in ("left", "crossflow")
    )


def test_requested_error_is_not_downgraded_by_an_offline_member() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    manager.update_availability({"left": True, "right": False, "crossflow": True})
    manager.update_runtime(GroupRuntime(state=GroupState.ERROR), now=100.0)

    plan = manager.plan_tick(now=100.0)

    assert plan.derived_status is GroupState.ERROR


def test_fallback_constant_is_explicitly_deferred_when_a_member_is_unavailable() -> None:
    manager = GroupManager(
        _group(policy=OfflinePolicy.FALLBACK_CONSTANT),
        _limits(),
        clock=lambda: 100.0,
    )
    manager.update_availability({"left": True, "right": False, "crossflow": True})

    with pytest.raises(GroupPlanningError, match="fallback_constant"):
        manager.plan_tick(now=100.0)


@pytest.mark.parametrize(
    "policy",
    [OfflinePolicy.STOP_GROUP, OfflinePolicy.FALLBACK_CONSTANT],
)
def test_held_state_precedes_active_offline_policy(policy: OfflinePolicy) -> None:
    manager = GroupManager(_group(policy=policy), _limits(), clock=lambda: 100.0)
    manager.update_runtime(GroupRuntime(state=GroupState.STARTING), now=101.0)

    plan = manager.plan_tick(now=101.0)

    assert all(decision.action is MemberAction.SKIP_HELD for decision in plan.decisions)
    assert not plan.failure_policy_stopped
    assert plan.derived_status is GroupState.STARTING


def test_native_group_is_rejected_before_any_plan_exists() -> None:
    group = GroupConfig(
        id="native_flow",
        name="Native flow",
        execution_strategy=GroupExecutionStrategy.NATIVE_LINKED,
        native_pair=NativePairConfig(
            master="left",
            slave="right",
            relation=NativeLinkageRelation.SYNC,
        ),
        members=(GroupMember(device="left"), GroupMember(device="right")),
        default=GroupDefaults(pattern=PatternKind.NATIVE),
    )

    with pytest.raises(GroupPlanningError, match="guarded native actuator"):
        GroupManager(group, {"left": _limits()["left"], "right": _limits()["right"]})


@pytest.mark.parametrize("pattern", [PatternKind.SINE, PatternKind.NATIVE])
def test_unimplemented_software_pattern_is_rejected_at_configuration_time(
    pattern: PatternKind,
) -> None:
    with pytest.raises(GroupPlanningError, match="not implemented"):
        GroupManager(_group(pattern=pattern), _limits(), clock=lambda: 100.0)


def test_monotonic_time_cannot_move_backwards() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)

    with pytest.raises(GroupPlanningError, match="moved backwards"):
        manager.plan_tick(now=99.0)


def test_pure_planner_rejects_a_boolean_timestamp() -> None:
    group = _group()
    epoch = PatternEpoch(
        generation=1,
        started_at=100.0,
        pattern=PatternKind.ANTI_PHASE,
        period_seconds=8,
    )
    with pytest.raises(GroupPlanningError, match="tick timestamp"):
        plan_group_tick(
            GroupTickInput(
                timestamp=True,
                generation=1,
                group=group,
                runtime=GroupRuntime(state=GroupState.RUNNING),
                epoch=epoch,
                member_status={device_id: MemberStatus(online=True) for device_id in _limits()},
                device_limits=_limits(),
            )
        )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True])
def test_pattern_epoch_rejects_invalid_time_values(value: object) -> None:
    with pytest.raises((GroupPlanningError, TypeError), match="pattern epoch"):
        PatternEpoch(
            generation=1,
            started_at=value,  # type: ignore[arg-type]
            pattern=PatternKind.CONSTANT,
            period_seconds=8,
        )


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), float("-inf"), True])
def test_monotonic_time_must_be_a_finite_number(timestamp: float) -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)

    with pytest.raises(GroupPlanningError, match="finite number"):
        manager.plan_tick(now=timestamp)


@pytest.mark.parametrize("online", [0, 1, "yes", object()])
def test_availability_rejects_non_boolean_values(online: object) -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)

    with pytest.raises(GroupPlanningError, match="bool or None"):
        manager.update_availability({"left": online})  # type: ignore[dict-item]


@pytest.mark.parametrize(
    "values",
    [
        {"min_power": False, "max_power": 80, "power_step": 1},
        {"min_power": 30.0, "max_power": 80, "power_step": 1},
        {"min_power": 30, "max_power": 80.0, "power_step": 1},
        {"min_power": 30, "max_power": 80, "power_step": 1.0},
    ],
)
def test_device_limits_require_strict_integers(values: dict[str, object]) -> None:
    with pytest.raises(TypeError, match="integer"):
        DeviceActuationLimits(**values)  # type: ignore[arg-type]


def test_disabled_runtime_rejects_active_or_held_state() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)

    with pytest.raises(GroupPlanningError, match="disabled groups"):
        manager.update_runtime(GroupRuntime(state=GroupState.FEEDING, enabled=False), now=101.0)


def test_infinite_period_is_rejected_before_manager_state_changes() -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 100.0)
    original_runtime = manager.runtime

    with pytest.raises(GroupPlanningError, match="finite and positive"):
        manager.update_runtime(
            GroupRuntime(state=GroupState.RUNNING, period_seconds=float("inf")),
            now=101.0,
        )

    assert manager.runtime == original_runtime
    manager.plan_tick(now=100.5)


def test_group_planner_import_graph_has_no_device_or_transport_dependencies() -> None:
    repository = Path(__file__).resolve().parents[2]
    modules = (
        repository / "src/jebao_flow/groups/plan.py",
        repository / "src/jebao_flow/groups/manager.py",
    )
    forbidden = (
        "jebao_flow.app",
        "jebao_flow.devices",
        "jebao_flow.mqtt",
        "jebao_flow.protocol.session",
    )

    imported: set[str] = set()
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)
    assert not any(name.startswith(forbidden) for name in imported)

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(repository / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            (
                "import json, sys; import jebao_flow.groups.manager; "
                "print(json.dumps(sorted(name for name in sys.modules "
                "if name.startswith(('jebao_flow.devices', 'jebao_flow.mqtt', "
                "'jebao_flow.app', 'jebao_flow.protocol.session')))))"
            ),
        ],
        cwd=repository,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout) == []
