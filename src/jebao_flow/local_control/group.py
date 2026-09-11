"""Evidence-gated actuation for one complete software-independent group plan.

The actuator remains useful as a one-shot boundary and also implements the complete-plan port used
by :class:`PlanAtomicGroupDispatchRuntime`. It never reconstructs a transaction from the existing
per-device runtime callbacks: one immutable plan reaches one current resolution, one all-member
lease, and one plan-wide journal record with its generation guard intact.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from jebao_flow.groups.models import GroupState
from jebao_flow.groups.plan import (
    DeviceActuationLimits,
    GroupTickPlan,
    MemberAction,
    MemberDecision,
)
from jebao_flow.groups.runtime import (
    GroupPlanSupersededError,
    PreWriteDispatchError,
    WriteGuard,
    group_plan_identity_sha256,
)
from jebao_flow.local_control.coordinator import (
    LocalControlBatchTarget,
    LocalControlCoordinator,
    LocalControlExecutionError,
    LocalControlResult,
)
from jebao_flow.local_control.journal import TransactionPhase
from jebao_flow.local_control.models import LocalControlIntent, LocalDeviceBinding
from jebao_flow.operator_capture.app_catalog import declared_app_integer_range
from jebao_flow.protocol.models import DeviceTarget
from jebao_flow.protocol.profiles import DC_PUMP_PRO, LOCAL_WAVEMAKER, LOCAL_WAVEMAKER_PRO

_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
_SUPPORTED_PRODUCT_KEYS = frozenset(
    {
        LOCAL_WAVEMAKER_PRO.product_key,
        LOCAL_WAVEMAKER.product_key,
        DC_PUMP_PRO.product_key,
    }
)
_CANONICAL_OFF = DeviceTarget(enabled=False, power=0)
_OPTIONAL_TARGET_FIELDS = ("mode", "frequency", "linkage", "timer_enabled")


class GroupActuationOutcome(StrEnum):
    """Terminal result returned by the one-shot group actuator."""

    NO_DISPATCH = "no_dispatch"
    SUPERSEDED = "superseded"
    VERIFIED = "verified"


@dataclass(frozen=True, slots=True)
class GroupActuationResult:
    """Public result without stable physical identity material."""

    request_id: str
    group_id: str
    generation: int
    member_count: int
    dispatched_member_count: int
    outcome: GroupActuationOutcome
    transaction: LocalControlResult | None = None

    @property
    def accepted(self) -> bool:
        return self.transaction is not None and self.transaction.accepted

    def public_dict(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "group_id": self.group_id,
            "generation": self.generation,
            "member_count": self.member_count,
            "dispatched_member_count": self.dispatched_member_count,
            "outcome": self.outcome.value,
            "hardware_action_requested": self.transaction is not None,
            "accepted": self.accepted,
            "transaction": (None if self.transaction is None else self.transaction.public_dict()),
            "physical_identity_keys_exposed": False,
        }


class SoftwareIndependentGroupActuator:
    """Compile one planner plan into existing qualified scalar operations.

    The request id is the durable journal idempotency identity. It is not a device identity or an
    approval gate. Device identity remains the complete current ``LocalDeviceBinding`` resolved by
    the coordinator, and qualification remains per physical device and component function.
    """

    def __init__(
        self,
        *,
        group_id: str,
        member_ids: tuple[str, ...],
        bindings: Mapping[str, LocalDeviceBinding],
        device_limits: Mapping[str, DeviceActuationLimits],
        coordinator: LocalControlCoordinator,
    ) -> None:
        if not group_id or not group_id.isascii():
            raise ValueError("group id must be non-empty ASCII")
        if not member_ids or len(member_ids) != len(set(member_ids)):
            raise ValueError("group members must be non-empty and unique")
        _require_exact_mapping("stable bindings", bindings, member_ids)
        _require_exact_mapping("device limits", device_limits, member_ids)
        if not isinstance(coordinator, LocalControlCoordinator):
            raise TypeError("group actuator requires a LocalControlCoordinator")

        copied_bindings: dict[str, LocalDeviceBinding] = {}
        copied_limits: dict[str, DeviceActuationLimits] = {}
        physical_keys: list[str] = []
        for member_id in member_ids:
            binding = bindings[member_id]
            limits = device_limits[member_id]
            if not isinstance(binding, LocalDeviceBinding):
                raise TypeError("group stable bindings must be LocalDeviceBinding values")
            if binding.logical_member != member_id:
                raise ValueError("group member key does not match its stable logical binding")
            if binding.product_key not in _SUPPORTED_PRODUCT_KEYS:
                raise ValueError(
                    "software-independent Flow actuation is unavailable for this product"
                )
            if not isinstance(limits, DeviceActuationLimits):
                raise TypeError("group device limits must be DeviceActuationLimits values")
            copied_bindings[member_id] = binding
            copied_limits[member_id] = limits
            physical_keys.append(binding.physical_identity_key)
        if len(physical_keys) != len(set(physical_keys)):
            raise ValueError("group members must bind distinct physical identities")

        self._group_id = group_id
        self._member_ids = member_ids
        self._bindings = copied_bindings
        self._device_limits = copied_limits
        self._coordinator = coordinator

    @property
    def group_id(self) -> str:
        return self._group_id

    @property
    def member_ids(self) -> tuple[str, ...]:
        return self._member_ids

    def validate_plan(self, plan: GroupTickPlan) -> None:
        """Validate the complete plan without resolving endpoints or creating durable state."""

        self._validate_plan(plan)

    async def execute(
        self,
        *,
        request_id: str,
        plan: GroupTickPlan,
        pre_send_guard: WriteGuard | None = None,
    ) -> GroupActuationResult:
        """Execute one immutable plan without starting a recurring tick loop."""

        if not isinstance(request_id, str) or _REQUEST_ID_RE.fullmatch(request_id) is None:
            raise ValueError("group request id must be an 8..128 ASCII token")
        ordered_decisions = self._validate_plan(plan)
        targets = tuple(
            self._compile_target(request_id, decision)
            for decision in ordered_decisions
            if decision.action is MemberAction.DISPATCH
        )
        if not targets:
            return GroupActuationResult(
                request_id=request_id,
                group_id=self._group_id,
                generation=plan.generation,
                member_count=len(self._member_ids),
                dispatched_member_count=0,
                outcome=GroupActuationOutcome.NO_DISPATCH,
            )

        transaction = await self._coordinator.execute_batch(
            request_id=request_id,
            intent_binding=self._intent_binding(request_id, plan, ordered_decisions),
            lease_targets=tuple(self._bindings[member] for member in self._member_ids),
            targets=targets,
            pre_send_guard=pre_send_guard,
        )
        if transaction.phase is TransactionPhase.SUPERSEDED:
            return GroupActuationResult(
                request_id=request_id,
                group_id=self._group_id,
                generation=plan.generation,
                member_count=len(self._member_ids),
                dispatched_member_count=len(targets),
                outcome=GroupActuationOutcome.SUPERSEDED,
                transaction=transaction,
            )
        if not transaction.accepted:
            raise LocalControlExecutionError(
                "a terminal non-verified group request cannot be reported as accepted"
            )
        return GroupActuationResult(
            request_id=request_id,
            group_id=self._group_id,
            generation=plan.generation,
            member_count=len(self._member_ids),
            dispatched_member_count=len(targets),
            outcome=GroupActuationOutcome.VERIFIED,
            transaction=transaction,
        )

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
        """Adapt one complete runtime job to the durable plan-wide transaction boundary."""

        try:
            if generation != plan.generation:
                raise ValueError("plan port generation does not match the immutable plan")
            if plan_identity_sha256 != group_plan_identity_sha256(plan):
                raise ValueError("plan port identity does not match the immutable plan")
            if safety_token is None:
                raise ValueError("plan port safety token must be opaque and non-null")
            if not callable(guard):
                raise TypeError("plan port guard must be callable")
        except (TypeError, ValueError) as error:
            raise PreWriteDispatchError("plan port rejected invalid execution context") from error

        try:
            result = await self.execute(
                request_id=request_id,
                plan=plan,
                pre_send_guard=guard,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            try:
                definitely_not_sent = self._coordinator.request_has_no_durable_state(request_id)
            except Exception:
                definitely_not_sent = False
            if definitely_not_sent:
                raise PreWriteDispatchError(
                    "group plan failed before durable state or any physical frame"
                ) from error
            raise
        if result.outcome is GroupActuationOutcome.SUPERSEDED:
            raise GroupPlanSupersededError(
                "group plan was durably superseded before its remaining frames"
            )
        if not result.accepted:
            raise LocalControlExecutionError("group plan port returned a non-verified result")

    def _validate_plan(self, plan: GroupTickPlan) -> tuple[MemberDecision, ...]:
        if not isinstance(plan, GroupTickPlan):
            raise TypeError("group actuator requires a GroupTickPlan")
        if plan.group_id != self._group_id:
            raise ValueError("group plan id does not match the bound actuator")
        if type(plan.generation) is not int or plan.generation < 1:
            raise ValueError("group plan generation must be positive")
        if not isinstance(plan.derived_status, GroupState):
            raise TypeError("group plan derived status must be a GroupState")
        if type(plan.failure_policy_stopped) is not bool:
            raise TypeError("group failure-policy marker must be boolean")

        decisions_by_member: dict[str, MemberDecision] = {}
        for decision in plan.decisions:
            if not isinstance(decision, MemberDecision):
                raise TypeError("group decisions must be MemberDecision values")
            if decision.device in decisions_by_member:
                raise ValueError("group plan contains duplicate member decisions")
            decisions_by_member[decision.device] = decision
        _require_exact_mapping("group decisions", decisions_by_member, self._member_ids)
        ordered = tuple(decisions_by_member[member] for member in self._member_ids)
        for decision in ordered:
            self._validate_decision(decision)

        dispatches = tuple(
            decision for decision in ordered if decision.action is MemberAction.DISPATCH
        )
        if plan.derived_status in {GroupState.STOPPED, GroupState.EMERGENCY_STOP} and (
            len(dispatches) != len(ordered)
            or any(decision.target != _CANONICAL_OFF for decision in dispatches)
        ):
            raise ValueError("stopped group plans require canonical OFF for every member")
        if plan.derived_status in {GroupState.STARTING, GroupState.MAINTENANCE} and any(
            decision.target != _CANONICAL_OFF for decision in dispatches
        ):
            raise ValueError("held group plans may only dispatch canonical OFF")
        if plan.failure_policy_stopped and any(
            (decision.action is MemberAction.DISPATCH and decision.target != _CANONICAL_OFF)
            or (
                decision.action is not MemberAction.DISPATCH
                and decision.action not in {MemberAction.SKIP_OFFLINE, MemberAction.SKIP_UNKNOWN}
            )
            for decision in ordered
        ):
            raise ValueError(
                "failure-policy stopped plans require canonical OFF or unavailable skips"
            )
        return ordered

    def _validate_decision(self, decision: MemberDecision) -> None:
        if not isinstance(decision.action, MemberAction):
            raise TypeError("group member action must be a MemberAction")
        if type(decision.control_epoch) is not int or decision.control_epoch < 0:
            raise ValueError("group member control epoch must be non-negative")
        if not isinstance(decision.requested_target, DeviceTarget):
            raise TypeError("group requested target must be a DeviceTarget")
        if decision.requested_power != decision.requested_target.power:
            raise ValueError("group requested power does not match its requested target")
        trusted_limits = self._device_limits[decision.device]
        if decision.limits_used != trusted_limits:
            raise ValueError("group plan limits do not match the actuator-bound limits")
        if decision.action is not MemberAction.DISPATCH:
            if decision.target is not None:
                raise ValueError("skipped group decisions cannot contain a target")
            return

        target = decision.target
        if not isinstance(target, DeviceTarget):
            raise TypeError("dispatched group decisions require a DeviceTarget")
        if decision.normalized_power != target.power:
            raise ValueError("group normalized power does not match its target")
        if (
            type(decision.effective_max_power) is not int
            or decision.effective_max_power < 0
            or decision.effective_max_power > trusted_limits.max_power
        ):
            raise ValueError("group effective maximum exceeds its bound device limits")
        if any(
            getattr(target, field_name) != getattr(decision.requested_target, field_name)
            for field_name in _OPTIONAL_TARGET_FIELDS
        ):
            raise ValueError("group normalization cannot change optional target fields")
        if any(getattr(target, field_name) is not None for field_name in _OPTIONAL_TARGET_FIELDS):
            raise ValueError(
                "software-independent Flow actuation cannot change mode, frequency, "
                "linkage, or timer"
            )
        if not target.enabled:
            if target != _CANONICAL_OFF:
                raise ValueError("disabled group targets must use canonical OFF")
            return
        if not decision.requested_target.enabled:
            raise ValueError("group normalization cannot turn a requested OFF target on")
        binding = self._bindings[decision.device]
        flow_minimum, flow_maximum = declared_app_integer_range(
            binding.product_key,
            "manual_flow",
        )
        if not flow_minimum <= target.power <= flow_maximum:
            raise ValueError(
                "group Flow must stay inside the declared "
                f"{flow_minimum}..{flow_maximum} app range"
            )
        if not trusted_limits.min_power <= target.power <= trusted_limits.max_power:
            raise ValueError("group target is outside its bound device limits")
        if target.power > decision.effective_max_power:
            raise ValueError("group target exceeds its effective maximum")
        if target.power % trusted_limits.power_step != 0:
            raise ValueError("group target does not match its absolute power step")

    def _compile_target(
        self,
        request_id: str,
        decision: MemberDecision,
    ) -> LocalControlBatchTarget:
        binding = self._bindings[decision.device]
        if decision.target is None:
            raise AssertionError("validated dispatch decision lost its target")
        if decision.target == _CANONICAL_OFF:
            intents = (
                LocalControlIntent(
                    request_id=request_id,
                    target=binding,
                    function="power",
                    value=False,
                ),
            )
        else:
            # Flow is deliberately established while the device is off or at its prior state.
            # SwitchON follows as a separate already-qualified scalar frame; no new composite
            # payload shape is invented here.
            intents = (
                LocalControlIntent(
                    request_id=request_id,
                    target=binding,
                    function="manual_flow",
                    value=decision.target.power,
                ),
                LocalControlIntent(
                    request_id=request_id,
                    target=binding,
                    function="power",
                    value=True,
                ),
            )
        return LocalControlBatchTarget(target=binding, intents=intents)

    def _intent_binding(
        self,
        request_id: str,
        plan: GroupTickPlan,
        decisions: tuple[MemberDecision, ...],
    ) -> dict[str, object]:
        return {
            "request_id": request_id,
            "operation": "software_independent_group_target",
            "group_id": self._group_id,
            "members": [
                {
                    "logical_member": decision.device,
                    "logical_role": self._bindings[decision.device].logical_role,
                    "product_key": self._bindings[decision.device].product_key,
                    "physical_identity_key": self._bindings[decision.device].physical_identity_key,
                    "action": decision.action.value,
                    "control_epoch": decision.control_epoch,
                    "target": (
                        None if decision.target is None else decision.target.model_dump(mode="json")
                    ),
                }
                for decision in decisions
            ],
            # Generation and timestamp remain execution provenance. They are intentionally absent
            # from durable idempotency identity so a retry of the same physical intent does not
            # become a different operation merely because the planner was reconstructed.
            "derived_status": plan.derived_status.value,
            "failure_policy_stopped": plan.failure_policy_stopped,
        }


def _require_exact_mapping(
    label: str,
    values: Mapping[str, object],
    members: tuple[str, ...],
) -> None:
    if set(values) == set(members):
        return
    missing = ", ".join(sorted(set(members) - set(values))) or "none"
    unexpected = ", ".join(sorted(set(values) - set(members))) or "none"
    raise ValueError(
        f"{label} must match group members; missing={missing}; unexpected={unexpected}"
    )


__all__ = [
    "GroupActuationOutcome",
    "GroupActuationResult",
    "SoftwareIndependentGroupActuator",
]
