from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from jebao_flow.groups.models import GroupState, PatternKind
from jebao_flow.groups.plan import (
    DeviceActuationLimits,
    GroupTickPlan,
    MemberAction,
    MemberDecision,
    PatternEpoch,
)
from jebao_flow.groups.runtime import (
    DeliveryCertainty,
    GroupDispatchError,
    GroupDispatchRuntime,
    PreWriteDispatchError,
    RuntimeClosedError,
    StaleDispatchError,
    UncertainDispatchError,
    UnresolvedSafetyStopError,
)
from jebao_flow.protocol.models import DeviceTarget, LinkageRole

LIMITS = DeviceActuationLimits(min_power=30, max_power=80, power_step=1)


class _CurrentGeneration:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self, generation: int) -> bool:
        return generation == self.value


class _RecordingPort:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.attempts: list[DeviceTarget] = []
        self.writes: list[DeviceTarget] = []
        self.guards: list[object] = []

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        self.writes.append(target)


class _MutableIdentityPort(_RecordingPort):
    pass


class _PreWriteBlockingPort(_RecordingPort):
    """Wait before the last-moment guard, as a real port may wait for its I/O lock."""

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if len(self.attempts) == 1:
            self.first_started.set()
            await self.release_first.wait()
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        self.writes.append(target)


class _PostWriteBlockingPort(_RecordingPort):
    """Hold an acknowledged call after its guarded physical change."""

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.first_written = asyncio.Event()
        self.release_first = asyncio.Event()
        self.cancelled = False

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        self.writes.append(target)
        if len(self.writes) == 1:
            self.first_written.set()
            try:
                await self.release_first.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise


class _PortFailure(PreWriteDispatchError):
    pass


class _AckLost(RuntimeError):
    pass


class _FailingPort(_RecordingPort):
    def __init__(self, device_id: str, *, failures: int = 1) -> None:
        super().__init__(device_id)
        self.failures_remaining = failures

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise _PortFailure("a secret-bearing transport message must not reach diagnostics")
        self.writes.append(target)


class _SelfCancelledPort(_RecordingPort):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.cancel_once = True

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        if self.cancel_once:
            self.cancel_once = False
            raise asyncio.CancelledError
        self.writes.append(target)


class _AckLostAfterWritePort(_RecordingPort):
    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        self.writes.append(target)
        raise _AckLost("the physical write may have succeeded before ACK loss")


class _ConfirmedOnThenAckLostOffPort(_RecordingPort):
    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        self.writes.append(target)
        if len(self.attempts) == 2:
            raise _AckLost("the canonical OFF may have reached the device")


class _AckLostThenBlockingSafetyOffPort(_RecordingPort):
    """Lose the first ON ACK, then hold the safety OFF before its physical write."""

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.off_started = asyncio.Event()
        self.release_off = asyncio.Event()

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if len(self.attempts) == 1:
            if guard is None or guard() is not True:
                raise StaleDispatchError("test port rejected a stale generation")
            self.writes.append(target)
            raise _AckLost("the first enabled write may have reached the device")
        if len(self.attempts) == 2:
            self.off_started.set()
            await self.release_off.wait()
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        self.writes.append(target)


class _AckLostThenPreWriteOffFailurePort(_RecordingPort):
    """Lose an ON ACK, prove the first OFF was not sent, then accept its explicit retry."""

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        if len(self.attempts) == 1:
            self.writes.append(target)
            raise _AckLost("the enabled write may have reached the device")
        if len(self.attempts) == 2:
            raise _PortFailure("the safety OFF was proven not sent")
        self.writes.append(target)


class _AckLostThenAlwaysPreWriteOffFailurePort(_RecordingPort):
    """Apply ON with a lost ACK, then prove every OFF attempt was not sent."""

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        if len(self.attempts) == 1:
            self.writes.append(target)
            raise _AckLost("the enabled write may have reached the device")
        raise _PortFailure("the safety OFF was proven not sent")


class _BlockingAckLostEnabledPort(_RecordingPort):
    """Physically apply the first ON, then hold before reporting its lost ACK."""

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.on_written = asyncio.Event()
        self.release_on = asyncio.Event()

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        self.writes.append(target)
        if len(self.attempts) == 1:
            self.on_written.set()
            await self.release_on.wait()
            raise _AckLost("the enabled write succeeded but its ACK was lost")


class _CachedOffThenBlockingAckLostOnPort(_RecordingPort):
    """Confirm OFF, then hold an applied ON before losing its ACK."""

    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self.on_written = asyncio.Event()
        self.release_on = asyncio.Event()

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a stale generation")
        self.writes.append(target)
        if len(self.attempts) == 2:
            self.on_written.set()
            await self.release_on.wait()
            raise _AckLost("the enabled write succeeded but its ACK was lost")


class _PhysicalBinding:
    def __init__(self) -> None:
        self.current = True

    def __call__(self) -> bool:
        return self.current


class _BindingFlipPort(_RecordingPort):
    def __init__(self, device_id: str, binding: _PhysicalBinding) -> None:
        super().__init__(device_id)
        self.binding = binding

    async def write_target(self, target: DeviceTarget, *, guard=None) -> None:
        self.attempts.append(target)
        self.guards.append(guard)
        self.binding.current = False
        if guard is None or guard() is not True:
            raise StaleDispatchError("test port rejected a changed physical binding")
        self.writes.append(target)


def _target(power: int, *, mode: str | None = None) -> DeviceTarget:
    return DeviceTarget(enabled=True, power=power, mode=mode)


def _off_target() -> DeviceTarget:
    return DeviceTarget(enabled=False, power=0)


def _decision(
    device_id: str,
    target: DeviceTarget,
    *,
    control_epoch: int = 0,
) -> MemberDecision:
    return MemberDecision(
        device=device_id,
        action=MemberAction.DISPATCH,
        requested_target=target,
        target=target,
        requested_power=target.power,
        normalized_power=target.power,
        limits_used=LIMITS,
        effective_max_power=LIMITS.max_power,
        control_epoch=control_epoch,
    )


def _skip_decision(
    device_id: str,
    *,
    control_epoch: int,
    action: MemberAction = MemberAction.SKIP_MANUAL_OVERRIDE,
) -> MemberDecision:
    requested = _target(40)
    return MemberDecision(
        device=device_id,
        action=action,
        requested_target=requested,
        target=None,
        requested_power=requested.power,
        normalized_power=None,
        limits_used=LIMITS,
        effective_max_power=LIMITS.max_power,
        control_epoch=control_epoch,
    )


def _plan(
    generation: int,
    decisions: tuple[MemberDecision, ...],
    *,
    group_id: str = "main_flow",
    derived_status: GroupState = GroupState.RUNNING,
    failure_policy_stopped: bool = False,
) -> GroupTickPlan:
    return GroupTickPlan(
        group_id=group_id,
        generation=generation,
        timestamp=float(generation),
        epoch=PatternEpoch(
            generation=1,
            started_at=0.0,
            pattern=PatternKind.CONSTANT,
            period_seconds=10.0,
        ),
        decisions=decisions,
        derived_status=derived_status,
        offline_devices=(),
        unknown_devices=(),
        manual_override_devices=(),
        failure_policy_stopped=failure_policy_stopped,
    )


def _device(snapshot, device_id: str):
    return next(device for device in snapshot.devices if device.device_id == device_id)


def _runtime(
    *ports: _RecordingPort,
    device_limits: dict[str, DeviceActuationLimits] | None = None,
    identity_guards: dict[str, Callable[[], bool]] | None = None,
) -> GroupDispatchRuntime:
    port_map = {port.device_id: port for port in ports}
    member_ids = tuple(port_map)
    limits = (
        {device_id: LIMITS for device_id in member_ids} if device_limits is None else device_limits
    )
    guards = (
        {device_id: (lambda: True) for device_id in member_ids}
        if identity_guards is None
        else identity_guards
    )
    return GroupDispatchRuntime(
        "main_flow",
        member_ids,
        port_map,
        device_limits=limits,
        identity_guards=guards,
    )


@pytest.mark.asyncio
async def test_latest_pending_target_wins_while_stale_in_flight_write_is_guarded() -> None:
    port = _PreWriteBlockingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)

    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await port.first_started.wait()

    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(50)),)), is_current=current)
    current.value = 3
    runtime.submit(_plan(3, (_decision("left", _target(60)),)), is_current=current)
    port.release_first.set()

    snapshot = await runtime.wait_idle()
    left = _device(snapshot, "left")
    assert port.writes == [_target(60)]
    assert port.attempts == [_target(40), _target(60)]
    assert left.stale_dropped_count == 1
    assert left.superseded_count == 1
    assert left.last_superseded_generation == 2
    assert left.applied_target == _target(60)
    await runtime.aclose()


@pytest.mark.asyncio
async def test_dedupe_uses_control_epoch_and_the_whole_device_target() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)

    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()
    assert port.writes == [_target(40)]

    current.value = 3
    runtime.submit(
        _plan(3, (_decision("left", _target(40, mode="sine")),)),
        is_current=current,
    )
    await runtime.wait_idle()

    current.value = 4
    runtime.submit(
        _plan(4, (_decision("left", _target(40, mode="sine"), control_epoch=1),)),
        is_current=current,
    )
    snapshot = await runtime.wait_idle()

    assert port.writes == [
        _target(40),
        _target(40, mode="sine"),
        _target(40, mode="sine"),
    ]
    assert _device(snapshot, "left").deduplicated_count == 1
    await runtime.aclose()


@pytest.mark.asyncio
async def test_skip_consumes_control_epoch_and_invalidates_applied_state() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)

    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    skipped = runtime.submit(
        _plan(2, (_skip_decision("left", control_epoch=1),)), is_current=current
    )
    assert _device(skipped, "left").applied_target is None

    current.value = 3
    runtime.submit(
        _plan(3, (_decision("left", _target(40), control_epoch=2),)),
        is_current=current,
    )
    snapshot = await runtime.wait_idle()

    assert port.writes == [_target(40), _target(40)]
    assert _device(snapshot, "left").last_control_epoch == 2
    assert _device(snapshot, "left").skip_count == 1
    await runtime.aclose()


@pytest.mark.asyncio
async def test_skip_cannot_be_undone_by_late_success_from_an_in_flight_plan() -> None:
    port = _PostWriteBlockingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await port.first_written.wait()

    current.value = 2
    skipped = runtime.submit(
        _plan(2, (_skip_decision("left", control_epoch=0),)), is_current=current
    )
    assert _device(skipped, "left").applied_target is None
    port.release_first.set()

    final = await runtime.wait_idle()
    assert port.writes == [_target(40)]
    assert _device(final, "left").applied_target is None
    await runtime.aclose()


@pytest.mark.asyncio
async def test_stale_plan_is_rejected_before_runtime_mutation() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(2)
    runtime = _runtime(port)

    with pytest.raises(StaleDispatchError):
        runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)

    snapshot = runtime.snapshot()
    assert snapshot.latest_generation == 0
    assert _device(snapshot, "left").submitted_count == 0
    assert port.attempts == []
    await runtime.aclose()


@pytest.mark.asyncio
async def test_invalid_member_in_batch_rejects_every_member_before_mutation() -> None:
    left = _RecordingPort("left")
    right = _RecordingPort("right")
    current = _CurrentGeneration(1)
    runtime = _runtime(left, right)
    unsafe = replace(
        _decision("right", _target(90)),
        effective_max_power=80,
    )

    with pytest.raises(GroupDispatchError, match="outside effective power limits"):
        runtime.submit(
            _plan(1, (_decision("left", _target(40)), unsafe)),
            is_current=current,
        )

    snapshot = runtime.snapshot()
    assert snapshot.latest_generation == 0
    assert all(device.submitted_count == 0 for device in snapshot.devices)
    assert all(not device.worker_running for device in snapshot.devices)
    assert left.attempts == right.attempts == []
    await runtime.aclose()


@pytest.mark.asyncio
async def test_effective_maximum_cannot_expand_the_validated_device_limit() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    expanded = replace(
        _decision("left", _target(90)),
        effective_max_power=90,
    )

    with pytest.raises(GroupDispatchError, match="effective maximum"):
        runtime.submit(_plan(1, (expanded,)), is_current=current)

    assert port.attempts == []
    assert runtime.snapshot().latest_generation == 0
    await runtime.aclose()


@pytest.mark.asyncio
async def test_plan_cannot_raise_runtime_bound_limits_by_changing_all_claimed_values() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    claimed_limits = DeviceActuationLimits(min_power=30, max_power=100, power_step=1)
    claimed = replace(
        _decision("left", _target(90)),
        limits_used=claimed_limits,
        effective_max_power=100,
    )

    with pytest.raises(GroupDispatchError, match="runtime-bound device limits"):
        runtime.submit(_plan(1, (claimed,)), is_current=current)

    assert port.attempts == []
    assert runtime.snapshot().latest_generation == 0
    await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_off",
    [
        DeviceTarget(enabled=False, power=0, mode="sine"),
        DeviceTarget(enabled=False, power=0, frequency=1),
        DeviceTarget(enabled=False, power=0, linkage=LinkageRole.MASTER),
        DeviceTarget(enabled=False, power=0, timer_enabled=True),
    ],
)
async def test_disabled_target_with_optional_side_effect_is_rejected_atomically(
    unsafe_off: DeviceTarget,
) -> None:
    left = _RecordingPort("left")
    right = _RecordingPort("right")
    current = _CurrentGeneration(1)
    runtime = _runtime(left, right)
    unsafe = replace(
        _decision("right", unsafe_off),
        requested_power=0,
        normalized_power=0,
    )

    with pytest.raises(GroupDispatchError, match="canonical OFF"):
        runtime.submit(
            _plan(1, (_decision("left", _target(40)), unsafe)),
            is_current=current,
        )

    snapshot = runtime.snapshot()
    assert snapshot.latest_generation == 0
    assert all(device.submitted_count == 0 for device in snapshot.devices)
    assert left.attempts == right.attempts == []
    await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_target",
    [
        DeviceTarget(enabled=True, power=40, linkage=LinkageRole.MASTER),
        DeviceTarget(enabled=True, power=40, timer_enabled=True),
    ],
)
async def test_software_independent_target_cannot_change_native_ownership_atomically(
    unsafe_target: DeviceTarget,
) -> None:
    left = _RecordingPort("left")
    right = _RecordingPort("right")
    current = _CurrentGeneration(1)
    runtime = _runtime(left, right)

    with pytest.raises(GroupDispatchError, match="linkage or timer ownership"):
        runtime.submit(
            _plan(
                1,
                (
                    _decision("left", _target(40)),
                    _decision("right", unsafe_target),
                ),
            ),
            is_current=current,
        )

    snapshot = runtime.snapshot()
    assert snapshot.latest_generation == 0
    assert all(device.submitted_count == 0 for device in snapshot.devices)
    assert left.attempts == right.attempts == []
    await runtime.aclose()


@pytest.mark.asyncio
async def test_normalization_cannot_turn_requested_off_into_enabled_target() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    forged = replace(
        _decision("left", _target(80)),
        requested_target=_off_target(),
        requested_power=0,
    )

    with pytest.raises(GroupDispatchError, match="requested OFF"):
        runtime.submit(_plan(1, (forged,)), is_current=current)

    assert runtime.snapshot().latest_generation == 0
    assert port.attempts == []
    await runtime.aclose()


@pytest.mark.asyncio
async def test_normalization_cannot_change_non_power_attributes() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    forged = replace(
        _decision("left", _target(40, mode="pulse")),
        requested_target=_target(40, mode="sine"),
    )

    with pytest.raises(GroupDispatchError, match="non-power"):
        runtime.submit(_plan(1, (forged,)), is_current=current)

    assert runtime.snapshot().latest_generation == 0
    assert port.attempts == []
    await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("derived_status", [GroupState.STOPPED, GroupState.EMERGENCY_STOP])
async def test_declared_stop_state_cannot_dispatch_enabled_target(
    derived_status: GroupState,
) -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)

    with pytest.raises(GroupDispatchError, match="canonical OFF for every member"):
        runtime.submit(
            _plan(
                1,
                (_decision("left", _target(40)),),
                derived_status=derived_status,
            ),
            is_current=current,
        )

    assert runtime.snapshot().latest_generation == 0
    assert port.attempts == []
    await runtime.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("derived_status", [GroupState.STARTING, GroupState.MAINTENANCE])
async def test_declared_held_state_cannot_dispatch_enabled_target(
    derived_status: GroupState,
) -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)

    with pytest.raises(GroupDispatchError, match="held group states"):
        runtime.submit(
            _plan(
                1,
                (_decision("left", _target(40)),),
                derived_status=derived_status,
            ),
            is_current=current,
        )

    assert runtime.snapshot().latest_generation == 0
    assert port.attempts == []
    await runtime.aclose()


@pytest.mark.asyncio
async def test_starting_plan_may_stop_a_config_disabled_member() -> None:
    active = _RecordingPort("active")
    spare = _RecordingPort("spare")
    current = _CurrentGeneration(1)
    runtime = _runtime(active, spare)

    runtime.submit(
        _plan(
            1,
            (
                _skip_decision(
                    "active",
                    control_epoch=0,
                    action=MemberAction.SKIP_UNKNOWN,
                ),
                _decision("spare", _off_target()),
            ),
            derived_status=GroupState.STARTING,
        ),
        is_current=current,
    )
    snapshot = await runtime.wait_idle()

    assert active.attempts == []
    assert spare.writes == [_off_target()]
    assert _device(snapshot, "spare").applied_target == _off_target()
    await runtime.aclose()


@pytest.mark.asyncio
async def test_failure_policy_stop_cannot_dispatch_enabled_target() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)

    with pytest.raises(GroupDispatchError, match="failure-policy stopped"):
        runtime.submit(
            _plan(
                1,
                (_decision("left", _target(40)),),
                derived_status=GroupState.ERROR,
                failure_policy_stopped=True,
            ),
            is_current=current,
        )

    assert runtime.snapshot().latest_generation == 0
    assert port.attempts == []
    await runtime.aclose()


@pytest.mark.asyncio
async def test_failure_policy_stop_cannot_leave_manual_override_member_running() -> None:
    left = _RecordingPort("left")
    right = _RecordingPort("right")
    current = _CurrentGeneration(1)
    runtime = _runtime(left, right)

    runtime.submit(
        _plan(
            1,
            (
                _decision("left", _target(40)),
                _decision("right", _target(40)),
            ),
        ),
        is_current=current,
    )
    await runtime.wait_idle()

    current.value = 2
    with pytest.raises(GroupDispatchError, match="canonical OFF or unavailable skips"):
        runtime.submit(
            _plan(
                2,
                (
                    _skip_decision(
                        "left",
                        control_epoch=1,
                        action=MemberAction.SKIP_MANUAL_OVERRIDE,
                    ),
                    _decision("right", _off_target(), control_epoch=1),
                ),
                derived_status=GroupState.ERROR,
                failure_policy_stopped=True,
            ),
            is_current=current,
        )

    assert left.writes == [_target(40)]
    assert right.writes == [_target(40)]
    assert runtime.snapshot().latest_generation == 1
    await runtime.aclose()


@pytest.mark.asyncio
async def test_failure_policy_stop_accepts_unavailable_skip_and_online_member_off() -> None:
    offline = _RecordingPort("offline")
    online = _RecordingPort("online")
    current = _CurrentGeneration(1)
    runtime = _runtime(offline, online)

    runtime.submit(
        _plan(
            1,
            (
                _skip_decision(
                    "offline",
                    control_epoch=0,
                    action=MemberAction.SKIP_OFFLINE,
                ),
                _decision("online", _off_target()),
            ),
            derived_status=GroupState.ERROR,
            failure_policy_stopped=True,
        ),
        is_current=current,
    )
    snapshot = await runtime.wait_idle()

    assert offline.attempts == []
    assert online.writes == [_off_target()]
    assert _device(snapshot, "online").applied_target == _off_target()
    await runtime.aclose()


def test_constructor_rejects_wrong_port_identity() -> None:
    wrong_port = _RecordingPort("right")

    with pytest.raises(GroupDispatchError, match="does not match"):
        GroupDispatchRuntime(
            "main_flow",
            ("left",),
            {"left": wrong_port},
            device_limits={"left": LIMITS},
            identity_guards={"left": lambda: True},
        )


@pytest.mark.asyncio
async def test_port_identity_is_checked_again_immediately_before_dispatch() -> None:
    port = _MutableIdentityPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    port.device_id = "right"

    snapshot = await runtime.wait_idle()
    failure = _device(snapshot, "left").failure
    assert failure is not None
    assert failure.exception_type == "PreWriteDispatchError"
    assert failure.delivery_certainty is DeliveryCertainty.NOT_SENT
    assert port.attempts == []
    await runtime.aclose()


@pytest.mark.asyncio
async def test_combined_guard_detects_physical_binding_change_inside_port_call() -> None:
    binding = _PhysicalBinding()
    port = _BindingFlipPort("left", binding)
    current = _CurrentGeneration(1)
    runtime = _runtime(port, identity_guards={"left": binding})
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)

    snapshot = await runtime.wait_idle()
    assert port.attempts == [_target(40)]
    assert port.writes == []
    assert _device(snapshot, "left").stale_dropped_count == 1
    assert _device(snapshot, "left").failure is None
    await runtime.aclose()


@pytest.mark.asyncio
async def test_failure_latches_without_retry_until_explicit_retry_latest() -> None:
    port = _FailingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)

    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    first = await runtime.wait_idle()
    first_failure = _device(first, "left").failure
    assert first_failure is not None
    assert first_failure.exception_type == "_PortFailure"
    assert first_failure.delivery_certainty is DeliveryCertainty.NOT_SENT
    assert first_failure.retry_safe is True
    assert not hasattr(first_failure, "message")
    assert _device(first, "left").applied_target is None

    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(50)),)), is_current=current)
    await asyncio.sleep(0)
    assert port.attempts == [_target(40)]

    runtime.retry_latest("left")
    final = await runtime.wait_idle()
    assert port.attempts == [_target(40), _target(50)]
    assert port.writes == [_target(50)]
    assert _device(final, "left").failure is None
    assert _device(final, "left").failure_suppressed_count == 1
    await runtime.aclose()


@pytest.mark.asyncio
async def test_port_cancelled_result_is_latched_without_killing_the_device_worker() -> None:
    port = _SelfCancelledPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)

    failed = await runtime.wait_idle()
    failure = _device(failed, "left").failure
    assert failure is not None
    assert failure.exception_type == "CancelledError"
    assert failure.delivery_certainty is DeliveryCertainty.UNCERTAIN
    assert failure.retry_safe is False
    assert _device(failed, "left").worker_running is True

    with pytest.raises(UncertainDispatchError):
        runtime.retry_latest("left")
    assert port.writes == []
    await runtime.aclose()


@pytest.mark.asyncio
async def test_ack_lost_after_physical_write_can_never_be_retried() -> None:
    port = _AckLostAfterWritePort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)

    failed = await runtime.wait_idle()
    failure = _device(failed, "left").failure
    assert port.writes == [_target(40)]
    assert failure is not None
    assert failure.exception_type == "_AckLost"
    assert failure.delivery_certainty is DeliveryCertainty.UNCERTAIN

    with pytest.raises(UncertainDispatchError):
        runtime.retry_latest("left")
    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(40)),)), is_current=current)
    await asyncio.sleep(0)
    assert port.attempts == [_target(40)]
    await runtime.aclose()


@pytest.mark.asyncio
async def test_uncertain_enabled_write_allows_one_non_cancelable_canonical_off() -> None:
    port = _AckLostThenBlockingSafetyOffPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    failed = await runtime.wait_idle()
    assert _device(failed, "left").failure is not None

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    await port.off_started.wait()

    current.value = 3
    runtime.submit(
        _plan(3, (_skip_decision("left", control_epoch=2),)),
        is_current=current,
    )
    current.value = 4
    runtime.submit(
        _plan(4, (_decision("left", _target(60), control_epoch=3),)),
        is_current=current,
    )
    port.release_off.set()

    stopped = await runtime.wait_idle()
    left = _device(stopped, "left")
    assert port.writes == [_target(40), _off_target()]
    assert left.failure is None
    assert left.applied_target == _off_target()
    assert left.safety_off_bypass_count == 1
    assert left.safety_off_succeeded_count == 1
    assert left.safety_off_required is False
    assert left.safety_off_origin_failure is None
    assert left.last_safety_recovered_failure is not None
    assert left.last_safety_recovered_failure.target == _target(40)

    current.value = 5
    runtime.submit(
        _plan(5, (_decision("left", _target(60), control_epoch=4),)),
        is_current=current,
    )
    await runtime.wait_idle()
    assert port.writes == [_target(40), _off_target(), _target(60)]
    await runtime.aclose()


@pytest.mark.asyncio
async def test_proven_not_sent_enabled_write_also_allows_canonical_off() -> None:
    port = _FailingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    failed = await runtime.wait_idle()
    assert _device(failed, "left").failure is not None
    assert _device(failed, "left").failure.delivery_certainty is DeliveryCertainty.NOT_SENT

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    stopped = await runtime.wait_idle()

    assert port.attempts == [_target(40), _off_target()]
    assert port.writes == [_off_target()]
    assert _device(stopped, "left").failure is None
    assert _device(stopped, "left").applied_target == _off_target()
    await runtime.aclose()


@pytest.mark.asyncio
async def test_off_behind_in_flight_enabled_write_survives_ack_loss_and_newer_on() -> None:
    port = _BlockingAckLostEnabledPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await port.on_written.wait()

    current.value = 2
    armed = runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    assert _device(armed, "left").safety_off_required is True

    current.value = 3
    runtime.submit(
        _plan(3, (_decision("left", _target(70), control_epoch=2),)),
        is_current=current,
    )
    port.release_on.set()

    stopped = await runtime.wait_idle()
    left = _device(stopped, "left")
    assert port.writes == [_target(40), _off_target()]
    assert left.failure is None
    assert left.applied_target == _off_target()
    assert left.safety_off_succeeded_count == 1
    assert left.last_safety_recovered_failure is not None
    assert left.last_safety_recovered_failure.delivery_certainty is DeliveryCertainty.UNCERTAIN
    await runtime.aclose()


@pytest.mark.asyncio
async def test_mandatory_off_is_not_deduped_against_cached_off_at_same_control_epoch() -> None:
    port = _CachedOffThenBlockingAckLostOnPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _off_target()),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(40)),)), is_current=current)
    await port.on_written.wait()

    current.value = 3
    runtime.submit(_plan(3, (_decision("left", _off_target()),)), is_current=current)
    port.release_on.set()
    stopped = await runtime.wait_idle()

    assert port.writes == [_off_target(), _target(40), _off_target()]
    assert _device(stopped, "left").applied_target == _off_target()
    assert _device(stopped, "left").failure is None
    assert _device(stopped, "left").safety_off_succeeded_count == 2
    await runtime.aclose()


@pytest.mark.asyncio
async def test_cached_off_cancels_pending_on_before_off_deduplication() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _off_target()),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(40)),)), is_current=current)
    current.value = 3
    runtime.submit(_plan(3, (_decision("left", _off_target()),)), is_current=current)
    stopped = await runtime.wait_idle()

    assert port.writes == [_off_target()]
    assert _device(stopped, "left").applied_target == _off_target()
    assert _device(stopped, "left").superseded_count == 1
    assert _device(stopped, "left").deduplicated_count == 1
    await runtime.aclose()


@pytest.mark.asyncio
async def test_uncertain_safety_off_is_never_automatically_retried() -> None:
    port = _AckLostAfterWritePort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    failed_off = await runtime.wait_idle()
    failure = _device(failed_off, "left").failure
    assert failure is not None
    assert failure.target == _off_target()
    assert failure.delivery_certainty is DeliveryCertainty.UNCERTAIN
    assert _device(failed_off, "left").safety_off_required is True

    current.value = 3
    runtime.submit(
        _plan(3, (_decision("left", _off_target(), control_epoch=2),)),
        is_current=current,
    )
    current.value = 4
    runtime.submit(
        _plan(4, (_decision("left", _target(50), control_epoch=3),)),
        is_current=current,
    )
    await asyncio.sleep(0)
    with pytest.raises(UncertainDispatchError):
        runtime.retry_latest("left")
    assert port.writes == [_target(40), _off_target()]
    with pytest.raises(UnresolvedSafetyStopError):
        await runtime.aclose()


@pytest.mark.asyncio
async def test_uncertain_off_after_confirmed_on_arms_barrier_and_blocks_resume() -> None:
    port = _ConfirmedOnThenAckLostOffPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    stopped = await runtime.wait_idle()
    failure = _device(stopped, "left").failure

    assert port.writes == [_target(40), _off_target()]
    assert failure is not None
    assert failure.target == _off_target()
    assert failure.delivery_certainty is DeliveryCertainty.UNCERTAIN
    assert _device(stopped, "left").safety_off_required is True

    current.value = 3
    runtime.submit(
        _plan(3, (_decision("left", _target(70), control_epoch=2),)),
        is_current=current,
    )
    await asyncio.sleep(0)
    assert port.writes == [_target(40), _off_target()]
    with pytest.raises(UnresolvedSafetyStopError):
        await runtime.aclose()


@pytest.mark.asyncio
async def test_proven_not_sent_safety_off_retries_that_exact_off_after_newer_on() -> None:
    port = _AckLostThenPreWriteOffFailurePort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    failed_off = await runtime.wait_idle()
    failure = _device(failed_off, "left").failure
    assert failure is not None
    assert failure.target == _off_target()
    assert failure.delivery_certainty is DeliveryCertainty.NOT_SENT

    current.value = 3
    runtime.submit(
        _plan(3, (_decision("left", _target(70), control_epoch=2),)),
        is_current=current,
    )
    runtime.retry_latest("left")
    stopped = await runtime.wait_idle()

    assert port.attempts == [_target(40), _off_target(), _off_target()]
    assert port.writes == [_target(40), _off_target()]
    assert _device(stopped, "left").failure is None
    assert _device(stopped, "left").applied_target == _off_target()
    await runtime.aclose()


@pytest.mark.asyncio
async def test_invalidate_during_mandatory_off_keeps_an_uncertain_off_barrier() -> None:
    port = _AckLostThenBlockingSafetyOffPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    await port.off_started.wait()
    runtime.invalidate_applied("left")
    port.release_off.set()
    invalidated = await runtime.wait_idle()

    failure = _device(invalidated, "left").failure
    assert port.writes == [_target(40), _off_target()]
    assert failure is not None
    assert failure.target == _off_target()
    assert failure.exception_type == "_AppliedStateInvalidatedDuringWrite"
    assert failure.delivery_certainty is DeliveryCertainty.UNCERTAIN
    assert _device(invalidated, "left").safety_off_required is True

    current.value = 3
    runtime.submit(
        _plan(3, (_decision("left", _target(70), control_epoch=2),)),
        is_current=current,
    )
    await asyncio.sleep(0)
    with pytest.raises(UncertainDispatchError):
        runtime.retry_latest("left")
    assert port.writes == [_target(40), _off_target()]
    with pytest.raises(UnresolvedSafetyStopError):
        await runtime.aclose()


@pytest.mark.asyncio
async def test_mandatory_off_does_not_block_another_device_latest_target() -> None:
    left = _AckLostThenBlockingSafetyOffPort("left")
    right = _RecordingPort("right")
    current = _CurrentGeneration(1)
    runtime = _runtime(left, right)
    runtime.submit(
        _plan(
            1,
            (
                _decision("left", _target(40)),
                _decision("right", _target(40)),
            ),
        ),
        is_current=current,
    )
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(
        _plan(
            2,
            (
                _decision("left", _off_target(), control_epoch=1),
                _decision("right", _target(50)),
            ),
        ),
        is_current=current,
    )
    await left.off_started.wait()

    current.value = 3
    runtime.submit(
        _plan(
            3,
            (
                _decision("left", _target(70), control_epoch=2),
                _decision("right", _target(60)),
            ),
        ),
        is_current=current,
    )
    left.release_off.set()
    snapshot = await runtime.wait_idle()

    assert left.writes == [_target(40), _off_target()]
    assert right.writes[-1] == _target(60)
    assert _device(snapshot, "left").failure is None
    assert _device(snapshot, "right").applied_target == _target(60)
    await runtime.aclose()


@pytest.mark.asyncio
async def test_partial_failure_does_not_block_other_device_worker() -> None:
    left = _FailingPort("left")
    right = _RecordingPort("right")
    current = _CurrentGeneration(1)
    runtime = _runtime(left, right)

    runtime.submit(
        _plan(
            1,
            (
                _decision("left", _target(40)),
                _decision("right", _target(50)),
            ),
        ),
        is_current=current,
    )
    snapshot = await runtime.wait_idle()

    assert [failure.device_id for failure in snapshot.failures] == ["left"]
    assert right.writes == [_target(50)]
    assert _device(snapshot, "right").write_succeeded_count == 1
    await runtime.aclose()


@pytest.mark.asyncio
async def test_local_stale_exception_does_not_latch_failure() -> None:
    port = _PreWriteBlockingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await port.first_started.wait()
    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(50)),)), is_current=current)
    port.release_first.set()

    snapshot = await runtime.wait_idle()
    assert _device(snapshot, "left").failure is None
    assert _device(snapshot, "left").stale_dropped_count == 1
    assert port.writes == [_target(50)]
    await runtime.aclose()


@pytest.mark.asyncio
async def test_invalidate_applied_requires_a_later_plan_and_then_rewrites() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    invalidated = runtime.invalidate_applied("left")
    assert _device(invalidated, "left").applied_target is None
    await asyncio.sleep(0)
    assert port.writes == [_target(40)]

    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()
    assert port.writes == [_target(40), _target(40)]
    await runtime.aclose()


@pytest.mark.asyncio
async def test_invalidate_applied_drops_pending_before_it_can_write() -> None:
    port = _PreWriteBlockingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await port.first_started.wait()

    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(50)),)), is_current=current)
    invalidated = runtime.invalidate_applied("left")
    assert _device(invalidated, "left").pending_target is None
    assert _device(invalidated, "left").superseded_count == 1
    port.release_first.set()
    await runtime.wait_idle()

    current.value = 3
    runtime.submit(_plan(3, (_decision("left", _target(50)),)), is_current=current)
    await runtime.wait_idle()
    assert port.writes == [_target(50)]
    await runtime.aclose()


@pytest.mark.asyncio
async def test_invalidate_during_successful_in_flight_write_latches_uncertain_state() -> None:
    port = _PostWriteBlockingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await port.first_written.wait()

    runtime.invalidate_applied("left")
    port.release_first.set()
    failed = await runtime.wait_idle()
    failure = _device(failed, "left").failure
    assert failure is not None
    assert failure.exception_type == "_AppliedStateInvalidatedDuringWrite"
    assert failure.delivery_certainty is DeliveryCertainty.UNCERTAIN
    assert _device(failed, "left").applied_target is None

    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(40)),)), is_current=current)
    await asyncio.sleep(0)
    assert port.writes == [_target(40)]
    with pytest.raises(UncertainDispatchError):
        runtime.retry_latest("left")
    await runtime.aclose()


@pytest.mark.asyncio
async def test_shutdown_drops_pending_without_off_and_awaits_uncancelled_in_flight() -> None:
    port = _PostWriteBlockingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await port.first_written.wait()

    current.value = 2
    runtime.submit(_plan(2, (_decision("left", _target(50)),)), is_current=current)
    closing = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    assert _device(runtime.snapshot(), "left").shutdown_dropped_count == 1

    port.release_first.set()
    await closing

    assert port.writes == [_target(40)]
    assert all(target.power != 0 for target in port.attempts)
    assert port.cancelled is False
    with pytest.raises(RuntimeClosedError):
        runtime.submit(_plan(2, (_decision("left", _target(50)),)), is_current=current)


@pytest.mark.asyncio
async def test_shutdown_drains_already_accepted_mandatory_off_without_synthesizing_one() -> None:
    port = _BlockingAckLostEnabledPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await port.on_written.wait()

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    closing = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    assert _device(runtime.snapshot(), "left").shutdown_dropped_count == 0
    assert not closing.done()
    port.release_on.set()
    await closing

    closed = _device(runtime.snapshot(), "left")
    assert port.writes == [_target(40), _off_target()]
    assert closed.failure is None
    assert closed.safety_off_required is False
    assert closed.applied_target == _off_target()
    assert closed.pending_target is None


@pytest.mark.asyncio
async def test_shutdown_promotes_and_drains_pending_off_after_confirmed_on() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    await runtime.aclose()

    closed = _device(runtime.snapshot(), "left")
    assert port.writes == [_target(40), _off_target()]
    assert closed.safety_off_required is False
    assert closed.applied_target == _off_target()
    assert closed.shutdown_dropped_count == 0


@pytest.mark.asyncio
async def test_shutdown_retries_not_sent_mandatory_off_once_then_succeeds() -> None:
    port = _AckLostThenPreWriteOffFailurePort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    await runtime.aclose()

    closed = _device(runtime.snapshot(), "left")
    assert port.attempts == [_target(40), _off_target(), _off_target()]
    assert port.writes == [_target(40), _off_target()]
    assert closed.shutdown_safety_retry_count == 1
    assert closed.safety_off_required is False
    assert closed.failure is None


@pytest.mark.asyncio
async def test_shutdown_retries_already_idle_not_sent_mandatory_off_once() -> None:
    port = _AckLostThenPreWriteOffFailurePort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    failed = await runtime.wait_idle()
    assert _device(failed, "left").failure is not None

    await runtime.aclose()

    closed = _device(runtime.snapshot(), "left")
    assert port.attempts == [_target(40), _off_target(), _off_target()]
    assert port.writes == [_target(40), _off_target()]
    assert closed.shutdown_safety_retry_count == 1
    assert closed.safety_off_required is False
    assert closed.failure is None


@pytest.mark.asyncio
async def test_shutdown_reports_unresolved_off_after_one_bounded_not_sent_retry() -> None:
    port = _AckLostThenAlwaysPreWriteOffFailurePort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    with pytest.raises(UnresolvedSafetyStopError) as raised:
        await runtime.aclose()

    closed = _device(runtime.snapshot(), "left")
    assert raised.value.device_ids == ("left",)
    assert port.attempts == [_target(40), _off_target(), _off_target()]
    assert port.writes == [_target(40)]
    assert closed.shutdown_safety_retry_count == 1
    assert closed.safety_off_required is True
    assert closed.failure is not None
    assert closed.failure.delivery_certainty is DeliveryCertainty.NOT_SENT


@pytest.mark.asyncio
async def test_shutdown_reports_idle_unresolved_off_after_one_bounded_retry() -> None:
    port = _AckLostThenAlwaysPreWriteOffFailurePort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await runtime.wait_idle()

    current.value = 2
    runtime.submit(
        _plan(2, (_decision("left", _off_target(), control_epoch=1),)),
        is_current=current,
    )
    await runtime.wait_idle()

    with pytest.raises(UnresolvedSafetyStopError):
        await runtime.aclose()

    closed = _device(runtime.snapshot(), "left")
    assert port.attempts == [_target(40), _off_target(), _off_target()]
    assert port.writes == [_target(40)]
    assert closed.shutdown_safety_retry_count == 1
    assert closed.safety_off_required is True
    assert closed.failure is not None
    assert closed.failure.delivery_certainty is DeliveryCertainty.NOT_SENT


@pytest.mark.asyncio
async def test_cancelled_close_still_waits_without_cancelling_in_flight_port() -> None:
    port = _PostWriteBlockingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await port.first_written.wait()

    closing = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    assert port.cancelled is False
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()
    assert port.cancelled is False

    port.release_first.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert port.writes == [_target(40)]
    assert port.cancelled is False
    await runtime.aclose()


@pytest.mark.asyncio
async def test_close_awaits_already_in_flight_prewrite_wait_without_synthesizing_off() -> None:
    port = _PreWriteBlockingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    runtime.submit(_plan(1, (_decision("left", _target(40)),)), is_current=current)
    await port.first_started.wait()

    closing = asyncio.create_task(runtime.aclose())
    await asyncio.sleep(0)
    assert not closing.done()
    assert port.writes == []

    port.release_first.set()
    await closing
    assert port.writes == [_target(40)]
    assert all(target.enabled for target in port.writes)


@pytest.mark.asyncio
async def test_same_generation_may_only_be_replayed_with_the_same_plan() -> None:
    port = _RecordingPort("left")
    current = _CurrentGeneration(1)
    runtime = _runtime(port)
    original = _plan(1, (_decision("left", _target(40)),))
    runtime.submit(original, is_current=current)
    runtime.submit(original, is_current=current)

    with pytest.raises(GroupDispatchError, match="two different plans"):
        runtime.submit(
            _plan(1, (_decision("left", _target(50)),)),
            is_current=current,
        )

    await runtime.wait_idle()
    assert port.writes == [_target(40)]
    await runtime.aclose()


def test_runtime_import_graph_has_no_device_or_transport_dependency() -> None:
    runtime_path = Path("src/jebao_flow/groups/runtime.py")
    source = runtime_path.read_text(encoding="utf-8")
    imports = {
        alias.name
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    forbidden_imports = {
        "jebao_flow.app",
        "jebao_flow.schedule_flow_experiment_cli",
        "jebao_flow.schedule_linkage_cli",
        "jebao_flow.protocol.connection",
        "jebao_flow.protocol.control_session",
        "jebao_flow.protocol.discovery",
        "jebao_flow.devices.linkage",
        "jebao_flow.devices.schedule_flow_experiment",
        "jebao_flow.devices.schedule_linkage",
        "jebao_flow.devices.schedule_transaction",
    }
    assert not any(name.startswith(("jebao_flow.devices", "jebao_flow.mqtt")) for name in imports)
    assert not imports & forbidden_imports

    script = """
import json
import sys
import jebao_flow.groups.runtime
forbidden = sorted(
    name for name in sys.modules
    if name.startswith("jebao_flow.devices")
    or name.startswith("jebao_flow.mqtt")
    or name in {
        "jebao_flow.app",
        "jebao_flow.schedule_flow_experiment_cli",
        "jebao_flow.schedule_linkage_cli",
        "jebao_flow.protocol.connection",
        "jebao_flow.protocol.control_session",
        "jebao_flow.protocol.discovery",
    }
)
print(json.dumps(forbidden))
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        cwd=Path.cwd(),
        env={"PYTHONPATH": str(Path("src").resolve())},
        check=False,
        capture_output=True,
        text=True,
    )
    # Isolated mode deliberately ignores PYTHONPATH.  Invoke through the installed test environment
    # when available, and fall back to a source insertion that still starts in a fresh interpreter.
    if result.returncode != 0:
        source_script = f"import sys; sys.path.insert(0, {str(Path('src').resolve())!r}); {script}"
        result = subprocess.run(
            [sys.executable, "-I", "-c", source_script],
            cwd=Path.cwd(),
            check=True,
            capture_output=True,
            text=True,
        )
    assert result.stdout.strip() == "[]"
