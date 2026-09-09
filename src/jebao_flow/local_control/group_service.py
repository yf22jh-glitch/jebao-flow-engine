"""Isolated production lifecycle construction for software-independent groups.

The service builds each group independently, feeds only complete fresh-state cycles into a
write-disarmed supervisor, and keeps construction or planning failures out of the existing device
state publisher. It exposes one qualified ON/OFF service boundary without importing MQTT or Home
Assistant transport code.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from jebao_flow.config import AppConfig, DeviceConfig
from jebao_flow.groups.calculator import PatternCalculator
from jebao_flow.groups.manager import GroupManager
from jebao_flow.groups.models import (
    GroupConfig,
    GroupExecutionStrategy,
    GroupState,
    OfflinePolicy,
)
from jebao_flow.groups.plan import DeviceActuationLimits, GroupTickPlan
from jebao_flow.groups.runtime import (
    DeliveryCertainty,
    GroupPlanDispatchSnapshot,
    PlanAtomicGroupDispatchRuntime,
    UnresolvedGroupSafetyStopError,
    WriteGuard,
    group_plan_identity_sha256,
)
from jebao_flow.local_control.catalog import LocalControlCatalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.group import SoftwareIndependentGroupActuator
from jebao_flow.local_control.group_command import (
    GroupControlQualification,
    group_control_qualification,
)
from jebao_flow.local_control.group_supervisor import (
    GROUP_EMERGENCY_STOP_BLOCKS_DIRECT_CONTROL,
    GROUP_FORCED_OFF_WILL_REVERT,
    GroupSupervisorCycle,
    GroupSupervisorDisposition,
    LocalControlGroupSupervisor,
)
from jebao_flow.local_control.models import FreshInstallManifest, LocalDeviceBinding
from jebao_flow.local_control.state import FreshDeviceState

_GROUP_FLOW_MINIMUM = 30
_GROUP_FLOW_MAXIMUM = 75
GROUP_COMMAND_COMPLETION_TIMEOUT_SECONDS = 2.0


class LocalControlGroupServiceError(RuntimeError):
    """The isolated group lifecycle could not complete a requested local operation."""


class LocalControlGroupCommandRejected(LocalControlGroupServiceError):
    """A stable pre-write diagnostic rejected one group command."""

    def __init__(self, diagnostic_reason: str) -> None:
        if not diagnostic_reason or not diagnostic_reason.isascii():
            raise ValueError("group command rejection reason must be non-empty ASCII")
        self.diagnostic_reason = diagnostic_reason
        super().__init__(diagnostic_reason)


class GroupLifecycleState(StrEnum):
    """Bounded public lifecycle state for one logical group."""

    SUPERVISED = "supervised"
    UNCONSTRUCTED = "unconstructed"
    CYCLE_FAILED = "cycle_failed"


class GroupActualOutput(StrEnum):
    """Fresh config-relative member power aggregation, never desired state."""

    OFF = "off"
    ON = "on"
    MIXED = "mixed"
    UNKNOWN = "unknown"


class GroupCommandHeadline(StrEnum):
    """Bounded action headline derived from immediate submission and final identity facts."""

    DEFERRED_SAFETY_OFF = "deferred_safety_off"
    SUPPRESSED_AFTER_FAILURE = "suppressed_after_failure"
    NOT_SENT = "not_sent"
    UNCERTAIN = "uncertain"
    AWAITING_FRESH_STATE = "awaiting_fresh_state"
    DEDUPLICATED = "deduplicated"
    VERIFIED = "verified"
    SUPERSEDED = "superseded"
    PENDING = "pending"


@dataclass(frozen=True, slots=True)
class GroupCommandResult:
    """Identity-bound group-command facts without guessed physical authorship."""

    group_id: str
    requested_on: bool
    headline: GroupCommandHeadline
    ownership_accepted: bool
    submitted_plan_identity_sha256: str
    runtime_submit_called: bool
    deduplicated: bool
    dispatch_decision_count: int
    applied_identity_matches: bool
    latest_identity_matches: bool
    delivery_certainty: DeliveryCertainty | None
    completion_pending: bool

    def __post_init__(self) -> None:
        if not self.group_id or not self.group_id.isascii():
            raise ValueError("group command result id must be non-empty ASCII")
        if type(self.requested_on) is not bool or type(self.ownership_accepted) is not bool:
            raise TypeError("group command acceptance flags must be boolean")
        if type(self.runtime_submit_called) is not bool or type(self.deduplicated) is not bool:
            raise TypeError("group command submission flags must be boolean")
        if (
            type(self.applied_identity_matches) is not bool
            or type(self.latest_identity_matches) is not bool
            or type(self.completion_pending) is not bool
        ):
            raise TypeError("group command completion flags must be boolean")
        if self.dispatch_decision_count < 0:
            raise ValueError("group command dispatch count cannot be negative")
        if len(self.submitted_plan_identity_sha256) != 64 or any(
            character not in "0123456789abcdef"
            for character in self.submitted_plan_identity_sha256
        ):
            raise ValueError("group command plan identity must be lowercase SHA-256")

    def public_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "requested_on": self.requested_on,
            "headline": self.headline.value,
            "ownership_accepted": self.ownership_accepted,
            "submitted_plan_identity_sha256": self.submitted_plan_identity_sha256,
            "runtime_submit_called": self.runtime_submit_called,
            "deduplicated": self.deduplicated,
            "dispatch_decision_count": self.dispatch_decision_count,
            "applied_identity_matches": self.applied_identity_matches,
            "latest_identity_matches": self.latest_identity_matches,
            "delivery_certainty": (
                None if self.delivery_certainty is None else self.delivery_certainty.value
            ),
            "completion_pending": self.completion_pending,
        }


@dataclass(frozen=True, slots=True)
class DirectControlOwnershipDecision:
    """Bounded pre-journal ownership result for one existing direct-device command."""

    logical_member: str
    group_id: str | None
    allowed: bool
    manual_override_recorded: bool
    diagnostic_reason: str | None = None

    def __post_init__(self) -> None:
        if not self.logical_member.isascii() or (
            self.group_id is not None and not self.group_id.isascii()
        ):
            raise ValueError("direct-control ownership identifiers must use ASCII")
        if type(self.allowed) is not bool or type(self.manual_override_recorded) is not bool:
            raise TypeError("direct-control ownership flags must be boolean")
        if not self.allowed and self.manual_override_recorded:
            raise ValueError("rejected direct control cannot record manual ownership")
        if self.diagnostic_reason is not None and not self.diagnostic_reason.isascii():
            raise ValueError("direct-control ownership diagnostics must use ASCII")

    def public_dict(self) -> dict[str, object]:
        return {
            "logical_member": self.logical_member,
            "group_id": self.group_id,
            "allowed": self.allowed,
            "manual_override_recorded": self.manual_override_recorded,
            "diagnostic_reason": self.diagnostic_reason,
        }


@dataclass(frozen=True, slots=True)
class GroupOutputState:
    """One logical group's fresh power truth and separately reported ownership state."""

    group_id: str
    actual_output: GroupActualOutput
    install_generation: str
    manual_override_members: tuple[str, ...]
    ownership_diagnostic_reason: str | None
    command_qualified: bool = False
    command_diagnostic_reason: str | None = None
    missing_control_qualifications: tuple[tuple[str, str], ...] = ()
    supervisor_constructed: bool = False

    @property
    def available(self) -> bool:
        return self.actual_output is not GroupActualOutput.UNKNOWN

    @property
    def command_available(self) -> bool:
        return (
            self.command_qualified
            and self.supervisor_constructed
            and self.actual_output in {GroupActualOutput.ON, GroupActualOutput.OFF}
        )

    def attributes_dict(self) -> dict[str, object]:
        return {
            "actual_output": self.actual_output.value,
            "config_relative": True,
            "install_generation": self.install_generation,
            "manual_override_members": list(self.manual_override_members),
            "ownership_diagnostic_reason": self.ownership_diagnostic_reason,
            "pattern_conformance": "unknown",
            "group_command": {
                "qualified": self.command_qualified,
                "available": self.command_available,
                "diagnostic_reason": self.command_diagnostic_reason,
                "missing": [
                    {
                        "logical_member": logical_member,
                        "function": function,
                    }
                    for logical_member, function in self.missing_control_qualifications
                ],
            },
        }


@dataclass(frozen=True, slots=True)
class GroupLifecycleItem:
    """Public logical-group inventory without physical identity material."""

    group_id: str
    member_count: int
    state: GroupLifecycleState
    supervisor_constructed: bool
    cycle_count: int
    cycle_failure_count: int
    last_cycle_disposition: str | None
    diagnostic_reason: str | None
    actual_output: GroupActualOutput
    actual_output_available: bool
    install_generation: str
    manual_override_members: tuple[str, ...]
    ownership_diagnostic_reason: str | None
    command_qualified: bool
    command_diagnostic_reason: str | None
    missing_control_qualifications: tuple[tuple[str, str], ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "group_id": self.group_id,
            "member_count": self.member_count,
            "state": self.state.value,
            "supervisor_constructed": self.supervisor_constructed,
            "cycle_count": self.cycle_count,
            "cycle_failure_count": self.cycle_failure_count,
            "last_cycle_disposition": self.last_cycle_disposition,
            "diagnostic_reason": self.diagnostic_reason,
            "actual_output": self.actual_output.value,
            "actual_output_available": self.actual_output_available,
            "install_generation": self.install_generation,
            "manual_override_members": list(self.manual_override_members),
            "ownership_diagnostic_reason": self.ownership_diagnostic_reason,
            "pattern_conformance": "unknown",
            "group_command": {
                "qualified": self.command_qualified,
                "diagnostic_reason": self.command_diagnostic_reason,
                "missing": [
                    {
                        "logical_member": logical_member,
                        "function": function,
                    }
                    for logical_member, function in self.missing_control_qualifications
                ],
            },
        }


@dataclass(frozen=True, slots=True)
class GroupLifecycleSnapshot:
    """One complete, bounded view of configured group lifecycle state."""

    configured_group_count: int
    supervisor_count: int
    fresh_cycle_count: int
    closed: bool
    groups: tuple[GroupLifecycleItem, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "status": "group_lifecycle",
            "configured_group_count": self.configured_group_count,
            "supervisor_count": self.supervisor_count,
            "unconstructed_group_count": sum(
                item.state is GroupLifecycleState.UNCONSTRUCTED for item in self.groups
            ),
            "cycle_failed_group_count": sum(
                item.state is GroupLifecycleState.CYCLE_FAILED for item in self.groups
            ),
            "fresh_cycle_count": self.fresh_cycle_count,
            "closed": self.closed,
            "groups": [item.public_dict() for item in self.groups],
            "physical_identity_keys_exposed": False,
            "private_paths_exposed": False,
        }


@dataclass(slots=True)
class _ManagedGroup:
    group: GroupConfig
    group_id: str
    member_ids: tuple[str, ...]
    member_enabled: tuple[tuple[str, bool], ...]
    qualification: GroupControlQualification
    supervisor: LocalControlGroupSupervisor | None
    state: GroupLifecycleState
    diagnostic_reason: str | None
    actual_output: GroupActualOutput = GroupActualOutput.UNKNOWN
    manual_override_members: tuple[str, ...] = ()
    ownership_diagnostic_reason: str | None = None
    cycle_count: int = 0
    cycle_failure_count: int = 0
    last_cycle_disposition: str | None = None


class _GroupConstructionUnavailable(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _OperationLockedGroupPlanPort:
    """Serialize the physical plan boundary with direct writes and fresh reads."""

    def __init__(
        self,
        delegate: SoftwareIndependentGroupActuator,
        operation_lock: asyncio.Lock,
    ) -> None:
        self._delegate = delegate
        self._operation_lock = operation_lock

    @property
    def group_id(self) -> str:
        return self._delegate.group_id

    @property
    def member_ids(self) -> tuple[str, ...]:
        return self._delegate.member_ids

    def validate_plan(self, plan: GroupTickPlan) -> None:
        self._delegate.validate_plan(plan)

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
        async with self._operation_lock:
            await self._delegate.write_plan(
                plan,
                request_id=request_id,
                generation=generation,
                plan_identity_sha256=plan_identity_sha256,
                guard=guard,
                safety_token=safety_token,
            )


class LocalControlGroupService:
    """Own independently isolated group supervisors for one current installation."""

    def __init__(
        self,
        *,
        config: AppConfig,
        manifest: FreshInstallManifest,
        catalog: LocalControlCatalog,
        coordinator: LocalControlCoordinator | None,
        operation_lock: asyncio.Lock,
        command_completion_timeout_seconds: float = GROUP_COMMAND_COMPLETION_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(config, AppConfig):
            raise TypeError("group lifecycle requires current AppConfig")
        if not isinstance(manifest, FreshInstallManifest):
            raise TypeError("group lifecycle requires a current install manifest")
        if not isinstance(catalog, LocalControlCatalog):
            raise TypeError("group lifecycle requires the current local-control catalog")
        if not isinstance(operation_lock, asyncio.Lock):
            raise TypeError("group lifecycle requires the shared operation lock")
        if (
            not isinstance(command_completion_timeout_seconds, int | float)
            or isinstance(command_completion_timeout_seconds, bool)
            or not math.isfinite(command_completion_timeout_seconds)
            or command_completion_timeout_seconds <= 0
        ):
            raise ValueError("group command completion timeout must be finite and positive")

        self._expected_members = tuple(device.logical_member for device in manifest.devices)
        self._install_generation = manifest.generation
        self._operation_lock = operation_lock
        self._command_completion_timeout_seconds = float(
            command_completion_timeout_seconds
        )
        self._fresh_cycle_count = 0
        self._closed = False
        devices_by_id = {device.id: device for device in config.devices}
        bindings_by_id = {device.logical_member: device for device in manifest.devices}
        self._groups = tuple(
            self._construct_group(
                group,
                qualification=group_control_qualification(group, manifest, catalog),
                devices_by_id=devices_by_id,
                bindings_by_id=bindings_by_id,
                catalog=catalog,
                coordinator=coordinator,
                operation_lock=operation_lock,
            )
            for group in config.groups
        )
        self._group_by_id: dict[str, _ManagedGroup] = {}
        self._group_by_member: dict[str, _ManagedGroup] = {}
        for item in self._groups:
            if item.group_id in self._group_by_id:
                raise ValueError("local-control group ids must be unique")
            self._group_by_id[item.group_id] = item
            for member_id in item.member_ids:
                if member_id in self._group_by_member:
                    raise ValueError("one device cannot belong to multiple local-control groups")
                self._group_by_member[member_id] = item

    @property
    def supervisor_count(self) -> int:
        return sum(item.supervisor is not None for item in self._groups)

    @property
    def operation_lock(self) -> asyncio.Lock:
        return self._operation_lock

    @property
    def configured_group_ids(self) -> tuple[str, ...]:
        return tuple(item.group_id for item in self._groups)

    @property
    def qualified_group_ids(self) -> tuple[str, ...]:
        return tuple(
            item.group_id for item in self._groups if item.qualification.qualified
        )

    def snapshot(self) -> GroupLifecycleSnapshot:
        groups = tuple(
            self._lifecycle_item(item)
            for item in self._groups
        )
        return GroupLifecycleSnapshot(
            configured_group_count=len(self._groups),
            supervisor_count=self.supervisor_count,
            fresh_cycle_count=self._fresh_cycle_count,
            closed=self._closed,
            groups=groups,
        )

    def public_dict(self) -> dict[str, object]:
        return self.snapshot().public_dict()

    def group_output_states(self) -> tuple[GroupOutputState, ...]:
        """Return every configured group's current fresh truth without physical identifiers."""

        return tuple(
            self._group_output_state(item)
            for item in self._groups
        )

    async def apply_group_switch(
        self,
        group_id: str,
        requested_on: bool,
    ) -> GroupCommandResult:
        """Apply one qualified ON/OFF command and report identity-bound completion facts."""

        if not isinstance(group_id, str) or not group_id or not group_id.isascii():
            raise LocalControlGroupCommandRejected("group_not_configured")
        if type(requested_on) is not bool:
            raise LocalControlGroupCommandRejected("group_command_payload_invalid")
        if self._closed:
            raise LocalControlGroupCommandRejected("group_lifecycle_closed")
        item = self._group_by_id.get(group_id)
        if item is None:
            raise LocalControlGroupCommandRejected("group_not_configured")
        if not item.qualification.qualified:
            reason = item.qualification.diagnostic_reason
            if reason is None:
                raise LocalControlGroupServiceError(
                    "unqualified group command requires a diagnostic reason"
                )
            raise LocalControlGroupCommandRejected(reason)
        supervisor = item.supervisor
        if supervisor is None:
            raise LocalControlGroupCommandRejected(
                item.diagnostic_reason or "group_supervisor_unavailable"
            )

        current = supervisor.runtime
        desired = current.model_copy(
            update={
                "state": GroupState.RUNNING if requested_on else GroupState.STOPPED,
                "enabled": requested_on,
            }
        )
        try:
            cycle = await supervisor.apply_runtime_command(desired)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise LocalControlGroupCommandRejected(
                "group_runtime_command_rejected"
            ) from error
        self._mark_cycle_succeeded(item, cycle)

        timed_out = False
        try:
            async with asyncio.timeout(self._command_completion_timeout_seconds):
                final_dispatch = await supervisor.wait_idle()
        except TimeoutError:
            timed_out = True
            final_dispatch = supervisor.snapshot().dispatch
        return _group_command_result(
            group_id=group_id,
            requested_on=requested_on,
            cycle=cycle,
            final_dispatch=final_dispatch,
            timed_out=timed_out,
        )

    async def claim_direct_ownership(
        self,
        logical_member: str,
        *,
        power_on_requested: bool = False,
    ) -> DirectControlOwnershipDecision:
        """Declare manual ownership without planning or submitting replacement group work."""

        if self._closed:
            raise LocalControlGroupServiceError("group lifecycle is closed")
        item = self._group_by_member.get(logical_member)
        if item is None:
            return DirectControlOwnershipDecision(
                logical_member=logical_member,
                group_id=None,
                allowed=True,
                manual_override_recorded=False,
            )
        supervisor = item.supervisor
        if supervisor is None:
            return DirectControlOwnershipDecision(
                logical_member=logical_member,
                group_id=item.group_id,
                allowed=True,
                manual_override_recorded=False,
            )
        decision = await supervisor.claim_manual_ownership(
            logical_member,
            power_on_requested=power_on_requested,
        )
        supervisor_snapshot = supervisor.snapshot()
        item.manual_override_members = supervisor_snapshot.manual_override_members
        item.ownership_diagnostic_reason = (
            supervisor_snapshot.ownership_diagnostic_reason
        )
        return DirectControlOwnershipDecision(
            logical_member=logical_member,
            group_id=item.group_id,
            allowed=decision.allowed,
            manual_override_recorded=decision.manual_override_recorded,
            diagnostic_reason=decision.diagnostic_reason,
        )

    async def note_verified_direct_power_on(self, logical_member: str) -> None:
        """Record a verified power-on watermark without submitting group work."""

        if self._closed:
            raise LocalControlGroupServiceError("group lifecycle is closed")
        item = self._group_by_member.get(logical_member)
        if item is None or item.supervisor is None:
            return
        await item.supervisor.note_verified_direct_power_on(logical_member)
        supervisor_snapshot = item.supervisor.snapshot()
        item.manual_override_members = supervisor_snapshot.manual_override_members
        item.ownership_diagnostic_reason = (
            supervisor_snapshot.ownership_diagnostic_reason
        )

    async def observe_fresh_cycle(
        self,
        states: Iterable[FreshDeviceState],
    ) -> GroupLifecycleSnapshot:
        """Feed one complete current cycle without allowing a group failure to escape."""

        self._fresh_cycle_count += 1
        copied = tuple(states)
        by_member = self._complete_fresh_cycle(copied)
        if by_member is None:
            for item in self._groups:
                item.actual_output = GroupActualOutput.UNKNOWN
            self._mark_all_constructed_failed("fresh_cycle_binding_mismatch")
            return self.snapshot()

        for item in self._groups:
            item.actual_output = _group_actual_output(item, by_member)
            supervisor = item.supervisor
            if supervisor is None:
                continue
            try:
                selected = tuple(by_member[member_id] for member_id in item.member_ids)
                cycle = await supervisor.observe_fresh_cycle(selected)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._mark_cycle_failed(item, "group_cycle_failed")
            else:
                self._mark_cycle_succeeded(item, cycle)
        return self.snapshot()

    async def aclose(self) -> None:
        """Close every supervisor and preserve a sticky unresolved mandatory-OFF error."""

        self._closed = True
        supervisors = tuple(item.supervisor for item in self._groups if item.supervisor is not None)
        if not supervisors:
            return

        close_waiter = asyncio.gather(
            *(supervisor.aclose() for supervisor in supervisors),
            return_exceptions=True,
        )
        cancellation_requested = False
        while not close_waiter.done():
            try:
                await asyncio.shield(close_waiter)
            except asyncio.CancelledError:
                cancellation_requested = True
        results = close_waiter.result()

        unresolved = next(
            (result for result in results if isinstance(result, UnresolvedGroupSafetyStopError)),
            None,
        )
        if unresolved is not None:
            raise unresolved
        ordinary = next((result for result in results if isinstance(result, Exception)), None)
        if ordinary is not None:
            raise LocalControlGroupServiceError(
                "one or more group supervisors failed to close"
            ) from ordinary
        if cancellation_requested or any(
            isinstance(result, asyncio.CancelledError) for result in results
        ):
            raise asyncio.CancelledError

    def _construct_group(
        self,
        group: GroupConfig,
        *,
        qualification: GroupControlQualification,
        devices_by_id: dict[str, DeviceConfig],
        bindings_by_id: dict[str, LocalDeviceBinding],
        catalog: LocalControlCatalog,
        coordinator: LocalControlCoordinator | None,
        operation_lock: asyncio.Lock,
    ) -> _ManagedGroup:
        member_ids = tuple(member.device for member in group.members)
        member_enabled = tuple((member.device, member.enabled) for member in group.members)
        try:
            self._require_supported_group(group)
            bindings, device_limits = self._current_group_inputs(
                member_ids,
                devices_by_id=devices_by_id,
                bindings_by_id=bindings_by_id,
                catalog=catalog,
            )
            if coordinator is None:
                raise _GroupConstructionUnavailable("write_coordinator_unavailable")
            manager = GroupManager(group, device_limits)
            actuator = SoftwareIndependentGroupActuator(
                group_id=group.id,
                member_ids=member_ids,
                bindings=bindings,
                device_limits=device_limits,
                coordinator=coordinator,
            )
            port = _OperationLockedGroupPlanPort(actuator, operation_lock)
            runtime = PlanAtomicGroupDispatchRuntime(group.id, member_ids, port)
            supervisor = LocalControlGroupSupervisor(manager=manager, runtime=runtime)
        except _GroupConstructionUnavailable as error:
            return _ManagedGroup(
                group=group,
                group_id=group.id,
                member_ids=member_ids,
                member_enabled=member_enabled,
                qualification=qualification,
                supervisor=None,
                state=GroupLifecycleState.UNCONSTRUCTED,
                diagnostic_reason=error.reason,
            )
        except Exception:
            return _ManagedGroup(
                group=group,
                group_id=group.id,
                member_ids=member_ids,
                member_enabled=member_enabled,
                qualification=qualification,
                supervisor=None,
                state=GroupLifecycleState.UNCONSTRUCTED,
                diagnostic_reason="group_construction_failed",
            )
        return _ManagedGroup(
            group=group,
            group_id=group.id,
            member_ids=member_ids,
            member_enabled=member_enabled,
            qualification=qualification,
            supervisor=supervisor,
            state=GroupLifecycleState.SUPERVISED,
            diagnostic_reason=None,
        )

    def _lifecycle_item(self, item: _ManagedGroup) -> GroupLifecycleItem:
        manual_override_members = item.manual_override_members
        ownership_diagnostic_reason = item.ownership_diagnostic_reason
        if item.supervisor is not None:
            supervisor_snapshot = item.supervisor.snapshot()
            manual_override_members = supervisor_snapshot.manual_override_members
            ownership_diagnostic_reason = (
                supervisor_snapshot.ownership_diagnostic_reason
            )
        return GroupLifecycleItem(
            group_id=item.group_id,
            member_count=len(item.member_ids),
            state=item.state,
            supervisor_constructed=item.supervisor is not None,
            cycle_count=item.cycle_count,
            cycle_failure_count=item.cycle_failure_count,
            last_cycle_disposition=item.last_cycle_disposition,
            diagnostic_reason=item.diagnostic_reason,
            actual_output=item.actual_output,
            actual_output_available=item.actual_output is not GroupActualOutput.UNKNOWN,
            install_generation=self._install_generation,
            manual_override_members=manual_override_members,
            ownership_diagnostic_reason=ownership_diagnostic_reason,
            command_qualified=item.qualification.qualified,
            command_diagnostic_reason=(
                item.qualification.diagnostic_reason
                if not item.qualification.qualified
                else (None if item.supervisor is not None else item.diagnostic_reason)
            ),
            missing_control_qualifications=item.qualification.missing,
        )

    def _group_output_state(self, item: _ManagedGroup) -> GroupOutputState:
        manual_override_members = item.manual_override_members
        ownership_diagnostic_reason = item.ownership_diagnostic_reason
        if item.supervisor is not None:
            supervisor_snapshot = item.supervisor.snapshot()
            manual_override_members = supervisor_snapshot.manual_override_members
            ownership_diagnostic_reason = (
                supervisor_snapshot.ownership_diagnostic_reason
            )
        return GroupOutputState(
            group_id=item.group_id,
            actual_output=item.actual_output,
            install_generation=self._install_generation,
            manual_override_members=manual_override_members,
            ownership_diagnostic_reason=ownership_diagnostic_reason,
            command_qualified=item.qualification.qualified,
            command_diagnostic_reason=(
                item.qualification.diagnostic_reason
                if not item.qualification.qualified
                else (None if item.supervisor is not None else item.diagnostic_reason)
            ),
            missing_control_qualifications=item.qualification.missing,
            supervisor_constructed=item.supervisor is not None,
        )

    @staticmethod
    def _require_supported_group(group: GroupConfig) -> None:
        if group.execution_strategy is not GroupExecutionStrategy.SOFTWARE_INDEPENDENT:
            raise _GroupConstructionUnavailable("native_linked_not_supported")
        if group.failure_policy.on_member_offline is OfflinePolicy.FALLBACK_CONSTANT:
            raise _GroupConstructionUnavailable("fallback_constant_not_supported")
        if group.default.pattern not in PatternCalculator.supported_patterns():
            raise _GroupConstructionUnavailable("pattern_not_supported")

    @staticmethod
    def _current_group_inputs(
        member_ids: tuple[str, ...],
        *,
        devices_by_id: dict[str, DeviceConfig],
        bindings_by_id: dict[str, LocalDeviceBinding],
        catalog: LocalControlCatalog,
    ) -> tuple[dict[str, LocalDeviceBinding], dict[str, DeviceActuationLimits]]:
        bindings: dict[str, LocalDeviceBinding] = {}
        limits: dict[str, DeviceActuationLimits] = {}
        physical_keys: list[str] = []
        for member_id in member_ids:
            configured = devices_by_id.get(member_id)
            if configured is None:
                raise _GroupConstructionUnavailable("current_config_member_missing")
            binding = bindings_by_id.get(member_id)
            if binding is None:
                raise _GroupConstructionUnavailable("current_manifest_member_missing")
            product_key = configured.product_key
            if product_key is None or product_key != binding.product_key:
                raise _GroupConstructionUnavailable("current_manifest_binding_mismatch")
            try:
                power = catalog.find(binding.product_key, "power")
                manual_flow = catalog.find(binding.product_key, "manual_flow")
            except KeyError as error:
                raise _GroupConstructionUnavailable("unsupported_product") from error
            if not power.encoding_available or not manual_flow.encoding_available:
                raise _GroupConstructionUnavailable("unsupported_product")

            configured_limits = configured.limits
            minimum = max(configured_limits.min_power, _GROUP_FLOW_MINIMUM)
            maximum = min(configured_limits.max_power, _GROUP_FLOW_MAXIMUM)
            if minimum > maximum:
                raise _GroupConstructionUnavailable("safe_flow_intersection_empty")
            bindings[member_id] = binding
            limits[member_id] = DeviceActuationLimits(
                min_power=minimum,
                max_power=maximum,
            )
            physical_keys.append(binding.physical_identity_key)
        if len(physical_keys) != len(set(physical_keys)):
            raise _GroupConstructionUnavailable("duplicate_physical_identity")
        return bindings, limits

    def _complete_fresh_cycle(
        self,
        states: tuple[FreshDeviceState, ...],
    ) -> dict[str, FreshDeviceState] | None:
        if any(not isinstance(state, FreshDeviceState) for state in states):
            return None
        by_member = {state.logical_member: state for state in states}
        if len(by_member) != len(states) or set(by_member) != set(self._expected_members):
            return None
        return by_member

    @staticmethod
    def _mark_cycle_failed(item: _ManagedGroup, reason: str) -> None:
        item.state = GroupLifecycleState.CYCLE_FAILED
        item.diagnostic_reason = reason
        item.cycle_count += 1
        item.cycle_failure_count += 1
        item.last_cycle_disposition = None

    @staticmethod
    def _mark_cycle_succeeded(
        item: _ManagedGroup,
        cycle: GroupSupervisorCycle,
    ) -> None:
        item.state = GroupLifecycleState.SUPERVISED
        item.diagnostic_reason = None
        item.cycle_count += 1
        item.last_cycle_disposition = cycle.disposition.value
        item.manual_override_members = cycle.plan.manual_override_devices

    def _mark_all_constructed_failed(self, reason: str) -> None:
        for item in self._groups:
            if item.supervisor is not None:
                self._mark_cycle_failed(item, reason)


def _group_actual_output(
    item: _ManagedGroup,
    by_member: dict[str, FreshDeviceState],
) -> GroupActualOutput:
    observed: dict[str, bool] = {}
    for member_id in item.member_ids:
        state = by_member.get(member_id)
        if state is None or not state.available or state.observed_power is None:
            return GroupActualOutput.UNKNOWN
        observed[member_id] = state.observed_power
    if all(value is False for value in observed.values()):
        return GroupActualOutput.OFF
    if all(observed[member_id] is enabled for member_id, enabled in item.member_enabled):
        return GroupActualOutput.ON
    return GroupActualOutput.MIXED


def _group_command_result(
    *,
    group_id: str,
    requested_on: bool,
    cycle: GroupSupervisorCycle,
    final_dispatch: GroupPlanDispatchSnapshot,
    timed_out: bool,
) -> GroupCommandResult:
    identity = group_plan_identity_sha256(cycle.plan)
    failure = final_dispatch.failure
    matching_failure = (
        failure
        if failure is not None and failure.plan_identity_sha256 == identity
        else None
    )
    applied_matches = final_dispatch.applied_plan_identity_sha256 == identity
    latest_matches = final_dispatch.latest_plan_identity_sha256 == identity
    verified = applied_matches and final_dispatch.failure is None

    if cycle.disposition is GroupSupervisorDisposition.DEFERRED_SAFETY_OFF:
        headline = GroupCommandHeadline.DEFERRED_SAFETY_OFF
    elif cycle.disposition is GroupSupervisorDisposition.SUPPRESSED_AFTER_FAILURE:
        headline = GroupCommandHeadline.SUPPRESSED_AFTER_FAILURE
    elif matching_failure is not None:
        headline = (
            GroupCommandHeadline.NOT_SENT
            if matching_failure.delivery_certainty is DeliveryCertainty.NOT_SENT
            else GroupCommandHeadline.UNCERTAIN
        )
    elif not cycle.plan.dispatch_decisions:
        headline = GroupCommandHeadline.AWAITING_FRESH_STATE
    elif cycle.deduplicated:
        headline = GroupCommandHeadline.DEDUPLICATED
    elif verified:
        headline = GroupCommandHeadline.VERIFIED
    elif not latest_matches:
        headline = GroupCommandHeadline.SUPERSEDED
    elif timed_out:
        headline = GroupCommandHeadline.PENDING
    else:
        headline = GroupCommandHeadline.PENDING

    completion_pending = headline is GroupCommandHeadline.PENDING
    return GroupCommandResult(
        group_id=group_id,
        requested_on=requested_on,
        headline=headline,
        ownership_accepted=True,
        submitted_plan_identity_sha256=identity,
        runtime_submit_called=cycle.runtime_submit_called,
        deduplicated=cycle.deduplicated,
        dispatch_decision_count=len(cycle.plan.dispatch_decisions),
        applied_identity_matches=applied_matches,
        latest_identity_matches=latest_matches,
        delivery_certainty=(
            None if matching_failure is None else matching_failure.delivery_certainty
        ),
        completion_pending=completion_pending,
    )


__all__ = [
    "DirectControlOwnershipDecision",
    "GROUP_EMERGENCY_STOP_BLOCKS_DIRECT_CONTROL",
    "GROUP_FORCED_OFF_WILL_REVERT",
    "GROUP_COMMAND_COMPLETION_TIMEOUT_SECONDS",
    "GroupActualOutput",
    "GroupCommandHeadline",
    "GroupCommandResult",
    "GroupLifecycleItem",
    "GroupLifecycleSnapshot",
    "GroupLifecycleState",
    "GroupOutputState",
    "LocalControlGroupService",
    "LocalControlGroupCommandRejected",
    "LocalControlGroupServiceError",
]
