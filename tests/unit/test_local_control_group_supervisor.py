from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

import pytest

import jebao_flow.groups.runtime as group_runtime_module
import jebao_flow.local_control.group_supervisor as group_supervisor_module
from jebao_flow.groups.manager import GroupManager
from jebao_flow.groups.models import (
    FailurePolicy,
    GroupConfig,
    GroupDefaults,
    GroupMember,
    GroupRuntime,
    GroupState,
    OfflinePolicy,
    PatternKind,
)
from jebao_flow.groups.plan import DeviceActuationLimits, GroupTickPlan
from jebao_flow.groups.runtime import (
    DeliveryCertainty,
    GroupPlanSupersededError,
    PlanAtomicGroupDispatchRuntime,
    PreWriteDispatchError,
    StaleDispatchError,
    UnresolvedGroupSafetyStopError,
    WriteGuard,
)
from jebao_flow.local_control.group_supervisor import (
    GROUP_EMERGENCY_STOP_BLOCKS_DIRECT_CONTROL,
    GROUP_FORCED_OFF_WILL_REVERT,
    GroupSupervisorDisposition,
    GroupSupervisorError,
    LocalControlGroupSupervisor,
)
from jebao_flow.local_control.state import FreshDeviceState

_OBSERVED_AT = datetime(2026, 9, 8, tzinfo=UTC)


class _RecordingPlanPort:
    def __init__(self, member_ids: tuple[str, ...] = ("left", "right")) -> None:
        self.group_id = "main_flow"
        self.member_ids = member_ids
        self.plans: list[GroupTickPlan] = []
        self.execution_contexts: list[tuple[int, str, object]] = []
        self.write_plan_call_count = 0

    def validate_plan(self, plan: GroupTickPlan) -> None:
        assert plan.group_id == self.group_id
        assert tuple(decision.device for decision in plan.decisions) == self.member_ids

    async def write_plan(
        self,
        plan: GroupTickPlan,
        *,
        request_id: str,
        generation: int,
        plan_identity_sha256: str,
        guard: WriteGuard,
        safety_token: object,
    ) -> None:
        del request_id
        self.write_plan_call_count += 1
        assert guard() is True
        self.plans.append(plan)
        self.execution_contexts.append((generation, plan_identity_sha256, safety_token))


class _FailingPlanPort(_RecordingPlanPort):
    def __init__(self, error_factory: Callable[[], Exception]) -> None:
        super().__init__()
        self._error_factory = error_factory

    async def write_plan(
        self,
        plan: GroupTickPlan,
        *,
        request_id: str,
        generation: int,
        plan_identity_sha256: str,
        guard: WriteGuard,
        safety_token: object,
    ) -> None:
        del plan, request_id, generation, plan_identity_sha256, safety_token
        self.write_plan_call_count += 1
        assert guard() is True
        raise self._error_factory()


class _DelayedPreSendPlanPort(_RecordingPlanPort):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.frame_count = 0

    async def write_plan(
        self,
        plan: GroupTickPlan,
        *,
        request_id: str,
        generation: int,
        plan_identity_sha256: str,
        guard: WriteGuard,
        safety_token: object,
    ) -> None:
        del plan, request_id, generation, plan_identity_sha256, safety_token
        self.write_plan_call_count += 1
        self.entered.set()
        await self.release.wait()
        if guard() is not True:
            raise GroupPlanSupersededError("injected frame-boundary supersede")
        self.frame_count += 1


class _SecondCallDelayedPlanPort(_RecordingPlanPort):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.frame_count = 0

    async def write_plan(
        self,
        plan: GroupTickPlan,
        *,
        request_id: str,
        generation: int,
        plan_identity_sha256: str,
        guard: WriteGuard,
        safety_token: object,
    ) -> None:
        del plan, request_id, generation, plan_identity_sha256, safety_token
        self.write_plan_call_count += 1
        if self.write_plan_call_count == 2:
            self.entered.set()
            await self.release.wait()
        assert guard() is True
        self.frame_count += 1


class _FailSecondPlanPort(_RecordingPlanPort):
    async def write_plan(
        self,
        plan: GroupTickPlan,
        *,
        request_id: str,
        generation: int,
        plan_identity_sha256: str,
        guard: WriteGuard,
        safety_token: object,
    ) -> None:
        del plan, request_id, generation, plan_identity_sha256, safety_token
        self.write_plan_call_count += 1
        assert guard() is True
        if self.write_plan_call_count == 2:
            raise RuntimeError("injected repeated OFF failure")


class _StalingGroupManager(GroupManager):
    """Fault injector that invalidates exactly one plan after producing it."""

    stale_next_plan = False

    def plan_tick(self, *, now: float | None = None) -> GroupTickPlan:
        plan = super().plan_tick(now=now)
        if self.stale_next_plan:
            self.stale_next_plan = False
            self.update_availability({"right": None})
        return plan


def _group(
    *,
    group_enabled: bool = True,
    left_enabled: bool = True,
    offline_policy: OfflinePolicy = OfflinePolicy.CONTINUE_LIMITED,
) -> GroupConfig:
    return GroupConfig(
        id="main_flow",
        name="Main flow",
        enabled=group_enabled,
        members=(
            GroupMember(device="left", enabled=left_enabled),
            GroupMember(device="right"),
        ),
        default=GroupDefaults(
            pattern=PatternKind.CONSTANT,
            power=50,
            min_power=30,
            max_power=75,
            period_seconds=10,
        ),
        failure_policy=FailurePolicy(on_member_offline=offline_policy),
    )


def _limits() -> dict[str, DeviceActuationLimits]:
    return {
        "left": DeviceActuationLimits(min_power=30, max_power=75),
        "right": DeviceActuationLimits(min_power=30, max_power=75),
    }


def _fresh(member: str, *, available: bool = True) -> FreshDeviceState:
    if available:
        return FreshDeviceState(
            logical_member=member,
            available=True,
            observed_at=_OBSERVED_AT,
        )
    return FreshDeviceState(
        logical_member=member,
        available=False,
        diagnostic_reason="fresh_read_unavailable",
    )


def _supervisor(
    *,
    group: GroupConfig | None = None,
    manager_factory: Callable[..., GroupManager] = GroupManager,
    port: _RecordingPlanPort | None = None,
) -> tuple[
    LocalControlGroupSupervisor,
    _RecordingPlanPort,
    GroupManager,
    PlanAtomicGroupDispatchRuntime,
]:
    manager = manager_factory(group or _group(), _limits(), clock=lambda: 0.0)
    selected_port = port or _RecordingPlanPort()
    runtime = PlanAtomicGroupDispatchRuntime(
        "main_flow",
        selected_port.member_ids,
        selected_port,
    )
    supervisor = LocalControlGroupSupervisor(manager=manager, runtime=runtime)
    return supervisor, selected_port, manager, runtime


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("group_enabled", "left_enabled"),
    [(True, True), (False, True), (True, False)],
)
async def test_construction_and_ticks_never_write_before_an_explicit_command(
    group_enabled: bool,
    left_enabled: bool,
) -> None:
    supervisor, port, _, _ = _supervisor(
        group=_group(group_enabled=group_enabled, left_enabled=left_enabled)
    )

    cycles = tuple([await supervisor.tick(now=float(index)) for index in range(1, 4)])

    assert all(cycle.disposition is GroupSupervisorDisposition.WRITE_DISARMED for cycle in cycles)
    assert port.plans == []
    snapshot = supervisor.snapshot()
    assert not snapshot.armed
    assert snapshot.runtime_submit_count == 0
    assert snapshot.write_disarmed_count == 3
    assert snapshot.dispatch.submitted_count == 0
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_explicit_command_submits_the_exact_manager_plan_once() -> None:
    supervisor, port, _, _ = _supervisor()
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)

    cycle = await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.RUNNING, enabled=True, power=50),
        now=2.0,
    )
    await supervisor.wait_idle()

    assert cycle.disposition is GroupSupervisorDisposition.SUBMITTED
    assert len(port.plans) == 1
    assert port.plans[0] is cycle.plan
    assert port.execution_contexts[0][0] == cycle.plan.generation
    assert supervisor.snapshot().runtime_submit_count == 1
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_repeated_ordinary_ticks_rely_on_plan_runtime_deduplication() -> None:
    supervisor, port, _, _ = _supervisor()
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.RUNNING, enabled=True, power=50),
        now=2.0,
    )
    await supervisor.wait_idle()

    await supervisor.tick(now=3.0)
    await supervisor.wait_idle()
    await supervisor.tick(now=4.0)
    snapshot = await supervisor.wait_idle()

    assert len(port.plans) == 1
    assert snapshot.deduplicated_count == 2
    assert supervisor.snapshot().runtime_submit_count == 3
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_repeated_mandatory_off_ticks_never_reach_the_plan_port_again() -> None:
    supervisor, port, _, _ = _supervisor()
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)
    first = await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=2.0,
    )
    await supervisor.wait_idle()

    repeated = tuple([await supervisor.tick(now=float(index)) for index in range(3, 6)])

    assert port.plans == [first.plan]
    assert all(
        cycle.disposition is GroupSupervisorDisposition.UNCHANGED_MANDATORY_OFF
        for cycle in repeated
    )
    snapshot = supervisor.snapshot()
    assert snapshot.runtime_submit_count == 1
    assert snapshot.mandatory_off_suppressed_count == 3
    assert snapshot.dispatch.submitted_count == 1
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_an_explicit_repeated_off_command_may_reach_the_plan_port_once_more() -> None:
    supervisor, port, _, _ = _supervisor()
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=2.0,
    )
    await supervisor.wait_idle()

    repeated = await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=3.0,
    )
    await supervisor.wait_idle()

    assert len(port.plans) == 2
    assert port.plans[1] is repeated.plan
    assert supervisor.snapshot().runtime_submit_count == 2
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_applied_invalidation_allows_the_same_off_to_be_checked_again() -> None:
    supervisor, port, _, runtime = _supervisor()
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=2.0,
    )
    await supervisor.wait_idle()

    runtime.invalidate_applied()
    repeated = await supervisor.tick(now=3.0)
    await supervisor.wait_idle()

    assert repeated.disposition is GroupSupervisorDisposition.SUBMITTED
    assert len(port.plans) == 2
    assert port.plans[1] is repeated.plan
    await supervisor.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error_factory", "certainty", "calls_after_close"),
    [
        pytest.param(
            lambda: RuntimeError("delivery unknown"),
            DeliveryCertainty.UNCERTAIN,
            1,
            id="uncertain",
        ),
        pytest.param(
            lambda: PreWriteDispatchError("known not sent"),
            DeliveryCertainty.NOT_SENT,
            2,
            id="known-not-sent",
        ),
    ],
)
async def test_failed_mandatory_off_stays_explicit_and_close_failure_is_sticky(
    error_factory: Callable[[], Exception],
    certainty: DeliveryCertainty,
    calls_after_close: int,
) -> None:
    failing_port = _FailingPlanPort(error_factory)
    supervisor, port, _, _ = _supervisor(port=failing_port)
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)
    first = await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=2.0,
    )
    failed = await supervisor.wait_idle()

    assert first.disposition is GroupSupervisorDisposition.SUBMITTED
    assert failed.failure is not None
    assert failed.failure.delivery_certainty is certainty
    assert failed.safety_off_required
    assert port.write_plan_call_count == 1

    deferred = await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=3.0,
    )
    periodic = await supervisor.tick(now=4.0)

    assert deferred.disposition is GroupSupervisorDisposition.DEFERRED_SAFETY_OFF
    assert not deferred.submitted
    assert deferred.runtime_submit_called
    assert periodic.disposition is GroupSupervisorDisposition.UNCHANGED_MANDATORY_OFF
    assert supervisor.snapshot().runtime_submit_count == 2
    assert port.write_plan_call_count == 1

    with pytest.raises(UnresolvedGroupSafetyStopError):
        await supervisor.aclose()
    assert port.write_plan_call_count == calls_after_close
    with pytest.raises(UnresolvedGroupSafetyStopError):
        await supervisor.aclose()
    assert port.write_plan_call_count == calls_after_close


@pytest.mark.asyncio
async def test_latched_ordinary_failure_reports_later_plan_as_suppressed() -> None:
    failing_port = _FailingPlanPort(lambda: RuntimeError("delivery unknown"))
    supervisor, port, _, _ = _supervisor(port=failing_port)
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)
    first = await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.RUNNING, enabled=True, power=50),
        now=2.0,
    )
    failed = await supervisor.wait_idle()

    assert first.disposition is GroupSupervisorDisposition.SUBMITTED
    assert failed.failure is not None
    assert failed.failure.delivery_certainty is DeliveryCertainty.UNCERTAIN
    assert port.write_plan_call_count == 1

    suppressed = await supervisor.tick(now=3.0)

    assert suppressed.disposition is GroupSupervisorDisposition.SUPPRESSED_AFTER_FAILURE
    assert not suppressed.submitted
    assert suppressed.runtime_submit_called
    assert suppressed.dispatch.failure_suppressed_count == 1
    assert port.write_plan_call_count == 1
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_fresh_failure_is_unknown_and_never_claimed_as_offline() -> None:
    supervisor, port, _, _ = _supervisor()

    cycle = await supervisor.observe_fresh_cycle(
        (_fresh("left"), _fresh("right", available=False)),
        now=1.0,
    )

    assert cycle.plan.offline_devices == ()
    assert cycle.plan.unknown_devices == ("right",)
    assert port.plans == []
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_stale_plan_between_tick_and_submit_is_rejected_then_next_tick_converges() -> None:
    supervisor, port, manager, _ = _supervisor(manager_factory=_StalingGroupManager)
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)
    assert isinstance(manager, _StalingGroupManager)
    manager.stale_next_plan = True

    with pytest.raises(StaleDispatchError, match="not current"):
        await supervisor.apply_runtime_command(
            GroupRuntime(state=GroupState.RUNNING, enabled=True, power=50),
            now=2.0,
        )

    assert supervisor.armed
    assert port.plans == []
    converged = await supervisor.tick(now=3.0)
    await supervisor.wait_idle()

    assert converged.plan.unknown_devices == ("right",)
    assert len(port.plans) == 1
    assert port.plans[0] is converged.plan
    assert supervisor.snapshot().runtime_submit_count == 1
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_fresh_cycle_requires_the_exact_group_member_set() -> None:
    supervisor, port, _, _ = _supervisor()

    with pytest.raises(GroupSupervisorError, match="missing=right"):
        await supervisor.observe_fresh_cycle((_fresh("left"),), now=1.0)
    with pytest.raises(GroupSupervisorError, match="duplicate"):
        await supervisor.observe_fresh_cycle(
            (_fresh("left"), _fresh("left")),
            now=1.0,
        )

    assert port.plans == []
    assert supervisor.snapshot().cycle_count == 0
    await supervisor.aclose()


@pytest.mark.parametrize(
    "state",
    [
        GroupState.RUNNING,
        GroupState.STARTING,
        GroupState.STOPPED,
        GroupState.EMERGENCY_STOP,
    ],
)
def test_supervisor_and_plan_runtime_classify_manager_plans_the_same_way(
    state: GroupState,
) -> None:
    manager = GroupManager(_group(), _limits(), clock=lambda: 0.0)
    manager.update_availability({"left": True, "right": True})
    manager.update_runtime(
        GroupRuntime(
            state=state,
            enabled=state not in {GroupState.STOPPED, GroupState.EMERGENCY_STOP},
            power=50,
        ),
        now=1.0,
    )
    plan = manager.plan_tick(now=1.0)

    assert group_supervisor_module._is_mandatory_group_off(  # noqa: SLF001
        plan
    ) is group_runtime_module._is_mandatory_group_off(plan)  # noqa: SLF001


def test_failure_policy_stop_classifier_matches_the_plan_runtime() -> None:
    manager = GroupManager(
        _group(offline_policy=OfflinePolicy.STOP_GROUP),
        _limits(),
        clock=lambda: 0.0,
    )
    manager.update_availability({"left": True, "right": None})
    manager.update_runtime(
        GroupRuntime(state=GroupState.RUNNING, enabled=True, power=50),
        now=1.0,
    )
    plan = manager.plan_tick(now=1.0)

    assert plan.failure_policy_stopped
    assert group_supervisor_module._is_mandatory_group_off(  # noqa: SLF001
        plan
    ) is group_runtime_module._is_mandatory_group_off(plan)  # noqa: SLF001


@pytest.mark.asyncio
async def test_manual_ownership_supersedes_an_ordinary_plan_at_the_frame_boundary() -> None:
    delayed_port = _DelayedPreSendPlanPort()
    supervisor, port, _, _ = _supervisor(port=delayed_port)
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.RUNNING, enabled=True, power=50),
        now=2.0,
    )
    await delayed_port.entered.wait()

    ownership = await supervisor.claim_manual_ownership(
        "left",
        power_on_requested=True,
    )
    await supervisor.note_verified_direct_power_on("left")
    delayed_port.release.set()
    dispatch = await supervisor.wait_idle()

    assert ownership.allowed
    assert ownership.manual_override_recorded
    assert ownership.diagnostic_reason is None
    assert port.write_plan_call_count == 1
    assert delayed_port.frame_count == 0
    assert dispatch.superseded_count == 1
    assert dispatch.failure is None
    assert supervisor.snapshot().manual_override_members == ("left",)
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_manual_ownership_cannot_cancel_an_accepted_mandatory_off() -> None:
    delayed_port = _DelayedPreSendPlanPort()
    supervisor, port, _, _ = _supervisor(port=delayed_port)
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=2.0,
    )
    await delayed_port.entered.wait()

    ownership = await supervisor.claim_manual_ownership(
        "left",
        power_on_requested=True,
    )
    await supervisor.note_verified_direct_power_on("left")
    delayed_port.release.set()
    dispatch = await supervisor.wait_idle()

    assert ownership.allowed
    assert ownership.diagnostic_reason == GROUP_FORCED_OFF_WILL_REVERT
    assert port.write_plan_call_count == 1
    assert delayed_port.frame_count == 1
    assert dispatch.safety_off_succeeded_count == 1
    assert not dispatch.safety_off_required
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_ordinary_override_replans_once_then_deduplicates() -> None:
    supervisor, port, _, _ = _supervisor()
    fresh = (_fresh("left"), _fresh("right"))
    await supervisor.observe_fresh_cycle(fresh, now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.RUNNING, enabled=True, power=50),
        now=2.0,
    )
    await supervisor.wait_idle()

    await supervisor.claim_manual_ownership("left")
    assert port.write_plan_call_count == 1
    await supervisor.observe_fresh_cycle(fresh, now=3.0)
    await supervisor.wait_idle()
    assert port.write_plan_call_count == 2
    await supervisor.observe_fresh_cycle(fresh, now=4.0)
    await supervisor.wait_idle()

    assert port.write_plan_call_count == 2
    assert supervisor.snapshot().manual_override_members == ("left",)
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_stopped_override_warns_and_one_off_reassertion_is_bounded() -> None:
    supervisor, port, _, _ = _supervisor()
    fresh = (_fresh("left"), _fresh("right"))
    await supervisor.observe_fresh_cycle(fresh, now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=2.0,
    )
    await supervisor.wait_idle()

    ownership = await supervisor.claim_manual_ownership(
        "left",
        power_on_requested=True,
    )
    assert ownership.allowed
    assert ownership.manual_override_recorded
    assert ownership.diagnostic_reason == GROUP_FORCED_OFF_WILL_REVERT
    assert supervisor.snapshot().ownership_diagnostic_reason is None
    await supervisor.note_verified_direct_power_on("left")
    assert (
        supervisor.snapshot().ownership_diagnostic_reason
        == GROUP_FORCED_OFF_WILL_REVERT
    )
    assert port.write_plan_call_count == 1

    await supervisor.observe_fresh_cycle(fresh, now=3.0)
    await supervisor.wait_idle()
    assert port.write_plan_call_count == 2
    await supervisor.observe_fresh_cycle(fresh, now=4.0)
    await supervisor.wait_idle()

    assert port.write_plan_call_count == 2
    assert supervisor.snapshot().ownership_diagnostic_reason is None
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_repeated_verified_power_on_each_reasserts_stopped_off_once() -> None:
    supervisor, port, _, _ = _supervisor()
    fresh = (_fresh("left"), _fresh("right"))
    await supervisor.observe_fresh_cycle(fresh, now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=2.0,
    )
    await supervisor.wait_idle()

    for index in range(3):
        ownership = await supervisor.claim_manual_ownership(
            "left",
            power_on_requested=True,
        )
        await supervisor.note_verified_direct_power_on("left")
        before = port.write_plan_call_count
        await supervisor.observe_fresh_cycle(fresh, now=3.0 + index * 2)
        await supervisor.wait_idle()
        assert ownership.diagnostic_reason == GROUP_FORCED_OFF_WILL_REVERT
        assert port.write_plan_call_count == before + 1
        await supervisor.observe_fresh_cycle(fresh, now=4.0 + index * 2)
        await supervisor.wait_idle()
        assert port.write_plan_call_count == before + 1

    await supervisor.aclose()


@pytest.mark.asyncio
async def test_waiting_off_success_reconciles_direct_power_on_without_duplicate() -> None:
    delayed_port = _SecondCallDelayedPlanPort()
    supervisor, port, _, _ = _supervisor(port=delayed_port)
    fresh = (_fresh("left"), _fresh("right"))
    await supervisor.observe_fresh_cycle(fresh, now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=2.0,
    )
    await supervisor.wait_idle()

    await supervisor.claim_manual_ownership("left", power_on_requested=True)
    await supervisor.note_verified_direct_power_on("left")
    await supervisor.observe_fresh_cycle(fresh, now=3.0)
    await delayed_port.entered.wait()
    await supervisor.claim_manual_ownership("left", power_on_requested=True)
    await supervisor.note_verified_direct_power_on("left")
    delayed_port.release.set()
    dispatch = await supervisor.wait_idle()
    before = port.write_plan_call_count

    cycle = await supervisor.observe_fresh_cycle(fresh, now=4.0)
    await supervisor.wait_idle()

    assert before == 2
    assert delayed_port.frame_count == 2
    assert dispatch.safety_off_succeeded_count == 2
    assert cycle.disposition is GroupSupervisorDisposition.UNCHANGED_MANDATORY_OFF
    assert port.write_plan_call_count == before
    assert supervisor.snapshot().ownership_diagnostic_reason is None
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_failed_watermarked_off_stays_required_without_another_port_call() -> None:
    failing_port = _FailSecondPlanPort()
    supervisor, port, _, _ = _supervisor(port=failing_port)
    fresh = (_fresh("left"), _fresh("right"))
    await supervisor.observe_fresh_cycle(fresh, now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=2.0,
    )
    await supervisor.wait_idle()
    await supervisor.claim_manual_ownership("left", power_on_requested=True)
    await supervisor.note_verified_direct_power_on("left")

    await supervisor.observe_fresh_cycle(fresh, now=3.0)
    failed = await supervisor.wait_idle()
    deferred = await supervisor.observe_fresh_cycle(fresh, now=4.0)

    assert port.write_plan_call_count == 2
    assert failed.safety_off_succeeded_count == 1
    assert failed.safety_off_required
    assert deferred.disposition is GroupSupervisorDisposition.DEFERRED_SAFETY_OFF
    assert deferred.dispatch.safety_off_deferred_count == 1
    assert port.write_plan_call_count == 2
    with pytest.raises(UnresolvedGroupSafetyStopError):
        await supervisor.aclose()


@pytest.mark.asyncio
async def test_non_power_redeclaration_only_pays_the_first_ownership_cost() -> None:
    supervisor, port, _, _ = _supervisor()
    fresh = (_fresh("left"), _fresh("right"))
    await supervisor.observe_fresh_cycle(fresh, now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.STOPPED, enabled=False),
        now=2.0,
    )
    await supervisor.wait_idle()

    first = await supervisor.claim_manual_ownership("left")
    await supervisor.observe_fresh_cycle(fresh, now=3.0)
    await supervisor.wait_idle()
    after_first = port.write_plan_call_count
    repeated = await supervisor.claim_manual_ownership("left")
    await supervisor.observe_fresh_cycle(fresh, now=4.0)
    await supervisor.wait_idle()

    assert first.diagnostic_reason is None
    assert repeated.diagnostic_reason is None
    assert after_first == 2
    assert port.write_plan_call_count == after_first
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_power_on_watermark_does_not_change_ordinary_plan_deduplication() -> None:
    supervisor, port, _, _ = _supervisor()
    fresh = (_fresh("left"), _fresh("right"))
    await supervisor.observe_fresh_cycle(fresh, now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.RUNNING, enabled=True, power=50),
        now=2.0,
    )
    await supervisor.wait_idle()
    await supervisor.claim_manual_ownership("left", power_on_requested=True)
    await supervisor.note_verified_direct_power_on("left")
    await supervisor.observe_fresh_cycle(fresh, now=3.0)
    await supervisor.wait_idle()
    after_first = port.write_plan_call_count

    await supervisor.claim_manual_ownership("left", power_on_requested=True)
    await supervisor.note_verified_direct_power_on("left")
    await supervisor.observe_fresh_cycle(fresh, now=4.0)
    await supervisor.wait_idle()

    assert after_first == 2
    assert port.write_plan_call_count == after_first
    assert supervisor.snapshot().ownership_diagnostic_reason is None
    await supervisor.aclose()


@pytest.mark.asyncio
async def test_emergency_stop_rejects_without_recording_override_or_new_plan() -> None:
    supervisor, port, manager, _ = _supervisor()
    await supervisor.observe_fresh_cycle((_fresh("left"), _fresh("right")), now=1.0)
    await supervisor.apply_runtime_command(
        GroupRuntime(state=GroupState.EMERGENCY_STOP, enabled=False),
        now=2.0,
    )
    await supervisor.wait_idle()
    generation_before = manager.generation

    ownership = await supervisor.claim_manual_ownership("left")

    assert not ownership.allowed
    assert not ownership.manual_override_recorded
    assert ownership.diagnostic_reason == GROUP_EMERGENCY_STOP_BLOCKS_DIRECT_CONTROL
    assert manager.generation == generation_before
    assert supervisor.snapshot().manual_override_members == ()
    assert port.write_plan_call_count == 1
    await supervisor.aclose()
