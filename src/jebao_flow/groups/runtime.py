"""Protocol-neutral asynchronous dispatch for software-independent groups.

The planner owns safety normalization.  This module owns only concurrency and delivery:
one worker and one replaceable pending slot per logical device, whole-target de-duplication,
generation fencing, and explicit recovery from write failures.  Every non-deduplicated
planner-canonical OFF gets a per-device barrier: identity fencing remains active, but later ordinary
generations cannot cancel the stop.

There is deliberately no import from :mod:`jebao_flow.devices` or a transport module.  A caller
must inject ports whose ``write_target`` implementation performs its own bounded I/O and checks
the supplied guard immediately before changing the physical device.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from jebao_flow.groups.models import GroupState
from jebao_flow.groups.plan import (
    DeviceActuationLimits,
    GroupTickPlan,
    MemberAction,
    MemberDecision,
)
from jebao_flow.protocol.models import DeviceTarget

WriteGuard = Callable[[], bool]
GenerationPredicate = Callable[[int], bool]


class GroupDevicePort(Protocol):
    """Smallest device boundary accepted by the group dispatcher."""

    @property
    def device_id(self) -> str: ...

    async def write_target(
        self,
        target: DeviceTarget,
        *,
        guard: WriteGuard | None = None,
    ) -> None:
        """Write once, checking ``guard`` immediately before the physical change."""


class GroupDispatchError(RuntimeError):
    """A plan or runtime operation violates the dispatch contract."""


class RuntimeClosedError(GroupDispatchError):
    """The dispatcher no longer accepts work."""


class StaleDispatchError(GroupDispatchError):
    """A generation lost ownership before its device write."""


class PreWriteDispatchError(GroupDispatchError):
    """A port proves that an operation failed before any physical write was sent.

    Port adapters must never use this type for an ACK timeout or any other outcome where delivery
    is uncertain.  It is the only failure category eligible for :meth:`retry_latest`.
    """


class UncertainDispatchError(GroupDispatchError):
    """A requested retry could duplicate a write whose delivery is uncertain."""


class UnresolvedSafetyStopError(GroupDispatchError):
    """Shutdown ended with one or more mandatory OFF outcomes unresolved."""

    def __init__(self, device_ids: tuple[str, ...]) -> None:
        self.device_ids = device_ids
        super().__init__("mandatory safety OFF remains unresolved for: " + ", ".join(device_ids))


class DeliveryCertainty(StrEnum):
    NOT_SENT = "not_sent"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class DispatchFailure:
    """Redacted failure latch; exception messages are intentionally not retained."""

    device_id: str
    generation: int
    control_epoch: int
    target: DeviceTarget
    exception_type: str
    delivery_certainty: DeliveryCertainty

    @property
    def retry_safe(self) -> bool:
        return self.delivery_certainty is DeliveryCertainty.NOT_SENT


@dataclass(frozen=True, slots=True)
class DeviceDispatchSnapshot:
    device_id: str
    last_control_epoch: int
    applied_control_epoch: int | None
    applied_target: DeviceTarget | None
    pending_generation: int | None
    pending_control_epoch: int | None
    pending_target: DeviceTarget | None
    in_flight_generation: int | None
    in_flight_control_epoch: int | None
    in_flight_target: DeviceTarget | None
    failure: DispatchFailure | None
    submitted_count: int
    write_started_count: int
    write_succeeded_count: int
    deduplicated_count: int
    superseded_count: int
    last_superseded_generation: int | None
    stale_dropped_count: int
    failure_count: int
    failure_suppressed_count: int
    safety_off_deferred_count: int
    safety_off_bypass_count: int
    safety_off_succeeded_count: int
    safety_off_required: bool
    safety_off_origin_failure: DispatchFailure | None
    last_safety_recovered_failure: DispatchFailure | None
    skip_count: int
    invalidated_count: int
    shutdown_dropped_count: int
    shutdown_safety_retry_count: int
    worker_running: bool


@dataclass(frozen=True, slots=True)
class GroupDispatchSnapshot:
    group_id: str
    closed: bool
    latest_generation: int
    devices: tuple[DeviceDispatchSnapshot, ...]

    @property
    def failures(self) -> tuple[DispatchFailure, ...]:
        return tuple(device.failure for device in self.devices if device.failure is not None)


_DedupeKey = tuple[int, DeviceTarget]


@dataclass(frozen=True, slots=True)
class _Dispatch:
    generation: int
    control_epoch: int
    target: DeviceTarget
    guard: WriteGuard
    safety_token: object
    cache_revision: int

    @property
    def key(self) -> _DedupeKey:
        return (self.control_epoch, self.target)


@dataclass(slots=True)
class _DeviceSlot:
    port: GroupDevicePort
    trusted_limits: DeviceActuationLimits
    identity_guard: WriteGuard
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    pending: _Dispatch | None = None
    in_flight: _Dispatch | None = None
    latest: _Dispatch | None = None
    applied_key: _DedupeKey | None = None
    failure: DispatchFailure | None = None
    failed_job: _Dispatch | None = None
    mandatory_off_token: object | None = None
    safety_off_origin_failure: DispatchFailure | None = None
    last_safety_recovered_failure: DispatchFailure | None = None
    last_control_epoch: int = -1
    cache_revision: int = 0
    submitted_count: int = 0
    write_started_count: int = 0
    write_succeeded_count: int = 0
    deduplicated_count: int = 0
    superseded_count: int = 0
    last_superseded_generation: int | None = None
    stale_dropped_count: int = 0
    failure_count: int = 0
    failure_suppressed_count: int = 0
    safety_off_deferred_count: int = 0
    safety_off_bypass_count: int = 0
    safety_off_succeeded_count: int = 0
    skip_count: int = 0
    invalidated_count: int = 0
    shutdown_dropped_count: int = 0
    shutdown_safety_retry_count: int = 0
    invalidated_in_flight: _Dispatch | None = None


class _AppliedStateInvalidatedDuringWrite(RuntimeError):
    """Internal redacted marker for an externally invalidated in-flight outcome."""


class GroupDispatchRuntime:
    """Dispatch immutable group plans through independent single-slot workers.

    ``submit`` is synchronous on purpose: whole-plan validation and all queue mutations happen
    without an ``await`` boundary.  Worker tasks cannot observe a partially consumed plan.
    """

    def __init__(
        self,
        group_id: str,
        member_ids: Iterable[str],
        ports: Mapping[str, GroupDevicePort],
        *,
        device_limits: Mapping[str, DeviceActuationLimits],
        identity_guards: Mapping[str, WriteGuard],
    ) -> None:
        if not isinstance(group_id, str) or not group_id:
            raise GroupDispatchError("group id must be a non-empty string")
        members = tuple(member_ids)
        if not members or any(
            not isinstance(device_id, str) or not device_id for device_id in members
        ):
            raise GroupDispatchError("member ids must be non-empty strings")
        if len(set(members)) != len(members):
            raise GroupDispatchError("member ids must be unique")
        if any(not isinstance(device_id, str) or not device_id for device_id in ports):
            raise GroupDispatchError("port keys must be non-empty strings")
        if set(ports) != set(members):
            missing = ", ".join(sorted(set(members) - set(ports))) or "none"
            unexpected = ", ".join(sorted(set(ports) - set(members))) or "none"
            raise GroupDispatchError(
                f"ports must match group members; missing={missing}; unexpected={unexpected}"
            )
        self._require_exact_mapping("device limits", device_limits, members)
        self._require_exact_mapping("identity guards", identity_guards, members)

        slots: dict[str, _DeviceSlot] = {}
        for device_id in members:
            port = ports[device_id]
            limits = device_limits[device_id]
            identity_guard = identity_guards[device_id]
            if not isinstance(limits, DeviceActuationLimits):
                raise GroupDispatchError(
                    f"trusted limits for {device_id!r} must be DeviceActuationLimits"
                )
            if not callable(identity_guard):
                raise GroupDispatchError(f"identity guard for {device_id!r} must be callable")
            try:
                port_device_id = port.device_id
            except Exception as error:
                raise GroupDispatchError("a port did not expose its logical device id") from error
            if port_device_id != device_id:
                raise GroupDispatchError(
                    f"port key {device_id!r} does not match its logical device id"
                )
            if not callable(getattr(port, "write_target", None)):
                raise GroupDispatchError(f"port {device_id!r} does not provide write_target")
            try:
                identity_is_current = identity_guard()
            except Exception as error:
                raise GroupDispatchError(
                    f"identity guard for {device_id!r} failed during binding"
                ) from error
            if identity_is_current is not True:
                raise GroupDispatchError(f"identity binding for {device_id!r} is not current")
            slots[device_id] = _DeviceSlot(
                port=port,
                trusted_limits=limits,
                identity_guard=identity_guard,
            )

        self._group_id = group_id
        self._member_ids = members
        self._slots = slots
        self._latest_generation = 0
        self._latest_plan: GroupTickPlan | None = None
        self._closed = False
        self._changed = asyncio.Event()
        self._close_waiter: asyncio.Task[None] | None = None

    @property
    def group_id(self) -> str:
        return self._group_id

    @property
    def closed(self) -> bool:
        return self._closed

    def submit(
        self,
        plan: GroupTickPlan,
        *,
        is_current: GenerationPredicate,
    ) -> GroupDispatchSnapshot:
        """Validate one complete plan and make all member decisions visible atomically.

        Calls are expected to come from a tick-driven planner.  A device decision received while a
        mandatory OFF owns that slot is intentionally deferred rather than retained; a later tick
        must resubmit current intent after the OFF has a confirmed outcome.
        """

        loop = self._validate_submission(plan, is_current)
        decisions = {decision.device: decision for decision in plan.decisions}

        if plan.generation > self._latest_generation:
            self._latest_generation = plan.generation
            self._latest_plan = plan

        self._ensure_workers(loop)
        for device_id in self._member_ids:
            decision = decisions[device_id]
            slot = self._slots[device_id]
            slot.submitted_count += 1

            # A non-deduplicated canonical OFF owns this slot until its outcome is known.  Later
            # ordinary plans must not invalidate its cache revision or generation guard while it
            # is pending/in flight.
            if slot.mandatory_off_token is not None:
                slot.last_control_epoch = decision.control_epoch
                slot.safety_off_deferred_count += 1
                continue

            if (
                decision.action is MemberAction.DISPATCH
                and decision.control_epoch > slot.last_control_epoch
            ):
                self._invalidate_slot(slot)
            slot.last_control_epoch = decision.control_epoch

            if decision.action is not MemberAction.DISPATCH:
                self._consume_skip(slot, decision)
                continue

            assert decision.target is not None  # established by whole-plan validation
            safety_token = object()
            job = _Dispatch(
                generation=plan.generation,
                control_epoch=decision.control_epoch,
                target=decision.target,
                guard=self._dispatch_guard(
                    plan.generation,
                    is_current,
                    device_id=device_id,
                    slot=slot,
                    safety_token=safety_token,
                ),
                safety_token=safety_token,
                cache_revision=slot.cache_revision,
            )
            self._consume_dispatch(slot, job)

        self._changed.set()
        return self.snapshot()

    def retry_latest(self, device_id: str) -> GroupDispatchSnapshot:
        """Explicitly retry one target proven not sent; uncertain writes are never retried."""

        self._require_open()
        slot = self._slot(device_id)
        if slot.failure is None:
            raise GroupDispatchError(f"device {device_id!r} has no latched failure")
        if not slot.failure.retry_safe:
            raise UncertainDispatchError(
                f"device {device_id!r} delivery is uncertain; retry would violate single-write"
            )
        mandatory_off = slot.mandatory_off_token is not None
        job = slot.failed_job if mandatory_off else slot.latest
        if job is None:
            raise GroupDispatchError(f"device {device_id!r} has no dispatch target to retry")
        if mandatory_off and (
            job.safety_token is not slot.mandatory_off_token
            or not self._is_canonical_off(job.target)
        ):
            raise GroupDispatchError(
                f"device {device_id!r} requires reconciliation before a non-OFF retry"
            )
        if (
            not mandatory_off and job.generation != self._latest_generation
        ) or job.guard() is not True:
            raise StaleDispatchError(f"device {device_id!r} retry target is stale")
        if slot.in_flight is not None or slot.pending is not None:
            raise GroupDispatchError(f"device {device_id!r} already has dispatch work")

        retried = _Dispatch(
            generation=job.generation,
            control_epoch=job.control_epoch,
            target=job.target,
            guard=job.guard,
            safety_token=job.safety_token,
            cache_revision=slot.cache_revision,
        )
        if not mandatory_off:
            slot.failure = None
            slot.failed_job = None
        slot.latest = retried
        slot.pending = retried
        slot.wake.set()
        self._changed.set()
        return self.snapshot()

    def invalidate_applied(self, device_id: str) -> GroupDispatchSnapshot:
        """Forget confirmed state after reconnect or externally observed drift.

        This never queues a new write.  An already in-flight call may finish; that overlap is
        latched as uncertain and blocks further dispatch until a future reconciliation layer can
        prove the physical state.  This also applies to an in-flight mandatory OFF: a concurrent
        reconnect/drift invalidation takes precedence over its late success claim.
        """

        self._require_open()
        slot = self._slot(device_id)
        if slot.pending is not None and not self._is_mandatory_off(slot, slot.pending):
            self._record_superseded(slot, slot.pending.generation)
            slot.pending = None
        if slot.in_flight is not None:
            slot.invalidated_in_flight = slot.in_flight
        self._invalidate_slot(slot)
        slot.wake.set()
        self._changed.set()
        return self.snapshot()

    def snapshot(self) -> GroupDispatchSnapshot:
        return GroupDispatchSnapshot(
            group_id=self._group_id,
            closed=self._closed,
            latest_generation=self._latest_generation,
            devices=tuple(self._snapshot_slot(device_id) for device_id in self._member_ids),
        )

    async def wait_idle(self) -> GroupDispatchSnapshot:
        """Wait until no device has a queued or in-flight dispatch."""

        while not self._is_idle():
            self._changed.clear()
            if self._is_idle():
                break
            await self._changed.wait()
        return self.snapshot()

    async def aclose(self) -> None:
        """Stop accepting work, drop pending slots, and await in-flight writes.

        No OFF target is synthesized and no worker is cancelled.  Ordinary pending work is dropped,
        while an already accepted mandatory OFF is drained before the worker exits.  A mandatory OFF
        proven not sent gets one bounded shutdown retry; any remaining barrier raises
        :class:`UnresolvedSafetyStopError` instead of reporting a clean close.  Therefore an
        injected port must own a finite transport timeout.  If the caller cancels this method,
        cleanup still waits for every already in-flight port call before propagating cancellation.
        """

        if not self._closed:
            self._closed = True
            for slot in self._slots.values():
                if slot.in_flight is not None and self._is_canonical_off(slot.in_flight.target):
                    self._arm_mandatory_off(slot, slot.in_flight)
                    if slot.pending is not None:
                        slot.shutdown_dropped_count += 1
                        slot.pending = None
                elif slot.pending is not None and self._is_canonical_off(slot.pending.target):
                    self._arm_mandatory_off(slot, slot.pending)
                elif (
                    slot.mandatory_off_token is not None
                    and slot.failure is not None
                    and slot.failure.retry_safe
                    and slot.failed_job is not None
                    and self._is_mandatory_off(slot, slot.failed_job)
                    and slot.shutdown_safety_retry_count < 1
                ):
                    failed_job = slot.failed_job
                    slot.shutdown_safety_retry_count += 1
                    slot.pending = _Dispatch(
                        generation=failed_job.generation,
                        control_epoch=failed_job.control_epoch,
                        target=failed_job.target,
                        guard=failed_job.guard,
                        safety_token=failed_job.safety_token,
                        cache_revision=slot.cache_revision,
                    )
                if slot.pending is not None and not self._is_mandatory_off(slot, slot.pending):
                    slot.shutdown_dropped_count += 1
                    slot.pending = None
                slot.wake.set()
            self._changed.set()

        tasks = tuple(slot.task for slot in self._slots.values() if slot.task is not None)
        if not tasks:
            self._raise_unresolved_safety_stop()
            return
        if self._close_waiter is None:
            self._close_waiter = asyncio.create_task(
                self._await_workers(tasks), name=f"group-dispatch-close:{self._group_id}"
            )
        cancellation_requested = False
        while not self._close_waiter.done():
            try:
                await asyncio.shield(self._close_waiter)
            except asyncio.CancelledError:
                cancellation_requested = True
        self._close_waiter.result()
        self._raise_unresolved_safety_stop()
        if cancellation_requested:
            raise asyncio.CancelledError

    def _validate_submission(
        self,
        plan: GroupTickPlan,
        is_current: GenerationPredicate,
    ) -> asyncio.AbstractEventLoop:
        self._require_open()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as error:
            raise GroupDispatchError("submit requires a running event loop") from error
        if not isinstance(plan, GroupTickPlan):
            raise GroupDispatchError("plan must be a GroupTickPlan")
        if plan.group_id != self._group_id:
            raise GroupDispatchError("plan group id does not match this runtime")
        if type(plan.generation) is not int or plan.generation < 1:
            raise GroupDispatchError("plan generation must be a positive integer")
        if not callable(is_current):
            raise GroupDispatchError("is_current must be callable")
        try:
            current = is_current(plan.generation)
        except Exception as error:
            raise GroupDispatchError("is_current failed during plan validation") from error
        if current is not True:
            raise StaleDispatchError("plan generation is not current")
        if plan.generation < self._latest_generation:
            raise StaleDispatchError("plan generation precedes the latest accepted plan")
        if plan.generation == self._latest_generation:
            if self._latest_plan is None or plan != self._latest_plan:
                raise GroupDispatchError("one generation cannot describe two different plans")
        if not isinstance(plan.derived_status, GroupState):
            raise GroupDispatchError("plan derived status must be a GroupState")
        if type(plan.failure_policy_stopped) is not bool:
            raise GroupDispatchError("plan failure-policy stop marker must be a boolean")

        decision_ids = [decision.device for decision in plan.decisions]
        if len(decision_ids) != len(set(decision_ids)):
            raise GroupDispatchError("plan contains duplicate member decisions")
        if set(decision_ids) != set(self._member_ids):
            missing = ", ".join(sorted(set(self._member_ids) - set(decision_ids))) or "none"
            unexpected = ", ".join(sorted(set(decision_ids) - set(self._member_ids))) or "none"
            raise GroupDispatchError(
                "plan decisions must match runtime members; "
                f"missing={missing}; unexpected={unexpected}"
            )
        for decision in plan.decisions:
            self._validate_decision(decision, self._slots[decision.device])
        dispatches = tuple(
            decision for decision in plan.decisions if decision.action is MemberAction.DISPATCH
        )
        if plan.derived_status in {GroupState.STOPPED, GroupState.EMERGENCY_STOP}:
            if len(dispatches) != len(plan.decisions) or any(
                decision.target is None or not self._is_canonical_off(decision.target)
                for decision in dispatches
            ):
                raise GroupDispatchError(
                    "stopped and emergency-stopped plans require canonical OFF for every member"
                )
        if plan.derived_status in {GroupState.STARTING, GroupState.MAINTENANCE} and any(
            decision.target is None or not self._is_canonical_off(decision.target)
            for decision in dispatches
        ):
            raise GroupDispatchError("held group states may only dispatch canonical OFF targets")
        if plan.failure_policy_stopped:
            allowed_unavailable_skips = {
                MemberAction.SKIP_OFFLINE,
                MemberAction.SKIP_UNKNOWN,
            }
            if any(
                (
                    decision.action is MemberAction.DISPATCH
                    and (decision.target is None or not self._is_canonical_off(decision.target))
                )
                or (
                    decision.action is not MemberAction.DISPATCH
                    and decision.action not in allowed_unavailable_skips
                )
                for decision in plan.decisions
            ):
                raise GroupDispatchError(
                    "failure-policy stopped plans require canonical OFF or unavailable skips"
                )
        return loop

    @staticmethod
    def _validate_decision(decision: MemberDecision, slot: _DeviceSlot) -> None:
        if not isinstance(decision.action, MemberAction):
            raise GroupDispatchError("decision action must be a MemberAction")
        if type(decision.control_epoch) is not int or decision.control_epoch < 0:
            raise GroupDispatchError("decision control epoch must be a non-negative integer")
        if decision.control_epoch < slot.last_control_epoch:
            raise StaleDispatchError(f"device {decision.device!r} control epoch moved backwards")
        requested = decision.requested_target
        if not isinstance(requested, DeviceTarget):
            raise GroupDispatchError("decision requested target must be a DeviceTarget")
        if decision.requested_power != requested.power:
            raise GroupDispatchError("decision requested power does not match its requested target")
        if not isinstance(decision.limits_used, DeviceActuationLimits):
            raise GroupDispatchError("decision limits must be validated device limits")
        if decision.limits_used != slot.trusted_limits:
            raise GroupDispatchError("decision limits do not match runtime-bound device limits")
        if decision.action is MemberAction.DISPATCH:
            target = decision.target
            if not isinstance(target, DeviceTarget):
                raise GroupDispatchError("dispatch decisions require a DeviceTarget")
            if decision.normalized_power != target.power:
                raise GroupDispatchError("dispatch target does not match normalized power")
            if (
                type(decision.effective_max_power) is not int
                or decision.effective_max_power < 0
                or decision.effective_max_power > decision.limits_used.max_power
            ):
                raise GroupDispatchError("effective maximum exceeds the validated device limits")
            if target.enabled:
                if not requested.enabled:
                    raise GroupDispatchError("normalization cannot turn a requested OFF into ON")
                if any(
                    getattr(target, field_name) != getattr(requested, field_name)
                    for field_name in ("mode", "frequency", "linkage", "timer_enabled")
                ):
                    raise GroupDispatchError(
                        "normalization may not change non-power target attributes"
                    )
                if target.linkage is not None or target.timer_enabled is not None:
                    raise GroupDispatchError(
                        "software-independent targets cannot change linkage or timer ownership"
                    )
                if not (
                    decision.limits_used.min_power <= target.power <= decision.effective_max_power
                ):
                    raise GroupDispatchError("dispatch target is outside effective power limits")
                if target.power % decision.limits_used.power_step != 0:
                    raise GroupDispatchError(
                        "dispatch target does not match the absolute power step"
                    )
            elif not GroupDispatchRuntime._is_canonical_off(target):
                raise GroupDispatchError(
                    "disabled dispatch targets must use the canonical OFF target"
                )
            return
        if decision.target is not None:
            raise GroupDispatchError("skip decisions must not contain a dispatch target")

    def _ensure_workers(self, loop: asyncio.AbstractEventLoop) -> None:
        for device_id, slot in self._slots.items():
            if slot.task is None:
                slot.task = loop.create_task(
                    self._run_device(device_id, slot),
                    name=f"group-dispatch:{self._group_id}:{device_id}",
                )

    def _dispatch_guard(
        self,
        generation: int,
        is_current: GenerationPredicate,
        *,
        device_id: str,
        slot: _DeviceSlot,
        safety_token: object,
    ) -> WriteGuard:
        def guard() -> bool:
            try:
                identity_is_current = (
                    slot.port.device_id == device_id and slot.identity_guard() is True
                )
                if not identity_is_current:
                    return False
                if slot.mandatory_off_token is safety_token:
                    return True
                return generation == self._latest_generation and is_current(generation) is True
            except Exception:
                return False

        return guard

    def _consume_skip(self, slot: _DeviceSlot, decision: MemberDecision) -> None:
        slot.skip_count += 1
        slot.latest = None
        self._invalidate_slot(slot)
        if slot.pending is not None:
            self._record_superseded(slot, slot.pending.generation)
            slot.pending = None
        slot.wake.set()

    def _consume_dispatch(self, slot: _DeviceSlot, job: _Dispatch) -> None:
        if self._is_canonical_off(job.target):
            if slot.pending is not None:
                self._record_superseded(slot, slot.pending.generation)
                slot.pending = None
            if slot.failure is None and slot.in_flight is None and slot.applied_key == job.key:
                slot.latest = job
                slot.deduplicated_count += 1
                return
            if (
                slot.failure is None
                and slot.in_flight is not None
                and self._is_canonical_off(slot.in_flight.target)
            ):
                self._arm_mandatory_off(slot, slot.in_flight)
                slot.safety_off_deferred_count += 1
                return
            if slot.failure is not None and not slot.failure.target.enabled:
                slot.latest = job
                slot.failure_suppressed_count += 1
                return
            self._arm_mandatory_off(slot, job)
            slot.pending = job
            slot.wake.set()
            return
        if slot.failure is not None:
            slot.latest = job
            slot.failure_suppressed_count += 1
            return
        slot.latest = job
        if slot.applied_key == job.key:
            slot.deduplicated_count += 1
            return
        if (
            slot.in_flight is not None
            and slot.in_flight.generation == job.generation
            and slot.in_flight.key == job.key
        ):
            slot.deduplicated_count += 1
            return
        if (
            slot.pending is not None
            and slot.pending.generation == job.generation
            and slot.pending.key == job.key
        ):
            slot.deduplicated_count += 1
            return
        if slot.pending is not None:
            self._record_superseded(slot, slot.pending.generation)
        slot.pending = job
        slot.wake.set()

    async def _run_device(self, device_id: str, slot: _DeviceSlot) -> None:
        while True:
            await slot.wake.wait()
            slot.wake.clear()
            while True:
                if self._closed and slot.pending is None and slot.in_flight is None:
                    self._changed.set()
                    return
                job = slot.pending
                slot.pending = None
                if job is None:
                    break
                mandatory_off = self._is_mandatory_off(slot, job)
                if slot.failure is not None and not mandatory_off:
                    slot.failure_suppressed_count += 1
                    continue
                if slot.applied_key == job.key and not mandatory_off:
                    slot.deduplicated_count += 1
                    continue

                slot.in_flight = job
                slot.write_started_count += 1
                self._changed.set()
                try:
                    if slot.port.device_id != device_id:
                        raise PreWriteDispatchError("port logical identity changed before dispatch")
                    if slot.identity_guard() is not True:
                        raise PreWriteDispatchError(
                            "physical identity binding changed before dispatch"
                        )
                    if job.guard() is not True:
                        raise StaleDispatchError("generation lost ownership before dispatch")
                    await slot.port.write_target(job.target, guard=job.guard)
                except StaleDispatchError as error:
                    if mandatory_off:
                        self._latch_failure(
                            device_id,
                            slot,
                            job,
                            error,
                            delivery_certainty=DeliveryCertainty.NOT_SENT,
                        )
                    else:
                        slot.stale_dropped_count += 1
                except asyncio.CancelledError as error:
                    task = asyncio.current_task()
                    if task is not None and task.cancelling():
                        raise
                    self._latch_failure(
                        device_id,
                        slot,
                        job,
                        error,
                        delivery_certainty=DeliveryCertainty.UNCERTAIN,
                    )
                except Exception as error:
                    certainty = (
                        DeliveryCertainty.NOT_SENT
                        if isinstance(error, PreWriteDispatchError)
                        else DeliveryCertainty.UNCERTAIN
                    )
                    self._latch_failure(
                        device_id,
                        slot,
                        job,
                        error,
                        delivery_certainty=certainty,
                    )
                else:
                    if slot.invalidated_in_flight is job:
                        self._latch_failure(
                            device_id,
                            slot,
                            job,
                            _AppliedStateInvalidatedDuringWrite(),
                            delivery_certainty=DeliveryCertainty.UNCERTAIN,
                        )
                    elif mandatory_off:
                        slot.last_safety_recovered_failure = slot.safety_off_origin_failure
                        slot.failure = None
                        slot.failed_job = None
                        slot.mandatory_off_token = None
                        slot.safety_off_origin_failure = None
                        slot.safety_off_succeeded_count += 1
                        slot.applied_key = job.key
                    elif job.cache_revision == slot.cache_revision:
                        slot.applied_key = job.key
                    slot.write_succeeded_count += 1
                finally:
                    if slot.invalidated_in_flight is job:
                        slot.invalidated_in_flight = None
                    slot.in_flight = None
                    self._changed.set()

                if self._closed:
                    if slot.pending is not None and self._is_mandatory_off(slot, slot.pending):
                        continue
                    if (
                        mandatory_off
                        and slot.failure is not None
                        and slot.failure.retry_safe
                        and slot.shutdown_safety_retry_count < 1
                    ):
                        slot.shutdown_safety_retry_count += 1
                        slot.pending = _Dispatch(
                            generation=job.generation,
                            control_epoch=job.control_epoch,
                            target=job.target,
                            guard=job.guard,
                            safety_token=job.safety_token,
                            cache_revision=slot.cache_revision,
                        )
                        continue
                    if slot.pending is not None:
                        slot.shutdown_dropped_count += 1
                        slot.pending = None
                    return

    @staticmethod
    async def _await_workers(tasks: tuple[asyncio.Task[None], ...]) -> None:
        await asyncio.gather(*tasks)

    @staticmethod
    def _latch_failure(
        device_id: str,
        slot: _DeviceSlot,
        job: _Dispatch,
        error: BaseException,
        *,
        delivery_certainty: DeliveryCertainty,
    ) -> None:
        slot.failure_count += 1
        slot.failed_job = job
        failure = DispatchFailure(
            device_id=device_id,
            generation=job.generation,
            control_epoch=job.control_epoch,
            target=job.target,
            exception_type=type(error).__name__,
            delivery_certainty=delivery_certainty,
        )
        slot.failure = failure
        if (
            slot.safety_off_origin_failure is None
            and slot.mandatory_off_token is not None
            and job.target.enabled
        ):
            slot.safety_off_origin_failure = failure
        GroupDispatchRuntime._invalidate_slot(slot)
        if slot.pending is not None and not GroupDispatchRuntime._is_mandatory_off(
            slot, slot.pending
        ):
            slot.failure_suppressed_count += 1
            slot.pending = None

    def _snapshot_slot(self, device_id: str) -> DeviceDispatchSnapshot:
        slot = self._slots[device_id]
        applied_epoch, applied_target = self._split_key(slot.applied_key)
        pending = slot.pending
        in_flight = slot.in_flight
        return DeviceDispatchSnapshot(
            device_id=device_id,
            last_control_epoch=slot.last_control_epoch,
            applied_control_epoch=applied_epoch,
            applied_target=applied_target,
            pending_generation=None if pending is None else pending.generation,
            pending_control_epoch=None if pending is None else pending.control_epoch,
            pending_target=None if pending is None else pending.target,
            in_flight_generation=None if in_flight is None else in_flight.generation,
            in_flight_control_epoch=None if in_flight is None else in_flight.control_epoch,
            in_flight_target=None if in_flight is None else in_flight.target,
            failure=slot.failure,
            submitted_count=slot.submitted_count,
            write_started_count=slot.write_started_count,
            write_succeeded_count=slot.write_succeeded_count,
            deduplicated_count=slot.deduplicated_count,
            superseded_count=slot.superseded_count,
            last_superseded_generation=slot.last_superseded_generation,
            stale_dropped_count=slot.stale_dropped_count,
            failure_count=slot.failure_count,
            failure_suppressed_count=slot.failure_suppressed_count,
            safety_off_deferred_count=slot.safety_off_deferred_count,
            safety_off_bypass_count=slot.safety_off_bypass_count,
            safety_off_succeeded_count=slot.safety_off_succeeded_count,
            safety_off_required=slot.mandatory_off_token is not None,
            safety_off_origin_failure=slot.safety_off_origin_failure,
            last_safety_recovered_failure=slot.last_safety_recovered_failure,
            skip_count=slot.skip_count,
            invalidated_count=slot.invalidated_count,
            shutdown_dropped_count=slot.shutdown_dropped_count,
            shutdown_safety_retry_count=slot.shutdown_safety_retry_count,
            worker_running=slot.task is not None and not slot.task.done(),
        )

    @staticmethod
    def _split_key(key: _DedupeKey | None) -> tuple[int | None, DeviceTarget | None]:
        if key is None:
            return None, None
        return key

    @staticmethod
    def _record_superseded(slot: _DeviceSlot, generation: int) -> None:
        slot.superseded_count += 1
        slot.last_superseded_generation = generation

    @staticmethod
    def _is_canonical_off(target: DeviceTarget) -> bool:
        return target == DeviceTarget(enabled=False, power=0)

    @staticmethod
    def _is_mandatory_off(slot: _DeviceSlot, job: _Dispatch) -> bool:
        return slot.mandatory_off_token is job.safety_token

    @staticmethod
    def _arm_mandatory_off(slot: _DeviceSlot, job: _Dispatch) -> None:
        if slot.mandatory_off_token is job.safety_token:
            return
        # A previously confirmed target is no longer a usable dedupe fact once this OFF is promoted:
        # either an enabled write overlapped it or shutdown has committed to draining the stop.
        GroupDispatchRuntime._invalidate_slot(slot)
        slot.mandatory_off_token = job.safety_token
        slot.safety_off_origin_failure = slot.failure
        slot.safety_off_bypass_count += 1
        slot.latest = job

    @staticmethod
    def _invalidate_slot(slot: _DeviceSlot) -> None:
        if slot.applied_key is not None:
            slot.applied_key = None
        slot.cache_revision += 1
        slot.invalidated_count += 1

    def _slot(self, device_id: str) -> _DeviceSlot:
        try:
            return self._slots[device_id]
        except KeyError as error:
            raise GroupDispatchError(f"unknown group member {device_id!r}") from error

    @staticmethod
    def _require_exact_mapping(
        label: str,
        values: Mapping[str, object],
        members: tuple[str, ...],
    ) -> None:
        if any(not isinstance(device_id, str) or not device_id for device_id in values):
            raise GroupDispatchError(f"{label} keys must be non-empty strings")
        if set(values) == set(members):
            return
        missing = ", ".join(sorted(set(members) - set(values))) or "none"
        unexpected = ", ".join(sorted(set(values) - set(members))) or "none"
        raise GroupDispatchError(
            f"{label} must match group members; missing={missing}; unexpected={unexpected}"
        )

    def _require_open(self) -> None:
        if self._closed:
            raise RuntimeClosedError("group dispatch runtime is closed")

    def _raise_unresolved_safety_stop(self) -> None:
        unresolved = tuple(
            device_id
            for device_id in self._member_ids
            if self._slots[device_id].mandatory_off_token is not None
        )
        if unresolved:
            raise UnresolvedSafetyStopError(unresolved)

    def _is_idle(self) -> bool:
        return all(slot.pending is None and slot.in_flight is None for slot in self._slots.values())


__all__ = [
    "DeliveryCertainty",
    "DeviceDispatchSnapshot",
    "DispatchFailure",
    "GroupDevicePort",
    "GroupDispatchError",
    "GroupDispatchRuntime",
    "GroupDispatchSnapshot",
    "PreWriteDispatchError",
    "RuntimeClosedError",
    "StaleDispatchError",
    "UncertainDispatchError",
    "UnresolvedSafetyStopError",
    "WriteGuard",
]
