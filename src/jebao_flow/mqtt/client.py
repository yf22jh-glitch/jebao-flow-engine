"""Resilient MQTT server adapter owned by jebao-flowd."""

from __future__ import annotations

import asyncio
import json
import logging

import aiomqtt
from pydantic import ValidationError

from jebao_flow.config import MqttConfig
from jebao_flow.mqtt.models import DeviceCommand, GroupCommand
from jebao_flow.mqtt.service import GroupControlService
from jebao_flow.mqtt.topics import MqttTopics

_LOGGER = logging.getLogger(__name__)


class MqttAdapter:
    def __init__(self, config: MqttConfig, service: GroupControlService) -> None:
        self._config = config
        self._service = service
        self.topics = MqttTopics(config.topic_prefix)

    async def run(self, stop_event: asyncio.Event) -> None:
        backoff = 1.0
        while not stop_event.is_set():
            try:
                await self._run_connected(stop_event)
                backoff = 1.0
            except aiomqtt.MqttError as error:
                _LOGGER.warning("mqtt_connection_failed", extra={"error": str(error)})
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, 30)

    async def _run_connected(self, stop_event: asyncio.Event) -> None:
        password = self._config.resolve_password()
        will = aiomqtt.Will(self.topics.availability, "offline", qos=1, retain=True)
        async with aiomqtt.Client(
            self._config.host,
            self._config.port,
            username=self._config.username,
            password=password,
            identifier=f"jebao-flowd-{self._service.system_config.instance_id}",
            will=will,
        ) as client:
            await client.subscribe(self.topics.group_command_wildcard, qos=1)
            await client.subscribe(self.topics.device_command_wildcard, qos=1)
            await client.publish(self.topics.availability, "online", qos=1, retain=True)
            await client.publish(
                self.topics.system_config,
                self._service.system_config.model_dump_json(),
                qos=1,
                retain=True,
            )
            for snapshot in self._service.snapshots():
                await client.publish(
                    self.topics.group_state(snapshot.group_id),
                    snapshot.model_dump_json(),
                    qos=1,
                    retain=True,
                )
            for snapshot in self._service.device_snapshots():
                await client.publish(
                    self.topics.device_state(snapshot.device_id),
                    snapshot.model_dump_json(),
                    qos=1,
                    retain=True,
                )

            consumer = asyncio.create_task(self._consume(client), name="mqtt-command-consumer")
            stop_waiter = asyncio.create_task(stop_event.wait(), name="mqtt-stop-waiter")
            try:
                done, _ = await asyncio.wait(
                    {consumer, stop_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if consumer in done:
                    await consumer
                else:
                    await client.publish(
                        self.topics.availability,
                        "offline",
                        qos=1,
                        retain=True,
                    )
            finally:
                consumer.cancel()
                stop_waiter.cancel()
                await asyncio.gather(consumer, stop_waiter, return_exceptions=True)

    async def _consume(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            topic = str(message.topic)
            group_id = self.topics.parse_group_command(topic)
            device_id = self.topics.parse_device_command(topic)
            if group_id is None and device_id is None:
                continue
            payload = (
                message.payload.encode()
                if isinstance(message.payload, str)
                else bytes(message.payload)
            )
            if device_id is not None:
                await self._consume_device(client, device_id, payload)
                continue
            try:
                command = GroupCommand.model_validate_json(payload)
            except (TypeError, ValidationError, ValueError) as error:
                _LOGGER.warning(
                    "mqtt_command_rejected",
                    extra={"group_id": group_id, "error": str(error)},
                )
                await client.publish(
                    self.topics.system_status,
                    json.dumps({"status": "invalid_command", "group_id": group_id}),
                    qos=1,
                )
                continue

            result = self._service.apply(group_id, command)
            await client.publish(
                self.topics.request_result(command.request_id),
                result.model_dump_json(),
                qos=1,
            )
            if result.accepted:
                state = self._service.snapshot(group_id)
                await client.publish(
                    self.topics.group_state(group_id),
                    state.model_dump_json(),
                    qos=1,
                    retain=True,
                )
                for device_id in state.members:
                    device_state = self._service.device_snapshot(device_id)
                    await client.publish(
                        self.topics.device_state(device_id),
                        device_state.model_dump_json(),
                        qos=1,
                        retain=True,
                    )

    async def _consume_device(
        self,
        client: aiomqtt.Client,
        device_id: str,
        payload: bytes,
    ) -> None:
        try:
            command = DeviceCommand.model_validate_json(payload)
        except (TypeError, ValidationError, ValueError) as error:
            _LOGGER.warning(
                "mqtt_device_command_rejected",
                extra={"device_id": device_id, "error": str(error)},
            )
            await client.publish(
                self.topics.system_status,
                json.dumps({"status": "invalid_device_command", "device_id": device_id}),
                qos=1,
            )
            return

        result = self._service.apply_device(device_id, command)
        await client.publish(
            self.topics.request_result(command.request_id),
            result.model_dump_json(),
            qos=1,
        )
        if not result.accepted:
            return
        state = self._service.device_snapshot(device_id)
        await client.publish(
            self.topics.device_state(device_id),
            state.model_dump_json(),
            qos=1,
            retain=True,
        )
        for group_id in state.group_ids:
            group_state = self._service.snapshot(group_id)
            await client.publish(
                self.topics.group_state(group_id),
                group_state.model_dump_json(),
                qos=1,
                retain=True,
            )
