"""Pure planning for software-independent pump groups.

This module deliberately has no device or transport dependency.  It turns one immutable
snapshot into one ordered group plan; another layer owns asynchronous dispatch.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from jebao_flow.groups.calculator import PatternCalculator
from jebao_flow.groups.models import (
    GroupConfig,
    GroupExecutionStrategy,
    GroupRuntime,
    GroupState,
    OfflinePolicy,
    PatternKind,
)
from jebao_flow.patterns.base import effective_period, is_group_enabled
from jebao_flow.protocol.models import DeviceTarget


class GroupPlanningError(ValueError):
    """The supplied snapshot cannot produce a safe software plan."""


class MemberAction(StrEnum):
    DISPATCH = "dispatch"
    SKIP_MANUAL_OVERRIDE = "skip_manual_override"
    SKIP_OFFLINE = "skip_offline"
    SKIP_UNKNOWN = "skip_unknown"
    SKIP_HELD = "skip_held"


@dataclass(frozen=True, slots=True)
class DeviceActuationLimits:
    """Effective config/capability limits at the command boundary."""

    min_power: int
    max_power: int
    power_step: int = 1

    def __post_init__(self) -> None:
        for field_name in ("min_power", "max_power", "power_step"):
            if type(getattr(self, field_name)) is not int:
                raise TypeError(f"device {field_name.replace('_', ' ')} must be an integer")
        if not 0 <= self.min_power <= 100:
            raise ValueError("device minimum power must be between 0 and 100")
        if not 0 <= self.max_power <= 100:
            raise ValueError("device maximum power must be between 0 and 100")
        if self.min_power > self.max_power:
            raise ValueError("device minimum power must not exceed maximum power")
        if not 1 <= self.power_step <= 100:
            raise ValueError("device power step must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class MemberStatus:
    """One planner-owned view of availability and control ownership."""

    online: bool | None = None
    manual_override: bool = False
    control_epoch: int = 0

    def __post_init__(self) -> None:
        if self.online is not None and type(self.online) is not bool:
            raise TypeError("member online state must be bool or None")
        if type(self.manual_override) is not bool:
            raise TypeError("manual override state must be bool")
        if type(self.control_epoch) is not int or self.control_epoch < 0:
            raise TypeError("member control epoch must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class PatternEpoch:
    generation: int
    started_at: float
    pattern: PatternKind
    period_seconds: float

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation < 1:
            raise TypeError("pattern epoch generation must be a positive integer")
        _require_finite_number("pattern epoch timestamp", self.started_at)
        if not isinstance(self.pattern, PatternKind):
            raise TypeError("pattern epoch pattern must be a PatternKind")
        _require_finite_number("pattern epoch period", self.period_seconds, positive=True)


@dataclass(frozen=True, slots=True)
class GroupTickInput:
    timestamp: float
    generation: int
    group: GroupConfig
    runtime: GroupRuntime
    epoch: PatternEpoch
    member_status: Mapping[str, MemberStatus]
    device_limits: Mapping[str, DeviceActuationLimits]


@dataclass(frozen=True, slots=True)
class MemberDecision:
    device: str
    action: MemberAction
    requested_target: DeviceTarget
    target: DeviceTarget | None
    requested_power: int
    normalized_power: int | None
    limits_used: DeviceActuationLimits
    effective_max_power: int
    clamp_reasons: tuple[str, ...] = ()
    planning_failure: str | None = None
    control_epoch: int = 0


@dataclass(frozen=True, slots=True)
class GroupTickPlan:
    group_id: str
    generation: int
    timestamp: float
    epoch: PatternEpoch
    decisions: tuple[MemberDecision, ...]
    derived_status: GroupState
    offline_devices: tuple[str, ...]
    unknown_devices: tuple[str, ...]
    manual_override_devices: tuple[str, ...]
    failure_policy_stopped: bool

    @property
    def dispatch_decisions(self) -> tuple[MemberDecision, ...]:
        return tuple(
            decision for decision in self.decisions if decision.action is MemberAction.DISPATCH
        )


def next_pattern_epoch(
    previous: PatternEpoch | None,
    group: GroupConfig,
    runtime: GroupRuntime,
    *,
    timestamp: float,
    restart: bool = False,
) -> PatternEpoch:
    """Return an epoch whose phase starts only at explicit waveform boundaries."""

    pattern = runtime.pattern or group.default.pattern
    period = effective_period(group, runtime)
    changed = (
        previous is None
        or previous.pattern is not pattern
        or previous.period_seconds != period
        or restart
    )
    if not changed:
        return previous
    return PatternEpoch(
        generation=1 if previous is None else previous.generation + 1,
        started_at=timestamp,
        pattern=pattern,
        period_seconds=period,
    )


def plan_group_tick(tick: GroupTickInput) -> GroupTickPlan:
    """Calculate every member from one timestamp and apply safety overlays once."""

    group = tick.group
    if group.execution_strategy is not GroupExecutionStrategy.SOFTWARE_INDEPENDENT:
        raise GroupPlanningError("native-linked groups require the guarded native actuator")

    member_ids = tuple(member.device for member in group.members)
    _require_exact_members("member status", tick.member_status, member_ids)
    _require_exact_members("device limits", tick.device_limits, member_ids)
    _validate_tick_metadata(tick)

    enabled = is_group_enabled(group, tick.runtime)
    state = tick.runtime.state
    forced_off = not enabled or state in {GroupState.STOPPED, GroupState.EMERGENCY_STOP}
    held = not forced_off and state in {GroupState.STARTING, GroupState.MAINTENANCE}

    active_member_ids = tuple(member.device for member in group.members if member.enabled)
    offline = tuple(
        device_id
        for device_id in active_member_ids
        if tick.member_status[device_id].online is False
    )
    unknown = tuple(
        device_id for device_id in active_member_ids if tick.member_status[device_id].online is None
    )
    manual_overrides = tuple(
        device_id
        for device_id in active_member_ids
        if tick.member_status[device_id].manual_override
    )
    unavailable = frozenset((*offline, *unknown))
    stop_for_failure = (
        bool(unavailable)
        and group.failure_policy.on_member_offline is OfflinePolicy.STOP_GROUP
        and not forced_off
        and not held
    )
    pattern_required = not (forced_off or held or stop_for_failure)
    validate_runtime_consistency(group, tick.runtime, require_pattern=pattern_required)
    if pattern_required:
        expected_pattern = tick.runtime.pattern or group.default.pattern
        expected_period = effective_period(group, tick.runtime)
        if (
            tick.epoch.pattern is not expected_pattern
            or tick.epoch.period_seconds != expected_period
        ):
            raise GroupPlanningError("pattern epoch does not match the runtime snapshot")
    if (
        unavailable
        and group.failure_policy.on_member_offline is OfflinePolicy.FALLBACK_CONSTANT
        and pattern_required
    ):
        raise GroupPlanningError("fallback_constant is not implemented by the first runtime slice")

    if forced_off or held or stop_for_failure:
        raw_targets = {device_id: DeviceTarget(enabled=False, power=0) for device_id in member_ids}
    else:
        calculation_runtime = tick.runtime.model_copy(update={"started_at": tick.epoch.started_at})
        try:
            raw_targets = PatternCalculator().calculate(tick.timestamp, group, calculation_runtime)
        except ArithmeticError as error:
            raise GroupPlanningError("pattern calculation failed safely") from error
        _require_exact_members("calculated targets", raw_targets, member_ids)

    decisions: list[MemberDecision] = []
    planning_failure_count = 0
    for member in group.members:
        device_id = member.device
        status = tick.member_status[device_id]
        limits = tick.device_limits[device_id]
        control_epoch = status.control_epoch
        requested = raw_targets[device_id]
        reasons: list[str] = []

        if forced_off:
            reasons.append("group_off")
            decisions.append(
                _off_decision(
                    device_id,
                    requested,
                    limits,
                    reasons,
                    control_epoch=control_epoch,
                )
            )
            continue

        if held:
            decisions.append(
                _skip_decision(
                    device_id,
                    MemberAction.SKIP_HELD,
                    requested,
                    limits,
                    ("group_state_held",),
                    control_epoch=control_epoch,
                )
            )
            continue

        if stop_for_failure:
            if status.online is not True:
                action = (
                    MemberAction.SKIP_OFFLINE
                    if status.online is False
                    else MemberAction.SKIP_UNKNOWN
                )
                decisions.append(
                    _skip_decision(
                        device_id,
                        action,
                        requested,
                        limits,
                        ("offline_policy_stop",),
                        control_epoch=control_epoch,
                    )
                )
            else:
                decisions.append(
                    _off_decision(
                        device_id,
                        requested,
                        limits,
                        ["offline_policy_stop"],
                        control_epoch=control_epoch,
                    )
                )
            continue

        if status.manual_override:
            decisions.append(
                _skip_decision(
                    device_id,
                    MemberAction.SKIP_MANUAL_OVERRIDE,
                    requested,
                    limits,
                    ("manual_override",),
                    control_epoch=control_epoch,
                )
            )
            continue

        if status.online is not True and requested.enabled:
            action = (
                MemberAction.SKIP_OFFLINE if status.online is False else MemberAction.SKIP_UNKNOWN
            )
            decisions.append(
                _skip_decision(
                    device_id,
                    action,
                    requested,
                    limits,
                    ("member_unavailable",),
                    control_epoch=control_epoch,
                )
            )
            continue

        failure_cap: int | None = None
        if (
            unavailable
            and status.online is True
            and group.failure_policy.on_member_offline is OfflinePolicy.CONTINUE_LIMITED
        ):
            failure_cap = group.failure_policy.remaining_member_max_power

        target, normalized_power, effective_max, normalized_reasons, failure = _normalize_target(
            requested, limits, failure_cap=failure_cap
        )
        reasons.extend(normalized_reasons)
        if failure is not None:
            planning_failure_count += 1
        decisions.append(
            MemberDecision(
                device=device_id,
                action=MemberAction.DISPATCH,
                requested_target=requested,
                target=target,
                requested_power=requested.power,
                normalized_power=normalized_power,
                limits_used=limits,
                effective_max_power=effective_max,
                clamp_reasons=tuple(reasons),
                planning_failure=failure,
                control_epoch=control_epoch,
            )
        )

    return GroupTickPlan(
        group_id=group.id,
        generation=tick.generation,
        timestamp=tick.timestamp,
        epoch=tick.epoch,
        decisions=tuple(decisions),
        derived_status=_derive_status(
            state,
            enabled=enabled,
            active_member_count=len(active_member_ids),
            offline_count=len(offline),
            unknown_count=len(unknown),
            planning_failure_count=planning_failure_count,
            manual_override_count=len(manual_overrides),
            failure_policy_stopped=stop_for_failure,
        ),
        offline_devices=offline,
        unknown_devices=unknown,
        manual_override_devices=manual_overrides,
        failure_policy_stopped=stop_for_failure,
    )


def _normalize_target(
    requested: DeviceTarget,
    limits: DeviceActuationLimits,
    *,
    failure_cap: int | None,
) -> tuple[DeviceTarget, int, int, tuple[str, ...], str | None]:
    if not requested.enabled:
        return DeviceTarget(enabled=False, power=0), 0, limits.max_power, (), None

    reasons: list[str] = []
    effective_max = limits.max_power
    if failure_cap is not None and failure_cap < effective_max:
        effective_max = failure_cap
        if requested.power > failure_cap:
            reasons.append("offline_policy_cap")

    first_valid = ((limits.min_power + limits.power_step - 1) // limits.power_step) * (
        limits.power_step
    )
    if first_valid > effective_max:
        reasons.append("no_valid_enabled_power")
        return (
            DeviceTarget(enabled=False, power=0),
            0,
            effective_max,
            tuple(reasons),
            "no_valid_enabled_power",
        )

    bounded = requested.power
    if bounded > effective_max:
        bounded = effective_max
        if "offline_policy_cap" not in reasons:
            reasons.append("device_max")
    if bounded < limits.min_power:
        bounded = limits.min_power
        reasons.append("device_min")

    normalized = bounded - (bounded % limits.power_step)
    if normalized < limits.min_power:
        normalized = first_valid
        reasons.append("step_ceiling_to_min")
    elif normalized != bounded:
        reasons.append("step_floor")

    if normalized > effective_max:
        reasons.append("no_valid_enabled_power")
        return (
            DeviceTarget(enabled=False, power=0),
            0,
            effective_max,
            tuple(reasons),
            "no_valid_enabled_power",
        )
    return (
        requested.model_copy(update={"power": normalized}),
        normalized,
        effective_max,
        tuple(reasons),
        None,
    )


def _off_decision(
    device_id: str,
    requested: DeviceTarget,
    limits: DeviceActuationLimits,
    reasons: list[str],
    *,
    control_epoch: int,
) -> MemberDecision:
    return MemberDecision(
        device=device_id,
        action=MemberAction.DISPATCH,
        requested_target=requested,
        target=DeviceTarget(enabled=False, power=0),
        requested_power=requested.power,
        normalized_power=0,
        limits_used=limits,
        effective_max_power=limits.max_power,
        clamp_reasons=tuple(reasons),
        control_epoch=control_epoch,
    )


def _skip_decision(
    device_id: str,
    action: MemberAction,
    requested: DeviceTarget,
    limits: DeviceActuationLimits,
    reasons: tuple[str, ...],
    *,
    control_epoch: int,
) -> MemberDecision:
    return MemberDecision(
        device=device_id,
        action=action,
        requested_target=requested,
        target=None,
        requested_power=requested.power,
        normalized_power=None,
        limits_used=limits,
        effective_max_power=limits.max_power,
        clamp_reasons=reasons,
        control_epoch=control_epoch,
    )


def _derive_status(
    requested_state: GroupState,
    *,
    enabled: bool,
    active_member_count: int,
    offline_count: int,
    unknown_count: int,
    planning_failure_count: int,
    manual_override_count: int,
    failure_policy_stopped: bool,
) -> GroupState:
    if requested_state is GroupState.EMERGENCY_STOP:
        return GroupState.EMERGENCY_STOP
    if not enabled or requested_state is GroupState.STOPPED:
        return GroupState.STOPPED
    if requested_state in {GroupState.STARTING, GroupState.MAINTENANCE}:
        return requested_state
    if requested_state is GroupState.ERROR:
        return GroupState.ERROR
    if active_member_count == 0:
        return GroupState.STOPPED
    if offline_count + unknown_count == active_member_count:
        return GroupState.ERROR if offline_count else GroupState.STARTING
    if failure_policy_stopped:
        return GroupState.ERROR
    planner_controlled_count = (
        active_member_count - offline_count - unknown_count - manual_override_count
    )
    if planner_controlled_count > 0 and planning_failure_count >= planner_controlled_count:
        return GroupState.ERROR
    if offline_count or unknown_count or planning_failure_count or manual_override_count:
        return GroupState.DEGRADED
    if requested_state in {GroupState.FEEDING, GroupState.DEGRADED}:
        return requested_state
    return GroupState.RUNNING


def _require_exact_members(
    label: str,
    values: Mapping[str, object],
    expected: tuple[str, ...],
) -> None:
    expected_set = set(expected)
    actual_set = set(values)
    if actual_set == expected_set:
        return
    missing = ", ".join(sorted(expected_set - actual_set)) or "none"
    unexpected = ", ".join(sorted(actual_set - expected_set)) or "none"
    raise GroupPlanningError(
        f"{label} must match group members; missing={missing}; unexpected={unexpected}"
    )


def _validate_tick_metadata(tick: GroupTickInput) -> None:
    _require_finite_number("tick timestamp", tick.timestamp)
    if type(tick.generation) is not int or tick.generation < 1:
        raise GroupPlanningError("tick generation must be a positive integer")
    if type(tick.epoch.generation) is not int or tick.epoch.generation < 1:
        raise GroupPlanningError("pattern epoch generation must be a positive integer")


def _require_finite_number(label: str, value: object, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        raise GroupPlanningError(f"{label} must be a finite number")
    if positive and value <= 0:
        raise GroupPlanningError(f"{label} must be positive")


def validate_runtime_consistency(
    group: GroupConfig,
    runtime: GroupRuntime,
    *,
    require_pattern: bool = True,
) -> None:
    if not is_group_enabled(group, runtime) and runtime.state not in {
        GroupState.STOPPED,
        GroupState.EMERGENCY_STOP,
    }:
        raise GroupPlanningError("disabled groups must be stopped or emergency-stopped")
    if not require_pattern:
        return
    effective_min_power = (
        group.default.min_power if runtime.min_power is None else runtime.min_power
    )
    effective_max_power = (
        group.default.max_power if runtime.max_power is None else runtime.max_power
    )
    if effective_min_power > effective_max_power:
        raise GroupPlanningError(
            f"group {group.id!r} effective minimum power {effective_min_power} "
            f"exceeds effective maximum power {effective_max_power}"
        )
    pattern = runtime.pattern or group.default.pattern
    if pattern not in PatternCalculator.supported_patterns():
        raise GroupPlanningError(f"pattern {pattern.value!r} is not implemented by the planner")
    period = effective_period(group, runtime)
    if not math.isfinite(period) or period <= 0:
        raise GroupPlanningError("pattern period must be finite and positive")


__all__ = [
    "DeviceActuationLimits",
    "GroupPlanningError",
    "GroupTickInput",
    "GroupTickPlan",
    "MemberAction",
    "MemberDecision",
    "MemberStatus",
    "PatternEpoch",
    "next_pattern_epoch",
    "plan_group_tick",
    "validate_runtime_consistency",
]
