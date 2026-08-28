"""Stateful, write-free manager for one software-independent group."""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Iterable, Mapping

from jebao_flow.groups.calculator import PatternCalculator
from jebao_flow.groups.models import (
    GroupConfig,
    GroupExecutionStrategy,
    GroupRuntime,
    GroupState,
)
from jebao_flow.groups.plan import (
    DeviceActuationLimits,
    GroupPlanningError,
    GroupTickInput,
    GroupTickPlan,
    MemberStatus,
    PatternEpoch,
    next_pattern_epoch,
    plan_group_tick,
    validate_runtime_consistency,
)
from jebao_flow.patterns.base import is_group_enabled

MonotonicClock = Callable[[], float]


class GroupManager:
    """Own phase, availability and override state without owning any device session."""

    def __init__(
        self,
        group: GroupConfig,
        device_limits: Mapping[str, DeviceActuationLimits],
        *,
        clock: MonotonicClock = time.monotonic,
    ) -> None:
        if group.execution_strategy is not GroupExecutionStrategy.SOFTWARE_INDEPENDENT:
            raise GroupPlanningError("native-linked groups require the guarded native actuator")
        initial_runtime = GroupRuntime(
            state=GroupState.RUNNING if group.enabled else GroupState.STOPPED,
            started_at=0,
        )
        validate_runtime_consistency(group, initial_runtime, require_pattern=group.enabled)
        member_ids = tuple(member.device for member in group.members)
        if set(device_limits) != set(member_ids):
            raise GroupPlanningError("device limits must match group members")

        now = _finite_timestamp(clock())
        self._group = group
        self._device_limits = dict(device_limits)
        self._clock = clock
        self._runtime = initial_runtime.model_copy(update={"started_at": now})
        self._epoch = next_pattern_epoch(None, group, self._runtime, timestamp=now)
        self._member_ids = member_ids
        self._member_status = {device_id: MemberStatus() for device_id in member_ids}
        self._generation = 0
        self._last_timestamp = now

    @property
    def group(self) -> GroupConfig:
        return self._group

    @property
    def runtime(self) -> GroupRuntime:
        return self._runtime

    @property
    def epoch(self) -> PatternEpoch:
        return self._epoch

    @property
    def generation(self) -> int:
        return self._generation

    def is_current(self, generation: int) -> bool:
        return generation == self._generation

    def update_runtime(self, runtime: GroupRuntime, *, now: float | None = None) -> int:
        was_active = _pattern_active(self._group, self._runtime)
        will_be_active = _pattern_active(self._group, runtime)
        validate_runtime_consistency(self._group, runtime, require_pattern=will_be_active)
        timestamp = self._timestamp(now)
        restart = (not was_active and will_be_active) or (
            self._runtime.state is GroupState.EMERGENCY_STOP
            and runtime.state is not GroupState.EMERGENCY_STOP
        )
        ownership_boundary = _forces_group_ownership(self._group, self._runtime) != (
            _forces_group_ownership(self._group, runtime)
        ) or (
            self._runtime.state is not GroupState.EMERGENCY_STOP
            and runtime.state is GroupState.EMERGENCY_STOP
        )
        if ownership_boundary:
            self._resume_all_without_generation(touch_control=False)
            self._touch_control(self._member_ids)
        epoch = (
            next_pattern_epoch(
                self._epoch,
                self._group,
                runtime,
                timestamp=timestamp,
                restart=restart,
            )
            if will_be_active
            else self._epoch
        )
        self._epoch = epoch
        self._runtime = runtime.model_copy(update={"started_at": epoch.started_at})
        return self._advance_generation()

    def update_availability(
        self,
        availability: Mapping[str, bool | None],
    ) -> int:
        unknown = set(availability) - set(self._member_status)
        if unknown:
            raise GroupPlanningError(
                "availability contains unknown members: " + ", ".join(sorted(unknown))
            )
        invalid = [
            device_id
            for device_id, online in availability.items()
            if online is not None and type(online) is not bool
        ]
        if invalid:
            raise GroupPlanningError(
                "availability must contain only bool or None: " + ", ".join(sorted(invalid))
            )
        changed = False
        for device_id, online in availability.items():
            current = self._member_status[device_id]
            if current.online is online:
                continue
            self._member_status[device_id] = MemberStatus(
                online=online,
                manual_override=current.manual_override,
                control_epoch=current.control_epoch,
            )
            self._touch_control((device_id,))
            changed = True
        return self._advance_generation() if changed else self._generation

    def set_manual_override(self, device_id: str) -> int:
        current = self._status(device_id)
        if current.manual_override:
            return self._generation
        self._member_status[device_id] = MemberStatus(
            online=current.online,
            manual_override=True,
            control_epoch=current.control_epoch,
        )
        self._touch_control((device_id,))
        return self._advance_generation()

    def resume_member(self, device_id: str) -> int:
        current = self._status(device_id)
        if not current.manual_override:
            return self._generation
        self._member_status[device_id] = MemberStatus(
            online=current.online,
            control_epoch=current.control_epoch,
        )
        self._touch_control((device_id,))
        return self._advance_generation()

    def resume_all(self) -> int:
        changed = self._resume_all_without_generation(touch_control=True)
        return self._advance_generation() if changed else self._generation

    def plan_tick(self, *, now: float | None = None) -> GroupTickPlan:
        timestamp = self._timestamp(now)
        generation = self._advance_generation()
        return plan_group_tick(
            GroupTickInput(
                timestamp=timestamp,
                generation=generation,
                group=self._group,
                runtime=self._runtime,
                epoch=self._epoch,
                member_status=dict(self._member_status),
                device_limits=dict(self._device_limits),
            )
        )

    def _status(self, device_id: str) -> MemberStatus:
        try:
            return self._member_status[device_id]
        except KeyError as error:
            raise GroupPlanningError(f"unknown group member {device_id!r}") from error

    def _timestamp(self, supplied: float | None) -> float:
        timestamp = _finite_timestamp(self._clock() if supplied is None else supplied)
        if timestamp < self._last_timestamp:
            raise GroupPlanningError("monotonic timestamp moved backwards")
        self._last_timestamp = timestamp
        return timestamp

    def _advance_generation(self) -> int:
        self._generation += 1
        return self._generation

    def _touch_control(self, device_ids: Iterable[str]) -> None:
        for device_id in device_ids:
            current = self._member_status[device_id]
            self._member_status[device_id] = MemberStatus(
                online=current.online,
                manual_override=current.manual_override,
                control_epoch=current.control_epoch + 1,
            )

    def _resume_all_without_generation(self, *, touch_control: bool) -> bool:
        changed = False
        for device_id, current in self._member_status.items():
            if not current.manual_override:
                continue
            self._member_status[device_id] = MemberStatus(
                online=current.online,
                control_epoch=current.control_epoch,
            )
            if touch_control:
                self._touch_control((device_id,))
            changed = True
        return changed


_ACTIVE_PATTERN_STATES = frozenset(
    {
        GroupState.RUNNING,
        GroupState.FEEDING,
        GroupState.DEGRADED,
        GroupState.ERROR,
    }
)


def _pattern_active(group: GroupConfig, runtime: GroupRuntime) -> bool:
    return is_group_enabled(group, runtime) and runtime.state in _ACTIVE_PATTERN_STATES


def _forces_group_ownership(group: GroupConfig, runtime: GroupRuntime) -> bool:
    return not is_group_enabled(group, runtime) or runtime.state in {
        GroupState.STOPPED,
        GroupState.EMERGENCY_STOP,
    }


def _finite_timestamp(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise GroupPlanningError("monotonic timestamp must be a finite number")
    return float(value)


__all__ = ["GroupManager", "PatternCalculator"]
