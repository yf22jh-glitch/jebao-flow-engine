"""MQTT command contract for qualified local control and Home Assistant.

Commands are consumed sequentially at MQTT QoS 0, retained or duplicate-marked commands are never
executed, and request ids are created at the local runtime boundary. Endpoint addresses are still
resolved afresh by the coordinator for every operation and never appear in this contract.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from dataclasses import dataclass

import aiomqtt

from jebao_flow.config import MqttConfig
from jebao_flow.local_control.catalog import LocalControlCatalog
from jebao_flow.local_control.coordinator import LocalControlCoordinator, LocalControlResult
from jebao_flow.local_control.ha import (
    HomeAssistantEntityDescriptor,
    HomeAssistantPlatform,
    HomeAssistantProjection,
)
from jebao_flow.local_control.models import (
    FreshInstallManifest,
    LocalControlIntent,
    LocalDeviceBinding,
)
from jebao_flow.local_control.schedule import FreshMasterSchedulePlan
from jebao_flow.local_control.state import FreshDeviceState, FreshStatePoller


class LocalControlMqttError(RuntimeError):
    """A command topic or payload did not match the published local-control contract."""


COMMAND_QOS = 0
_PASSWORD_FROM_CONFIG = object()


@dataclass(frozen=True, slots=True)
class RoutedCommandResult:
    request_id: str
    state_topic: str | None
    state_value: str | None
    result: LocalControlResult

    def public_result(self) -> dict[str, object]:
        return self.result.public_dict()


@dataclass(frozen=True, slots=True)
class _ScalarRoute:
    entity: HomeAssistantEntityDescriptor
    target: LocalDeviceBinding


@dataclass(frozen=True, slots=True)
class _ScheduleRoute:
    entity: HomeAssistantEntityDescriptor
    plan: FreshMasterSchedulePlan
    slave_index: int


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
    ) -> None:
        self._coordinator = coordinator
        self._operation_lock = operation_lock
        device_by_member = {device.logical_member: device for device in manifest.devices}
        self._routes: dict[str, _ScalarRoute | _ScheduleRoute] = {}
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
        return tuple(sorted(self._routes))

    async def handle(self, topic: str, payload: bytes | str) -> RoutedCommandResult:
        if self._operation_lock is None:
            return await self._handle(topic, payload)
        async with self._operation_lock:
            return await self._handle(topic, payload)

    async def _handle(self, topic: str, payload: bytes | str) -> RoutedCommandResult:
        coordinator = self._coordinator
        if coordinator is None:
            raise LocalControlMqttError("read-only MQTT runtime has no command executor")
        try:
            route = self._routes[topic]
        except KeyError as error:
            raise LocalControlMqttError(
                "MQTT topic is not a published qualified control"
            ) from error
        value = _parse_entity_value(route.entity, payload)
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
        return RoutedCommandResult(
            request_id=request_id,
            state_topic=route.entity.state_topic,
            state_value=_state_value(route.entity, value) if result.accepted else None,
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
        self._availability = projection.global_availability_topic
        self._status = f"{projection.topic_prefix}/local/status"

    async def run(self, stop_event: asyncio.Event) -> None:
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
            for topic in self._router.command_topics:
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
                        }
                    ),
                    qos=1,
                    retain=True,
                )
            await client.publish(self._availability, "online", qos=1, retain=True)

            stop_waiter = asyncio.create_task(stop_event.wait())
            consumer = asyncio.create_task(self._consume(client))
            state_sync = asyncio.create_task(
                self._state_poller.run(
                    lambda result: self._publish_fresh_state(client, result),
                    stop_event,
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

    async def _consume(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            if bool(message.retain):
                await self._publish_rejection(client, "retained_command_rejected")
                continue
            if bool(getattr(message, "dup", False)):
                await self._publish_rejection(client, "duplicate_command_rejected")
                continue
            try:
                routed = await self._router.handle(str(message.topic), message.payload)
            except Exception as error:
                await self._publish_rejection(
                    client,
                    f"command_rejected_{type(error).__name__}",
                )
                continue
            await client.publish(
                f"{self._projection.topic_prefix}/local/requests/"
                f"{routed.request_id}/result",
                _canonical_json(routed.public_result()),
                qos=1,
                retain=False,
            )
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
            await client.publish(
                publication.topic,
                publication.payload,
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
    "LocalControlMqttAdapter",
    "LocalControlMqttError",
    "LocalControlMqttRouter",
    "RoutedCommandResult",
]
