"""Isolated production lifecycle construction for software-independent groups.

The service builds each group independently, feeds only complete fresh-state cycles into a
write-disarmed supervisor, and keeps construction or planning failures out of the existing device
state publisher.  It deliberately exposes no command transport or Home Assistant entity.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from jebao_flow.config import AppConfig, DeviceConfig
from jebao_flow.groups.calculator import PatternCalculator
from jebao_flow.groups.manager import GroupManager
from jebao_flow.groups.models import (
    GroupConfig,
    GroupExecutionStrategy,
    OfflinePolicy,
)
from jebao_flow.groups.plan import DeviceActuationLimits, GroupTickPlan
from jebao_flow.groups.runtime import (
    PlanAtomicGroupDispatchRuntime,
    UnresolvedGroupSafetyStopError,
    WriteGuard,
)
from jebao_flow.local_control.catalog import LocalControlCatalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator
from jebao_flow.local_control.group import SoftwareIndependentGroupActuator
from jebao_flow.local_control.group_supervisor import (
    GROUP_EMERGENCY_STOP_BLOCKS_DIRECT_CONTROL,
    GROUP_FORCED_OFF_WILL_REVERT,
    GroupSupervisorCycle,
    LocalControlGroupSupervisor,
)
from jebao_flow.local_control.models import FreshInstallManifest, LocalDeviceBinding
from jebao_flow.local_control.state import FreshDeviceState

_GROUP_FLOW_MINIMUM = 30
_GROUP_FLOW_MAXIMUM = 75


class LocalControlGroupServiceError(RuntimeError):
    """The isolated group lifecycle could not complete a requested local operation."""


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

    @property
    def available(self) -> bool:
        return self.actual_output is not GroupActualOutput.UNKNOWN

    def attributes_dict(self) -> dict[str, object]:
        return {
            "actual_output": self.actual_output.value,
            "config_relative": True,
            "install_generation": self.install_generation,
            "manual_override_members": list(self.manual_override_members),
            "ownership_diagnostic_reason": self.ownership_diagnostic_reason,
            "pattern_conformance": "unknown",
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
    group_id: str
    member_ids: tuple[str, ...]
    member_enabled: tuple[tuple[str, bool], ...]
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
    ) -> None:
        if not isinstance(config, AppConfig):
            raise TypeError("group lifecycle requires current AppConfig")
        if not isinstance(manifest, FreshInstallManifest):
            raise TypeError("group lifecycle requires a current install manifest")
        if not isinstance(catalog, LocalControlCatalog):
            raise TypeError("group lifecycle requires the current local-control catalog")
        if not isinstance(operation_lock, asyncio.Lock):
            raise TypeError("group lifecycle requires the shared operation lock")

        self._expected_members = tuple(device.logical_member for device in manifest.devices)
        self._install_generation = manifest.generation
        self._operation_lock = operation_lock
        self._fresh_cycle_count = 0
        self._closed = False
        devices_by_id = {device.id: device for device in config.devices}
        bindings_by_id = {device.logical_member: device for device in manifest.devices}
        self._groups = tuple(
            self._construct_group(
                group,
                devices_by_id=devices_by_id,
                bindings_by_id=bindings_by_id,
                catalog=catalog,
                coordinator=coordinator,
                operation_lock=operation_lock,
            )
            for group in config.groups
        )
        self._group_by_member: dict[str, _ManagedGroup] = {}
        for item in self._groups:
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
                group_id=group.id,
                member_ids=member_ids,
                member_enabled=member_enabled,
                supervisor=None,
                state=GroupLifecycleState.UNCONSTRUCTED,
                diagnostic_reason=error.reason,
            )
        except Exception:
            return _ManagedGroup(
                group_id=group.id,
                member_ids=member_ids,
                member_enabled=member_enabled,
                supervisor=None,
                state=GroupLifecycleState.UNCONSTRUCTED,
                diagnostic_reason="group_construction_failed",
            )
        return _ManagedGroup(
            group_id=group.id,
            member_ids=member_ids,
            member_enabled=member_enabled,
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


__all__ = [
    "DirectControlOwnershipDecision",
    "GROUP_EMERGENCY_STOP_BLOCKS_DIRECT_CONTROL",
    "GROUP_FORCED_OFF_WILL_REVERT",
    "GroupActualOutput",
    "GroupLifecycleItem",
    "GroupLifecycleSnapshot",
    "GroupLifecycleState",
    "GroupOutputState",
    "LocalControlGroupService",
    "LocalControlGroupServiceError",
]
