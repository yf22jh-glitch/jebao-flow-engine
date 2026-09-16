"""MQTT command contract for qualified local control and Home Assistant.

Commands are consumed sequentially at MQTT QoS 0, retained or duplicate-marked commands are never
executed. Scalar request ids are created at the runtime boundary; whole-schedule requests carry
caller-owned ids for durable replay detection. Endpoint addresses are still
resolved afresh by the coordinator for every operation and never appear in this contract.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

import aiomqtt

from jebao_flow.config import MqttConfig
from jebao_flow.local_control.catalog import LocalControlCatalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator, LocalControlResult
from jebao_flow.local_control.group_service import (
    GROUP_FORCED_OFF_WILL_REVERT,
    DirectControlOwnershipDecision,
    GroupActualOutput,
    GroupCommandResult,
    GroupMemberResumeResult,
    GroupOutputState,
    LocalControlGroupCommandRejected,
)
from jebao_flow.local_control.ha import (
    GroupControlHomeAssistantEntityDescriptor,
    GroupMemberResumeHomeAssistantEntityDescriptor,
    HomeAssistantEntityDescriptor,
    HomeAssistantPlatform,
    HomeAssistantProjection,
)
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    LocalControlIntent,
    LocalDeviceBinding,
)
from jebao_flow.local_control.pump_preview import ReturnPumpSchedule, preview_device_clock
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan
from jebao_flow.local_control.state import FreshDeviceState, FreshStatePoller
from jebao_flow.local_control.wavemaker import NativeWavemakerPlan, WavemakerSchedule
from jebao_flow.local_control.wavemaker_lifecycle import NativeWavemakerController


class LocalControlMqttError(RuntimeError):
    """A command topic or payload did not match the published local-control contract."""


class LocalControlCommandConflictError(LocalControlMqttError):
    """A stable pre-journal ownership conflict rejected one direct command."""

    def __init__(self, diagnostic_reason: str) -> None:
        if not diagnostic_reason or not diagnostic_reason.isascii():
            raise ValueError("command conflict reason must be non-empty ASCII")
        self.diagnostic_reason = diagnostic_reason
        super().__init__(diagnostic_reason)


class LocalControlGroupCommandError(LocalControlMqttError):
    """A stable group-route diagnostic rejected one ON/OFF command."""

    def __init__(self, diagnostic_reason: str) -> None:
        if not diagnostic_reason or not diagnostic_reason.isascii():
            raise ValueError("group command diagnostic must be non-empty ASCII")
        self.diagnostic_reason = diagnostic_reason
        super().__init__(diagnostic_reason)


COMMAND_QOS = 0
_PASSWORD_FROM_CONFIG = object()


class GroupLifecycleService(Protocol):
    """Small lifecycle surface consumed by the existing state adapter."""

    async def observe_fresh_cycle(
        self,
        states: tuple[FreshDeviceState, ...],
    ) -> object: ...

    async def aclose(self) -> None: ...

    def public_dict(self) -> dict[str, object]: ...

    def group_output_states(self) -> tuple[GroupOutputState, ...]: ...


class DirectControlOwnershipService(Protocol):
    """Pre-journal ownership boundary used by existing direct-device routes."""

    @property
    def operation_lock(self) -> asyncio.Lock: ...

    async def claim_direct_ownership(
        self,
        logical_member: str,
        *,
        power_on_requested: bool = False,
    ) -> DirectControlOwnershipDecision: ...

    async def note_verified_direct_power_on(self, logical_member: str) -> None: ...


class GroupCommandService(Protocol):
    """Qualified plan-atomic ON/OFF surface used by the separate group router."""

    @property
    def configured_group_ids(self) -> tuple[str, ...]: ...

    @property
    def qualified_group_ids(self) -> tuple[str, ...]: ...

    @property
    def qualified_group_members(self) -> tuple[tuple[str, str], ...]: ...

    async def apply_group_switch(
        self,
        group_id: str,
        requested_on: bool,
    ) -> GroupCommandResult: ...

    async def resume_group_member(
        self,
        group_id: str,
        logical_member: str,
    ) -> GroupMemberResumeResult: ...


@dataclass(frozen=True, slots=True)
class RoutedCommandResult:
    request_id: str
    state_topic: str | None
    state_value: str | None
    result: LocalControlResult
    ownership: DirectControlOwnershipDecision | None = None

    def public_result(self) -> dict[str, object]:
        payload = self.result.public_dict()
        if self.ownership is not None:
            payload["direct_control_ownership"] = self.ownership.public_dict()
        return payload


@dataclass(frozen=True, slots=True)
class RoutedSchedulePreview:
    """Compiler output, never a claim about device state or permission to write."""

    request_id: str
    logical_member: str
    schedule: WavemakerSchedule

    def public_result(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "logical_member": self.logical_member,
            "operation": "preview",
            **self.schedule.preview(),
        }


@dataclass(frozen=True, slots=True)
class RoutedNativeResult:
    request_id: str
    operation: str
    result: dict[str, object]
    failed: bool = False

    def public_result(self) -> dict[str, object]:
        return {
            "request_id": self.request_id,
            "operation": self.operation,
            "status": "failed" if self.failed else "result",
            "result": self.result,
        }


@dataclass(frozen=True, slots=True)
class RoutedGroupCommandResult:
    """One MQTT request id and the bounded group-service result it names."""

    request_id: str
    result: GroupCommandResult | GroupMemberResumeResult

    def public_result(self) -> dict[str, object]:
        payload = self.result.public_dict()
        payload["request_id"] = self.request_id
        return payload


@dataclass(frozen=True, slots=True)
class _ScalarRoute:
    entity: HomeAssistantEntityDescriptor
    target: LocalDeviceBinding


@dataclass(frozen=True, slots=True)
class _ScheduleRoute:
    entity: HomeAssistantEntityDescriptor
    plan: FreshMasterSchedulePlan
    slave_index: int


@dataclass(frozen=True, slots=True)
class _WavemakerScheduleRoute:
    entity: HomeAssistantEntityDescriptor
    target: LocalDeviceBinding


class LocalControlMqttRouter:
    """Route only command topics that were actually projected as qualified controls."""

    def __init__(
        self,
        *,
        projection: HomeAssistantProjection,
        manifest: FreshInstallManifest,
        catalog: LocalControlCatalog,
        schedule_plans: tuple[FreshMasterSchedulePlan, ...],
        coordinator: LocalControlCoordinator | None,
        operation_lock: asyncio.Lock | None = None,
        ownership_service: DirectControlOwnershipService | None = None,
        native_controller: NativeWavemakerController | None = None,
    ) -> None:
        self._coordinator = coordinator
        self._operation_lock = operation_lock
        self._ownership_service = ownership_service
        self._native_controller = native_controller
        self._native_topic = projection.native_request_topic
        self._native_members = frozenset(projection.native_wavemaker_members)
        self._native_attach_members = frozenset(projection.native_attach_members)
        self._devices = {device.logical_member: device for device in manifest.devices}
        self._preview_routes: dict[
            str, tuple[HomeAssistantEntityDescriptor, LocalDeviceBinding]
        ] = {}
        if self._native_topic is not None and (
            native_controller is None or operation_lock is None
            or projection.control_surface_diagnostic_reason is not None
        ):
            raise ValueError("native MQTT requires a write runtime and shared operation lock")
        if ownership_service is not None and (
            operation_lock is None or ownership_service.operation_lock is not operation_lock
        ):
            raise ValueError("direct ownership and device writes require one shared operation lock")
        device_by_member = {device.logical_member: device for device in manifest.devices}
        self._routes: dict[str, _ScalarRoute | _ScheduleRoute | _WavemakerScheduleRoute] = {}
        plans_by_topic: dict[str, tuple[FreshMasterSchedulePlan, int]] = {}
        for plan in schedule_plans:
            for slave_index, slave in enumerate(plan.slaves):
                for slot_index in range(48):
                    group_key = f"{plan.master.logical_member}_{plan.linkage_mode.value}"
                    topic = (
                        f"{projection.topic_prefix}/local/schedules/{group_key}/slaves/"
                        f"{slave.target.logical_member}/slots/{slot_index}/flow/set"
                    )
                    if topic in plans_by_topic:
                        raise ValueError("schedule plans produce a duplicate command topic")
                    plans_by_topic[topic] = (plan, slave_index)

        for entity in projection.entities:
            if entity.preview_request_topic is not None:
                self._preview_routes[entity.preview_request_topic] = (
                    entity, device_by_member[entity.logical_member]
                )
            if entity.schedule_request_topic is not None:
                target = device_by_member.get(entity.logical_member)
                if target is None:
                    raise ValueError("schedule API is not in the current install manifest")
                descriptor = catalog.find(target.product_key, "schedule")
                if (
                    projection.control_surface_diagnostic_reason is not None
                    or not descriptor.control_exposed_for(target.physical_identity_key)
                ):
                    raise ValueError("unqualified device received a schedule request topic")
                if entity.schedule_request_topic in self._routes:
                    raise ValueError("local-control MQTT command topic is duplicated")
                self._routes[entity.schedule_request_topic] = _WavemakerScheduleRoute(
                    entity, target
                )
            if entity.command_topic is None:
                continue
            if entity.command_topic in self._routes:
                raise ValueError("local-control MQTT command topic is duplicated")
            if entity.slot_index is None:
                target = device_by_member.get(entity.logical_member)
                if target is None:
                    raise ValueError("control entity is not in the current install manifest")
                descriptor = catalog.find(target.product_key, entity.function)
                if not descriptor.control_exposed:
                    raise ValueError("unqualified function received an MQTT command topic")
                self._routes[entity.command_topic] = _ScalarRoute(entity, target)
                continue
            match = plans_by_topic.get(entity.command_topic)
            if match is None:
                raise ValueError("schedule entity does not identify exactly one current topology")
            plan, slave_index = match
            self._routes[entity.command_topic] = _ScheduleRoute(
                entity,
                plan,
                slave_index,
            )
        if self._routes and self._coordinator is None:
            raise ValueError("qualified MQTT controls require a write coordinator")

    @property
    def command_topics(self) -> tuple[str, ...]:
        topics = set(self._routes)
        if self._native_topic is not None:
            topics.add(self._native_topic)
        return tuple(sorted(topics))

    @property
    def request_topics(self) -> tuple[str, ...]:
        """All subscriptions, including write-free preview requests in observer mode."""
        return tuple(sorted((*self.command_topics, *self._preview_routes)))

    async def handle(
        self, topic: str, payload: bytes | str
    ) -> RoutedCommandResult | RoutedSchedulePreview | RoutedNativeResult:
        if self._operation_lock is None:
            return await self._handle(topic, payload)
        async with self._operation_lock:
            return await self._handle(topic, payload)

    async def _handle(
        self, topic: str, payload: bytes | str
    ) -> RoutedCommandResult | RoutedSchedulePreview | RoutedNativeResult:
        if topic in self._preview_routes:
            return self._handle_preview(topic, payload)
        if self._native_topic is not None and topic == self._native_topic:
            return await self._handle_native(payload)
        coordinator = self._coordinator
        if coordinator is None:
            raise LocalControlMqttError("read-only MQTT runtime has no command executor")
        try:
            route = self._routes[topic]
        except KeyError as error:
            raise LocalControlMqttError(
                "MQTT topic is not a published qualified control"
            ) from error
        if isinstance(route, _WavemakerScheduleRoute):
            return await self._handle_wavemaker_schedule(route, payload, coordinator)
        value = _parse_entity_value(route.entity, payload)
        logical_member = (
            route.target.logical_member
            if isinstance(route, _ScalarRoute)
            else route.plan.slaves[route.slave_index].target.logical_member
        )
        power_on_requested = (
            isinstance(route, _ScalarRoute)
            and route.entity.function == "power"
            and value is True
        )
        ownership = (
            None
            if self._ownership_service is None
            else await self._ownership_service.claim_direct_ownership(
                logical_member,
                power_on_requested=power_on_requested,
            )
        )
        if ownership is not None and not ownership.allowed:
            reason = ownership.diagnostic_reason
            if reason is None:
                raise LocalControlMqttError(
                    "rejected direct-control ownership requires a diagnostic reason"
                )
            raise LocalControlCommandConflictError(reason)
        request_id = f"mqtt_{secrets.token_hex(16)}"
        if isinstance(route, _ScalarRoute):
            intent = LocalControlIntent(
                request_id=request_id,
                target=route.target,
                function=route.entity.function,
                value=value,
            )
            result = await coordinator.execute(intent)
        else:
            result = await coordinator.execute_schedule_flow(
                request_id=request_id,
                linkage_mode=route.plan.linkage_mode,
                master=route.plan.master,
                slave=route.plan.slaves[route.slave_index].target,
                slot_index=route.entity.slot_index,
                flow=value,
            )
        if self._ownership_service is not None and power_on_requested and result.accepted:
            await self._ownership_service.note_verified_direct_power_on(logical_member)
        elif (
            ownership is not None
            and ownership.diagnostic_reason == GROUP_FORCED_OFF_WILL_REVERT
        ):
            ownership = replace(ownership, diagnostic_reason=None)
        return RoutedCommandResult(
            request_id=request_id,
            state_topic=route.entity.state_topic,
            state_value=_state_value(route.entity, value) if result.accepted else None,
            result=result,
            ownership=ownership,
        )

    async def _handle_wavemaker_schedule(
        self,
        route: _WavemakerScheduleRoute,
        payload: bytes | str,
        coordinator: LocalControlCoordinator,
    ) -> RoutedCommandResult | RoutedSchedulePreview:
        operation, intent, schedule = _parse_wavemaker_request(route.target, payload)
        if operation == "preview":
            return RoutedSchedulePreview(intent.request_id, route.target.logical_member, schedule)

        # Existing ids must be reconciled by the coordinator before touching group ownership.
        # It returns terminal history without sending, or rejects conflicting/unfinished ids.
        ownership = None
        if (
            coordinator.request_has_no_durable_state(intent.request_id)
            and self._ownership_service is not None
        ):
            ownership = await self._ownership_service.claim_direct_ownership(
                route.target.logical_member,
                power_on_requested=False,
            )
            if not ownership.allowed:
                if ownership.diagnostic_reason is None:
                    raise LocalControlMqttError("rejected ownership requires a diagnostic reason")
                raise LocalControlCommandConflictError(ownership.diagnostic_reason)
        result = await coordinator.execute(intent)
        # Even VERIFIED may be historical replay. Never turn the supplied image into live state.
        return RoutedCommandResult(intent.request_id, None, None, result, ownership)

    async def _handle_native(self, payload: bytes | str) -> RoutedNativeResult:
        controller = self._native_controller
        if controller is None:
            raise LocalControlMqttError("native controller is unavailable")
        request = _parse_native_request(payload)
        request_id, operation = request["request_id"], request["operation"]
        record_id = request_id
        if operation in {"preview", "attach"}:
            plan = NativeWavemakerPlan.model_validate(request["plan"])
            plan.compile_images()
            if plan.mode != "async" and (
                request.get("await_operator_observation", False)
                or request.get("master_cycle_on_async_failure", False)
            ):
                raise LocalControlMqttError("observation and master cycle options require Async")
            names = (plan.master, *(slave.logical_member for slave in plan.slaves))
            if len(names) != 2 or not set(names) <= self._native_members:
                raise LocalControlMqttError("native plan requires two current Pro members")
            targets = tuple(self._devices[name] for name in names)
            if operation == "preview":
                return RoutedNativeResult(request_id, operation, {
                    **plan.preview(), "hardware_write": False, "device_acceptance": "unverified",
                })
            if controller.request_has_no_durable_state(request_id):
                if not set(names) <= self._native_attach_members:
                    raise LocalControlMqttError(
                        "native attach requires each member's current qualifications"
                    )
                for name in names:
                    if self._ownership_service is not None:
                        decision = await self._ownership_service.claim_direct_ownership(
                            name, power_on_requested=False,
                        )
                        if not decision.allowed:
                            raise LocalControlCommandConflictError(
                                decision.diagnostic_reason or "native_ownership_rejected"
                            )
        else:
            record_id = request["attachment_id" if operation == "detach" else "record_id"]
            targets = self._native_targets_for(record_id)
            if operation == "status":
                return RoutedNativeResult(request_id, operation, controller.status(record_id))

        try:
            if operation == "attach":
                result = await controller.attach(
                    request_id=request_id, plan=plan, targets=targets,
                    await_operator_observation=request.get("await_operator_observation", False),
                    master_cycle_on_async_failure=request.get(
                        "master_cycle_on_async_failure", False
                    ),
                )
            elif operation == "detach":
                result = await controller.detach(attachment_id=record_id, targets=targets)
            elif operation == "assist_detach":
                result = await controller.assist_detach(request_id=record_id, targets=targets)
            elif operation == "cycle_master":
                result = await controller.cycle_master(request_id=record_id, targets=targets)
            else:
                result = await controller.recover_native(request_id=record_id, targets=targets)
        except (OSError, RuntimeError, ValueError):
            # Keep the durable diagnostic, not an exception string or an optimistic success.
            try:
                self._native_targets_for(record_id)
                result = controller.status(record_id)
            except (KeyError, OSError, RuntimeError, ValueError):
                result = {"diagnostic_reason": "native_operation_failed_no_readable_record"}
            return RoutedNativeResult(request_id, operation, result, failed=True)
        return RoutedNativeResult(request_id, operation, result)

    def _native_targets_for(self, record_id: str) -> tuple[LocalDeviceBinding, ...]:
        controller = self._native_controller
        if controller is None:
            raise LocalControlMqttError("native controller is unavailable")
        saved = controller.targets_for(record_id)
        if any(target.logical_member not in self._native_members for target in saved):
            raise LocalControlMqttError("native record is outside the current Pro inventory")
        targets = tuple(self._devices[target.logical_member] for target in saved)
        if any(
            (old.product_key, old.physical_identity_key, old.logical_role)
            != (new.product_key, new.physical_identity_key, new.logical_role)
            for old, new in zip(saved, targets, strict=True)
        ):
            raise LocalControlMqttError("native record does not match current stable bindings")
        return targets

    def _handle_preview(self, topic: str, payload: bytes | str) -> RoutedNativeResult:
        entity, target = self._preview_routes[topic]
        request = _parse_bounded_json(payload)
        field = "local_time" if entity.function == "device_clock_sync" else "schedule"
        if not isinstance(request, dict) or set(request) != {"request_id", field}:
            raise LocalControlMqttError("preview requires request_id and its declared input")
        try:
            request_id = LocalControlIntent.validate_request_id(request["request_id"])
            if field == "local_time":
                result = preview_device_clock(
                    target.product_key, datetime.fromisoformat(request["local_time"])
                )
            else:
                value = request["schedule"]
                if not isinstance(value, dict):
                    raise ValueError("schedule must be an object")
                model = (
                    WavemakerSchedule if value.get("family") in {"pro", "bar"}
                    else ReturnPumpSchedule
                )
                plan = model.model_validate(value)
                if plan.product_key != target.product_key:
                    raise ValueError("preview family differs from current product")
                result = plan.preview()
        except (TypeError, ValueError) as error:
            raise LocalControlMqttError("preview violates the declared product contract") from error
        return RoutedNativeResult(request_id, "preview", result)


class LocalControlGroupCommandRouter:
    """Route group switches and member resumes without the physical-operation lock."""

    def __init__(
        self,
        *,
        projection: HomeAssistantProjection,
        service: GroupCommandService,
    ) -> None:
        self._service = service
        self._switch_routes: dict[str, GroupControlHomeAssistantEntityDescriptor] = {}
        for entity in projection.group_control_entities:
            if entity.command_topic in self._switch_routes:
                raise ValueError("group MQTT command topic is duplicated")
            self._switch_routes[entity.command_topic] = entity
        self._resume_routes: dict[
            str, GroupMemberResumeHomeAssistantEntityDescriptor
        ] = {}
        for entity in projection.group_resume_entities:
            if (
                entity.command_topic in self._switch_routes
                or entity.command_topic in self._resume_routes
            ):
                raise ValueError("group MQTT command topic is duplicated")
            self._resume_routes[entity.command_topic] = entity
        projected_groups = {entity.group_id for entity in self._switch_routes.values()}
        projected_members = {
            (entity.group_id, entity.logical_member)
            for entity in self._resume_routes.values()
        }
        self._inventory_consistent = (
            projected_groups == set(service.qualified_group_ids)
            and projected_members == set(service.qualified_group_members)
            and projected_groups <= set(service.configured_group_ids)
            and {group_id for group_id, _member in projected_members}
            <= set(service.configured_group_ids)
        )

    @property
    def command_topics(self) -> tuple[str, ...]:
        return tuple(sorted((*self._switch_routes, *self._resume_routes)))

    @property
    def inventory_consistent(self) -> bool:
        return self._inventory_consistent

    def owns(self, topic: str) -> bool:
        return topic in self._switch_routes or topic in self._resume_routes

    async def handle(self, topic: str, payload: bytes | str) -> RoutedGroupCommandResult:
        switch = self._switch_routes.get(topic)
        resume = self._resume_routes.get(topic)
        if switch is None and resume is None:
            raise LocalControlGroupCommandError(
                "group_command_topic_not_published"
            )
        requested_on: bool | None = None
        if switch is not None:
            requested_on = _parse_group_switch_value(payload)
        else:
            _parse_group_resume_value(payload)
        if not self._inventory_consistent:
            raise LocalControlGroupCommandError("group_command_inventory_mismatch")
        try:
            if switch is not None:
                if requested_on is None:
                    raise AssertionError("group switch parsing must produce a boolean")
                result: GroupCommandResult | GroupMemberResumeResult = (
                    await self._service.apply_group_switch(
                        switch.group_id,
                        requested_on,
                    )
                )
            else:
                if resume is None:
                    raise AssertionError("group route selection must be exhaustive")
                result = await self._service.resume_group_member(
                    resume.group_id,
                    resume.logical_member,
                )
        except LocalControlGroupCommandRejected as error:
            raise LocalControlGroupCommandError(error.diagnostic_reason) from error
        return RoutedGroupCommandResult(
            request_id=f"mqtt_group_{secrets.token_hex(16)}",
            result=result,
        )


class LocalControlMqttAdapter:
    """Publish the HA surface and execute its non-retained commands one at a time."""

    def __init__(
        self,
        config: MqttConfig,
        *,
        projection: HomeAssistantProjection,
        router: LocalControlMqttRouter,
        state_poller: FreshStatePoller,
        password: str | None | object = _PASSWORD_FROM_CONFIG,
        group_service: GroupLifecycleService | None = None,
        group_router: LocalControlGroupCommandRouter | None = None,
    ) -> None:
        if password is not _PASSWORD_FROM_CONFIG and not (
            password is None or isinstance(password, str)
        ):
            raise TypeError("MQTT password override must be text or null")
        self._config = config
        self._projection = projection
        self._router = router
        self._state_poller = state_poller
        self._password = password
        self._group_service = group_service
        self._group_router = group_router
        projected_group_topics = {
            entity.command_topic
            for entity in (
                *projection.group_control_entities,
                *projection.group_resume_entities,
            )
        }
        routed_group_topics = (
            set() if group_router is None else set(group_router.command_topics)
        )
        if projected_group_topics != routed_group_topics:
            raise ValueError("projected group command topics require one group router")
        if set(router.command_topics) & routed_group_topics:
            raise ValueError("device and group command topics must be disjoint")
        self._availability = projection.global_availability_topic
        self._status = f"{projection.topic_prefix}/local/status"
        self._group_status = f"{projection.topic_prefix}/local/groups/status"

    async def run(self, stop_event: asyncio.Event) -> None:
        try:
            await self._run_connected(stop_event)
        finally:
            if self._group_service is not None:
                # Group close errors deliberately live outside the task-cancellation gather below.
                # In particular, an unresolved mandatory OFF must escape the adapter.
                await self._group_service.aclose()

    async def _run_connected(self, stop_event: asyncio.Event) -> None:
        password = (
            self._config.resolve_password()
            if self._password is _PASSWORD_FROM_CONFIG
            else self._password
        )
        if password is not None and not isinstance(password, str):
            raise TypeError("MQTT password must be text or null")
        will = aiomqtt.Will(self._availability, "offline", qos=1, retain=True)
        async with aiomqtt.Client(
            self._config.host,
            self._config.port,
            username=self._config.username,
            password=password,
            identifier=f"jebao-flow-local-{secrets.token_hex(8)}",
            will=will,
        ) as client:
            # Clear availability before subscriptions or discovery. Retained state from a previous
            # process must never look current during this process's initial read.
            await client.publish(self._availability, "offline", qos=1, retain=True)
            for logical_member in self._state_poller.logical_members:
                await client.publish(
                    self._projection.device_availability_topic(logical_member),
                    "offline",
                    qos=1,
                    retain=True,
                )
            for entity in self._projection.group_entities:
                await client.publish(
                    entity.availability_topic,
                    "offline",
                    qos=1,
                    retain=True,
                )
            for entity in self._projection.group_control_entities:
                await client.publish(
                    entity.availability_topic,
                    "offline",
                    qos=1,
                    retain=True,
                )
            for entity in self._projection.group_resume_entities:
                await client.publish(
                    entity.availability_topic,
                    "offline",
                    qos=1,
                    retain=True,
                )
            for topic in self._projection.discovery_tombstone_topics():
                await client.publish(topic, b"", qos=1, retain=True)
            group_topics = () if self._group_router is None else self._group_router.command_topics
            for topic in (*self._router.request_topics, *group_topics):
                # At-most-once command delivery is intentional: losing one command is safer than
                # a broker replay applying an old value after a newer physical operation.
                await client.subscribe(topic, qos=COMMAND_QOS)
            for topic, payload in self._projection.discovery_records():
                await client.publish(
                    topic,
                    _canonical_json(payload),
                    qos=1,
                    retain=True,
                )
            for entity in self._projection.entities:
                if entity.control_available:
                    continue
                await client.publish(
                    entity.state_topic,
                    _canonical_json(
                        {
                            "stage": entity.stage,
                            "control_available": False,
                            "function": entity.function,
                            "diagnostic_reason": entity.diagnostic_reason,
                            **(
                                {"preview_request_topic": entity.preview_request_topic}
                                if entity.preview_request_topic is not None else {}
                            ),
                            **(
                                {"native_wavemaker_api": self._projection.native_wavemaker_api}
                                if entity.function == "schedule"
                                and entity.logical_member
                                in self._projection.native_wavemaker_members
                                else {}
                            ),
                            **(
                                {"schedule_request_topic": entity.schedule_request_topic}
                                if entity.schedule_request_topic is not None
                                else {}
                            ),
                        }
                    ),
                    qos=1,
                    retain=True,
                )
            if self._group_service is not None:
                await self._publish_group_status(client)
            await client.publish(self._availability, "online", qos=1, retain=True)

            stop_waiter = asyncio.create_task(stop_event.wait())
            consumer = asyncio.create_task(self._consume(client))
            if self._group_service is None:
                state_sync = asyncio.create_task(
                    self._state_poller.run(
                        lambda result: self._publish_fresh_state(client, result),
                        stop_event,
                    )
                )
            else:
                state_sync = asyncio.create_task(
                    self._state_poller.run(
                        lambda result: self._publish_fresh_state(client, result),
                        stop_event,
                        observe_cycle=lambda results: self._observe_group_cycle(
                            client,
                            results,
                        ),
                    )
                )
            try:
                done, _pending = await asyncio.wait(
                    {stop_waiter, consumer, state_sync},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if consumer in done:
                    await consumer
                if state_sync in done:
                    await state_sync
            finally:
                try:
                    await client.publish(self._availability, "offline", qos=1, retain=True)
                finally:
                    consumer.cancel()
                    state_sync.cancel()
                    stop_waiter.cancel()
                    await asyncio.gather(
                        consumer,
                        state_sync,
                        stop_waiter,
                        return_exceptions=True,
                    )

    async def _observe_group_cycle(
        self,
        client: aiomqtt.Client,
        results: tuple[FreshDeviceState, ...],
    ) -> None:
        group_service = self._group_service
        if group_service is None:
            return
        try:
            await group_service.observe_fresh_cycle(results)
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._mark_group_outputs_unavailable(client)
            await client.publish(
                self._group_status,
                _canonical_json(
                    {
                        "schema_version": 1,
                        "status": "group_lifecycle_unavailable",
                        "diagnostic_reason": "group_cycle_callback_failed",
                        "physical_identity_keys_exposed": False,
                        "private_paths_exposed": False,
                    }
                ),
                qos=1,
                retain=True,
            )
            return
        await self._publish_group_status(client)
        await self._publish_group_outputs(client)

    async def _publish_group_status(self, client: aiomqtt.Client) -> None:
        group_service = self._group_service
        if group_service is None:
            return
        await client.publish(
            self._group_status,
            _canonical_json(group_service.public_dict()),
            qos=1,
            retain=True,
        )

    async def _publish_group_outputs(self, client: aiomqtt.Client) -> None:
        if not (
            self._projection.group_entities
            or self._projection.group_control_entities
            or self._projection.group_resume_entities
        ):
            return
        group_service = self._group_service
        if group_service is None:
            await self._mark_group_outputs_unavailable(client)
            return
        outputs = group_service.group_output_states()
        by_group = {output.group_id: output for output in outputs}
        expected = {entity.group_id for entity in self._projection.group_entities}
        expected_controls = {
            entity.group_id for entity in self._projection.group_control_entities
        }
        expected_resume_groups = {
            entity.group_id for entity in self._projection.group_resume_entities
        }
        qualified_outputs = {
            output.group_id for output in outputs if output.command_qualified
        }
        router_consistent = (
            self._group_router is None or self._group_router.inventory_consistent
        )
        if (
            len(by_group) != len(outputs)
            or set(by_group) != expected
            or expected_controls != qualified_outputs
            or not expected_resume_groups <= set(by_group)
            or not router_consistent
        ):
            await self._mark_group_outputs_unavailable(client)
            await client.publish(
                self._group_status,
                _canonical_json(
                    {
                        "schema_version": 1,
                        "status": "group_lifecycle_unavailable",
                        "diagnostic_reason": "group_output_inventory_mismatch",
                        "physical_identity_keys_exposed": False,
                        "private_paths_exposed": False,
                    }
                ),
                qos=1,
                retain=True,
            )
            return
        for entity in self._projection.group_control_entities:
            output = by_group[entity.group_id]
            if output.actual_output in {GroupActualOutput.ON, GroupActualOutput.OFF}:
                await client.publish(
                    entity.state_topic,
                    "ON" if output.actual_output is GroupActualOutput.ON else "OFF",
                    qos=1,
                    retain=True,
                )
            await client.publish(
                entity.availability_topic,
                "online" if output.command_available else "offline",
                qos=1,
                retain=True,
            )
        for entity in self._projection.group_resume_entities:
            output = by_group[entity.group_id]
            await client.publish(
                entity.availability_topic,
                "online" if output.resume_available else "offline",
                qos=1,
                retain=True,
            )
        for entity in self._projection.group_entities:
            output = by_group[entity.group_id]
            await client.publish(
                entity.state_topic,
                output.actual_output.value,
                qos=1,
                retain=True,
            )
            await client.publish(
                entity.attributes_topic,
                _canonical_json(output.attributes_dict()),
                qos=1,
                retain=True,
            )
            await client.publish(
                entity.availability_topic,
                "online" if output.available else "offline",
                qos=1,
                retain=True,
            )

    async def _mark_group_outputs_unavailable(self, client: aiomqtt.Client) -> None:
        for entity in (
            *self._projection.group_control_entities,
            *self._projection.group_resume_entities,
            *self._projection.group_entities,
        ):
            await client.publish(
                entity.availability_topic,
                "offline",
                qos=1,
                retain=True,
            )

    async def _consume(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            if bool(message.retain):
                await self._publish_rejection(client, "retained_command_rejected")
                continue
            if bool(getattr(message, "dup", False)):
                await self._publish_rejection(client, "duplicate_command_rejected")
                continue
            topic = str(message.topic)
            if self._group_router is not None and self._group_router.owns(topic):
                try:
                    group_routed = await self._group_router.handle(topic, message.payload)
                except LocalControlGroupCommandError as error:
                    await self._publish_rejection(client, error.diagnostic_reason)
                    await self._publish_group_status(client)
                    continue
                except Exception as error:
                    await self._publish_rejection(
                        client,
                        f"command_rejected_{type(error).__name__}",
                    )
                    await self._publish_group_status(client)
                    continue
                await client.publish(
                    f"{self._projection.topic_prefix}/local/requests/"
                    f"{group_routed.request_id}/result",
                    _canonical_json(group_routed.public_result()),
                    qos=1,
                    retain=False,
                )
                # Switch truth remains fresh-device-derived; a command result is never mirrored.
                await self._publish_group_status(client)
                continue
            try:
                routed = await self._router.handle(topic, message.payload)
            except LocalControlCommandConflictError as error:
                await self._publish_rejection(client, error.diagnostic_reason)
                await self._publish_group_status(client)
                continue
            except Exception as error:
                await self._publish_rejection(
                    client,
                    f"command_rejected_{type(error).__name__}",
                )
                await self._publish_group_status(client)
                continue
            await client.publish(
                f"{self._projection.topic_prefix}/local/requests/"
                f"{routed.request_id}/result",
                _canonical_json(routed.public_result()),
                qos=1,
                retain=False,
            )
            if isinstance(routed, RoutedNativeResult):
                await self._publish_group_status(client)
                continue
            if isinstance(routed, RoutedSchedulePreview):
                continue
            if (
                routed.result.accepted
                and routed.state_topic is not None
                and routed.state_value is not None
            ):
                await client.publish(
                    routed.state_topic,
                    routed.state_value,
                    qos=1,
                    retain=True,
                )
            if routed.ownership is not None:
                await self._publish_group_status(client)

    async def _publish_fresh_state(
        self,
        client: aiomqtt.Client,
        result: FreshDeviceState,
    ) -> None:
        availability = self._projection.device_availability_topic(result.logical_member)
        if not result.available:
            await client.publish(availability, "offline", qos=1, retain=True)
            await client.publish(
                self._status,
                _canonical_json(
                    {
                        "status": "state_unavailable",
                        "logical_member": result.logical_member,
                        "reason": result.diagnostic_reason,
                    }
                ),
                qos=1,
                retain=False,
            )
            return
        for publication in result.publications:
            payload = publication.payload
            preview_entity = next((
                entity for entity in self._projection.entities
                if entity.state_topic == publication.topic
                and entity.preview_request_topic is not None
            ), None)
            if preview_entity is not None:
                payload = _canonical_json({
                    **json.loads(payload),
                    "preview_request_topic": preview_entity.preview_request_topic,
                })
            if (
                result.logical_member in self._projection.native_wavemaker_members
                and publication.topic.endswith("/functions/schedule/state")
            ):
                payload = _canonical_json({
                    **json.loads(payload),
                    "native_wavemaker_api": self._projection.native_wavemaker_api,
                })
            schedule_entity = next(
                (
                    entity
                    for entity in self._projection.entities
                    if entity.state_topic == publication.topic
                    and entity.schedule_request_topic is not None
                ),
                None,
            )
            if schedule_entity is not None:
                payload = _canonical_json(
                    {
                        **json.loads(payload),
                        "schedule_request_topic": schedule_entity.schedule_request_topic,
                    }
                )
            await client.publish(
                publication.topic,
                payload,
                qos=1,
                retain=True,
            )
        await client.publish(availability, "online", qos=1, retain=True)

    async def _publish_rejection(self, client: aiomqtt.Client, reason: str) -> None:
        await client.publish(
            self._status,
            _canonical_json({"status": "rejected", "reason": reason}),
            qos=1,
            retain=False,
        )


def _parse_bounded_json(payload: bytes | str) -> object:
    try:
        text = payload.decode("ascii") if isinstance(payload, bytes) else payload
    except UnicodeDecodeError as error:
        raise LocalControlMqttError("schedule request must be ASCII JSON") from error
    if not text.isascii() or len(text) > 32768:
        raise LocalControlMqttError("schedule request must be bounded ASCII JSON")

    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise LocalControlMqttError("schedule request contains duplicate JSON fields")
            result[key] = value
        return result

    try:
        return json.loads(text, object_pairs_hook=unique_fields)
    except (ValueError, RecursionError) as error:
        raise LocalControlMqttError("schedule request must be valid JSON") from error


def _parse_native_request(payload: bytes | str) -> dict:
    request = _parse_bounded_json(payload)
    if not isinstance(request, dict) or not {"request_id", "operation"} <= request.keys():
        raise LocalControlMqttError("native request requires operation and request_id")
    operation = request["operation"]
    required = {"request_id", "operation"}
    optional = set()
    if operation in ("preview", "attach"):
        required.add("plan")
        if operation == "attach":
            optional = {"await_operator_observation", "master_cycle_on_async_failure"}
    elif operation == "detach":
        required.add("attachment_id")
    elif operation in ("status", "recover", "assist_detach", "cycle_master"):
        required.add("record_id")
    else:
        raise LocalControlMqttError("unsupported native operation")
    if not required <= request.keys() or request.keys() - required - optional:
        raise LocalControlMqttError("native operation has missing or unexpected fields")
    for field in ("request_id", "attachment_id", "record_id"):
        if field in request:
            try:
                LocalControlIntent.validate_request_id(request[field])
            except (TypeError, ValueError) as error:
                raise LocalControlMqttError("native ids must be bounded ASCII tokens") from error
    if any(type(request[field]) is not bool for field in optional if field in request):
        raise LocalControlMqttError("native options must be booleans")
    return request


def _parse_wavemaker_request(
    target: LocalDeviceBinding,
    payload: bytes | str,
) -> tuple[str, LocalControlIntent, WavemakerSchedule]:
    """Validate the whole message before ownership changes or any device I/O."""

    envelope = _parse_bounded_json(payload)
    if not isinstance(envelope, dict) or set(envelope) != {"operation", "request_id", "schedule"}:
        raise LocalControlMqttError("schedule request requires operation, request_id and schedule")
    operation = envelope["operation"]
    if operation not in ("preview", "apply"):
        raise LocalControlMqttError("schedule operation must be preview or apply")
    try:
        schedule = WavemakerSchedule.model_validate(envelope["schedule"])
        schedule.compile_image()
        intent = LocalControlIntent(
            request_id=envelope["request_id"],
            target=target,
            function="schedule",
            value=schedule.model_dump(mode="json"),
        )
    except ValueError as error:
        raise LocalControlMqttError(
            "schedule request violates the declared schedule contract"
        ) from error
    if schedule.product_key != target.product_key:
        raise LocalControlMqttError("schedule family does not match the bound device")
    return operation, intent, schedule


def _parse_entity_value(
    entity: HomeAssistantEntityDescriptor,
    payload: bytes | str,
):
    try:
        text = payload.decode("ascii") if isinstance(payload, bytes) else payload
    except UnicodeDecodeError as error:
        raise LocalControlMqttError("MQTT command payload must be ASCII") from error
    if not text.isascii() or len(text) > 128:
        raise LocalControlMqttError("MQTT command payload must be bounded ASCII")
    if entity.platform is HomeAssistantPlatform.SWITCH:
        normalized = text.strip().upper()
        if normalized not in {"ON", "OFF"}:
            raise LocalControlMqttError("switch command must be ON or OFF")
        return normalized == "ON"
    if entity.platform is HomeAssistantPlatform.NUMBER:
        stripped = text.strip()
        if not stripped or any(character not in "-0123456789" for character in stripped):
            raise LocalControlMqttError("number command must be an integer")
        value = int(stripped)
        if entity.native_minimum is not None and value < entity.native_minimum:
            raise LocalControlMqttError("number command is below the native range")
        if entity.native_maximum is not None and value > entity.native_maximum:
            raise LocalControlMqttError("number command is above the native range")
        return value
    if entity.platform is HomeAssistantPlatform.SELECT:
        if text not in entity.options:
            raise LocalControlMqttError("select command is not a published option")
        return text
    if entity.platform is HomeAssistantPlatform.BUTTON:
        if text.strip().upper() != "PRESS":
            raise LocalControlMqttError("button command must be PRESS")
        return None
    raise LocalControlMqttError("diagnostic entities do not accept commands")


def _parse_group_switch_value(payload: bytes | str) -> bool:
    try:
        text = payload.decode("ascii") if isinstance(payload, bytes) else payload
    except UnicodeDecodeError as error:
        raise LocalControlGroupCommandError("group_command_payload_invalid") from error
    if not isinstance(text, str) or not text.isascii() or len(text) > 128:
        raise LocalControlGroupCommandError("group_command_payload_invalid")
    normalized = text.strip().upper()
    if normalized not in {"ON", "OFF"}:
        raise LocalControlGroupCommandError("group_command_payload_invalid")
    return normalized == "ON"


def _parse_group_resume_value(payload: bytes | str) -> None:
    try:
        text = payload.decode("ascii") if isinstance(payload, bytes) else payload
    except UnicodeDecodeError as error:
        raise LocalControlGroupCommandError("group_command_payload_invalid") from error
    if not isinstance(text, str) or not text.isascii() or len(text) > 128:
        raise LocalControlGroupCommandError("group_command_payload_invalid")
    if text.strip().upper() != "PRESS":
        raise LocalControlGroupCommandError("group_command_payload_invalid")


def _state_value(entity: HomeAssistantEntityDescriptor, value) -> str | None:
    if entity.platform is HomeAssistantPlatform.SWITCH:
        return "ON" if value else "OFF"
    if entity.platform is HomeAssistantPlatform.NUMBER:
        return str(value)
    if entity.platform is HomeAssistantPlatform.SELECT:
        return str(value)
    return None


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


__all__ = [
    "DirectControlOwnershipService",
    "GroupCommandService",
    "LocalControlCommandConflictError",
    "LocalControlGroupCommandError",
    "LocalControlGroupCommandRouter",
    "LocalControlMqttAdapter",
    "LocalControlMqttError",
    "LocalControlMqttRouter",
    "RoutedGroupCommandResult",
    "RoutedCommandResult",
]
