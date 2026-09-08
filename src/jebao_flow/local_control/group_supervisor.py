"""Write-disarmed lifecycle supervision for one software-independent group.

The supervisor owns ordering around an existing write-free :class:`GroupManager` and the
plan-atomic runtime. It deliberately has no daemon, MQTT, Home Assistant, discovery, or device
construction path. Constructing it never submits a plan; an explicit runtime command is the only
operation that arms later ticks.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from jebao_flow.groups.manager import GroupManager
from jebao_flow.groups.models import GroupRuntime, GroupState
from jebao_flow.groups.plan import GroupTickPlan
from jebao_flow.groups.runtime import (
    GroupPlanDispatchSnapshot,
    PlanAtomicGroupDispatchRuntime,
    group_plan_identity_sha256,
)
from jebao_flow.local_control.state import FreshDeviceState
from jebao_flow.protocol.models import DeviceTarget

_CANONICAL_OFF = DeviceTarget(enabled=False, power=0)


class GroupSupervisorError(RuntimeError):
    """The group supervisor lifecycle or current observation is invalid."""


class GroupSupervisorDisposition(StrEnum):
    """Observable result of one manager-produced planning cycle."""

    WRITE_DISARMED = "write_disarmed"
    SUBMITTED = "submitted"
    DEFERRED_SAFETY_OFF = "deferred_safety_off"
    SUPPRESSED_AFTER_FAILURE = "suppressed_after_failure"
    UNCHANGED_MANDATORY_OFF = "unchanged_mandatory_off"


@dataclass(frozen=True, slots=True)
class GroupSupervisorCycle:
    """One exact manager plan and what the supervisor did with it."""

    plan: GroupTickPlan
    disposition: GroupSupervisorDisposition
    dispatch: GroupPlanDispatchSnapshot

    @property
    def submitted(self) -> bool:
        return self.disposition is GroupSupervisorDisposition.SUBMITTED

    @property
    def runtime_submit_called(self) -> bool:
        return self.disposition in {
            GroupSupervisorDisposition.SUBMITTED,
            GroupSupervisorDisposition.DEFERRED_SAFETY_OFF,
            GroupSupervisorDisposition.SUPPRESSED_AFTER_FAILURE,
        }


@dataclass(frozen=True, slots=True)
class GroupSupervisorSnapshot:
    """Bounded in-memory lifecycle diagnostics without physical identity material."""

    group_id: str
    armed: bool
    closed: bool
    cycle_count: int
    runtime_submit_count: int
    write_disarmed_count: int
    mandatory_off_suppressed_count: int
    last_plan_generation: int | None
    last_plan_identity_sha256: str | None
    dispatch: GroupPlanDispatchSnapshot


class LocalControlGroupSupervisor:
    """Serialize fresh availability, explicit ownership, planning, and plan submission.

    The supplied manager is exclusively owned by this supervisor after construction. Every plan
    submitted to the runtime is the exact object just returned by ``manager.plan_tick()``. There is
    no background loop here; callers may request cycles, but no cycle can submit until a valid
    explicit runtime command arms the supervisor.
    """

    def __init__(
        self,
        *,
        manager: GroupManager,
        runtime: PlanAtomicGroupDispatchRuntime,
    ) -> None:
        if not isinstance(manager, GroupManager):
            raise TypeError("group supervisor requires a GroupManager")
        if not isinstance(runtime, PlanAtomicGroupDispatchRuntime):
            raise TypeError("group supervisor requires a PlanAtomicGroupDispatchRuntime")
        if manager.group.id != runtime.group_id:
            raise ValueError("group manager and plan runtime ids must match")

        self._manager = manager
        self._runtime = runtime
        self._member_ids = tuple(member.device for member in manager.group.members)
        self._operation_lock = asyncio.Lock()
        self._armed = False
        self._closed = False
        self._cycle_count = 0
        self._runtime_submit_count = 0
        self._write_disarmed_count = 0
        self._mandatory_off_suppressed_count = 0
        self._last_plan_generation: int | None = None
        self._last_plan_identity_sha256: str | None = None

    @property
    def group_id(self) -> str:
        return self._manager.group.id

    @property
    def armed(self) -> bool:
        return self._armed

    @property
    def closed(self) -> bool:
        return self._closed

    async def tick(self, *, now: float | None = None) -> GroupSupervisorCycle:
        """Plan one cycle, remaining structurally write-disarmed before explicit ownership."""

        async with self._operation_lock:
            self._require_open()
            plan = self._manager.plan_tick(now=now)
            return self._finish_cycle(plan, force_mandatory_off=False)

    async def observe_fresh_cycle(
        self,
        states: Iterable[FreshDeviceState],
        *,
        now: float | None = None,
    ) -> GroupSupervisorCycle:
        """Apply one exact fresh-state set and plan after mapping unavailable to unknown."""

        async with self._operation_lock:
            self._require_open()
            availability = self._fresh_availability(states)
            self._manager.update_availability(availability)
            plan = self._manager.plan_tick(now=now)
            return self._finish_cycle(plan, force_mandatory_off=False)

    async def apply_runtime_command(
        self,
        runtime: GroupRuntime,
        *,
        now: float | None = None,
    ) -> GroupSupervisorCycle:
        """Apply explicit desired ownership, arm writes, and submit its exact manager plan once."""

        if not isinstance(runtime, GroupRuntime):
            raise TypeError("explicit group command must be a GroupRuntime")
        async with self._operation_lock:
            self._require_open()
            self._manager.update_runtime(runtime, now=now)
            plan = self._manager.plan_tick(now=now)
            self._armed = True
            return self._finish_cycle(plan, force_mandatory_off=True)

    def snapshot(self) -> GroupSupervisorSnapshot:
        return GroupSupervisorSnapshot(
            group_id=self.group_id,
            armed=self._armed,
            closed=self._closed,
            cycle_count=self._cycle_count,
            runtime_submit_count=self._runtime_submit_count,
            write_disarmed_count=self._write_disarmed_count,
            mandatory_off_suppressed_count=self._mandatory_off_suppressed_count,
            last_plan_generation=self._last_plan_generation,
            last_plan_identity_sha256=self._last_plan_identity_sha256,
            dispatch=self._runtime.snapshot(),
        )

    async def wait_idle(self) -> GroupPlanDispatchSnapshot:
        """Wait for work already accepted by the plan runtime."""

        return await self._runtime.wait_idle()

    async def aclose(self) -> None:
        """Close the underlying runtime without synthesizing or submitting a new plan."""

        self._closed = True
        # Delegate every call. The runtime deliberately re-reports an unresolved mandatory OFF,
        # so a second shutdown observer must not turn that sticky failure into a clean return.
        await self._runtime.aclose()

    def _finish_cycle(
        self,
        plan: GroupTickPlan,
        *,
        force_mandatory_off: bool,
    ) -> GroupSupervisorCycle:
        identity = group_plan_identity_sha256(plan)
        self._cycle_count += 1
        self._last_plan_generation = plan.generation
        self._last_plan_identity_sha256 = identity

        if not self._armed:
            self._write_disarmed_count += 1
            return GroupSupervisorCycle(
                plan=plan,
                disposition=GroupSupervisorDisposition.WRITE_DISARMED,
                dispatch=self._runtime.snapshot(),
            )

        mandatory_off = _is_mandatory_group_off(plan)
        current_dispatch = self._runtime.snapshot()
        if (
            mandatory_off
            and not force_mandatory_off
            and _runtime_holds_mandatory_off(current_dispatch, identity)
        ):
            self._mandatory_off_suppressed_count += 1
            return GroupSupervisorCycle(
                plan=plan,
                disposition=GroupSupervisorDisposition.UNCHANGED_MANDATORY_OFF,
                dispatch=self._runtime.snapshot(),
            )

        dispatch = self._runtime.submit(plan, is_current=self._manager.is_current)
        self._runtime_submit_count += 1
        disposition = (
            GroupSupervisorDisposition.DEFERRED_SAFETY_OFF
            if dispatch.safety_off_deferred_count > current_dispatch.safety_off_deferred_count
            else (
                GroupSupervisorDisposition.SUPPRESSED_AFTER_FAILURE
                if dispatch.failure_suppressed_count > current_dispatch.failure_suppressed_count
                else GroupSupervisorDisposition.SUBMITTED
            )
        )
        return GroupSupervisorCycle(
            plan=plan,
            disposition=disposition,
            dispatch=dispatch,
        )

    def _fresh_availability(
        self,
        states: Iterable[FreshDeviceState],
    ) -> dict[str, bool | None]:
        copied = tuple(states)
        if any(not isinstance(state, FreshDeviceState) for state in copied):
            raise TypeError("fresh group cycle must contain FreshDeviceState values")
        member_ids = tuple(state.logical_member for state in copied)
        if len(member_ids) != len(set(member_ids)):
            raise GroupSupervisorError("fresh group cycle contains duplicate logical members")
        if set(member_ids) != set(self._member_ids):
            missing = ", ".join(sorted(set(self._member_ids) - set(member_ids))) or "none"
            unexpected = ", ".join(sorted(set(member_ids) - set(self._member_ids))) or "none"
            raise GroupSupervisorError(
                "fresh group cycle must match group members; "
                f"missing={missing}; unexpected={unexpected}"
            )
        if any(type(state.available) is not bool for state in copied):
            raise TypeError("fresh group availability must be boolean")
        by_member = {state.logical_member: state for state in copied}
        return {
            member_id: True if by_member[member_id].available else None
            for member_id in self._member_ids
        }

    def _require_open(self) -> None:
        if self._closed:
            raise GroupSupervisorError("group supervisor is closed")


def _is_mandatory_group_off(plan: GroupTickPlan) -> bool:
    """Mirror the existing runtime classification solely to suppress periodic resubmission."""

    dispatches = plan.dispatch_decisions
    return (
        bool(dispatches)
        and all(decision.target == _CANONICAL_OFF for decision in dispatches)
        and (
            plan.derived_status in {GroupState.STOPPED, GroupState.EMERGENCY_STOP}
            or plan.failure_policy_stopped
        )
    )


def _runtime_holds_mandatory_off(
    dispatch: GroupPlanDispatchSnapshot,
    identity: str,
) -> bool:
    """Use runtime-owned state so applied invalidation also invalidates OFF suppression."""

    return dispatch.applied_plan_identity_sha256 == identity or (
        dispatch.safety_off_required and dispatch.latest_plan_identity_sha256 == identity
    )


__all__ = [
    "GroupSupervisorCycle",
    "GroupSupervisorDisposition",
    "GroupSupervisorError",
    "GroupSupervisorSnapshot",
    "LocalControlGroupSupervisor",
]
