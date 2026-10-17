"""HA's native MQTT service is the editor transport; no browser credentials or Docker API."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import signal
from datetime import UTC, datetime

import aiomqtt

from jebao_flow.config import MqttConfig
from jebao_flow.local_control.external_manual_editor import (
    EditorRejected,
    ScheduleEditor,
    decode_request,
)
from jebao_flow.local_control.external_manual_schedule import ZONE


class EditorMqtt:
    def __init__(self, editor: ScheduleEditor, config: MqttConfig) -> None:
        self.editor = editor
        self.config = config
        namespace = hashlib.sha256(config.topic_prefix.encode()).hexdigest()[:24]
        self.unique_id = f"aqua_pro_schedule_{namespace}"
        self.prefix = f"{config.topic_prefix}/pro_manual/editor"
        self.command_topic = f"{self.prefix}/request"
        self.state_topic = f"{self.prefix}/state"
        self.availability_topic = f"{self.prefix}/availability"
        self.queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=32)
        self.changed = asyncio.Event()

    def discovery(self) -> dict:
        return {
            "name": "GMP Schedule Editor",
            "unique_id": self.unique_id,
            "default_entity_id": "sensor.aquarium_gmp_schedule_editor",
            "state_topic": self.state_topic,
            "value_template": "{{ value_json.status }}",
            "json_attributes_topic": self.state_topic,
            "availability_topic": self.availability_topic,
            "expire_after": 60,
            "icon": "mdi:calendar-clock",
            "device": {
                "identifiers": [self.unique_id],
                "name": "Aquarium GMP Scheduler",
                "manufacturer": "Jebao Flow Engine",
                "model": "External manual scheduler",
            },
        }

    async def _publish(self, client) -> None:
        while True:
            self.changed.clear()
            snapshot = self.editor.snapshot(datetime.now(ZONE), self.command_topic)
            await client.publish(self.state_topic, json.dumps(snapshot), qos=0, retain=False)
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=10)
            except TimeoutError:
                pass

    async def connected(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                async with aiomqtt.Client(
                    self.config.host,
                    self.config.port,
                    username=self.config.username,
                    password=self.config.resolve_password(),
                    identifier=self.unique_id,
                    will=aiomqtt.Will(self.availability_topic, "offline", qos=1, retain=True),
                ) as client:
                    await client.publish(self.availability_topic, "offline", qos=1, retain=True)
                    await client.subscribe(self.command_topic, qos=0)
                    await client.publish(
                        f"{self.config.discovery_prefix}/sensor/{self.unique_id}/config",
                        json.dumps(self.discovery()),
                        qos=1,
                        retain=True,
                    )
                    publisher = asyncio.create_task(self._publish(client))
                    receiver = asyncio.create_task(self._receive(client))
                    try:
                        await client.publish(self.availability_topic, "online", qos=1, retain=True)
                        done, _ = await asyncio.wait(
                            (publisher, receiver), return_when=asyncio.FIRST_COMPLETED
                        )
                        for task in done:
                            task.result()
                    finally:
                        publisher.cancel()
                        receiver.cancel()
                        await asyncio.gather(publisher, receiver, return_exceptions=True)
                        with contextlib.suppress(aiomqtt.MqttError):
                            await client.publish(
                                self.availability_topic, "offline", qos=1, retain=True
                            )
            except aiomqtt.MqttError as error:
                print(
                    json.dumps(
                        {"outcome": "editor_mqtt_disconnected", "error_type": type(error).__name__}
                    ),
                    flush=True,
                )
                try:
                    await asyncio.wait_for(stop.wait(), timeout=5)
                except TimeoutError:
                    pass

    async def _receive(self, client) -> None:
        async for message in client.messages:
            if (
                str(message.topic) != self.command_topic
                or message.retain
                or (bool(getattr(message, "dup", False)))
            ):
                continue
            payload = bytes(message.payload)
            if len(payload) > 40 * 1024 or self.queue.full():
                continue
            with contextlib.suppress(EditorRejected):
                self.editor.request_read_pause(payload, datetime.now(UTC))
            self.queue.put_nowait(payload)

    async def consume(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                payload = await asyncio.wait_for(self.queue.get(), timeout=0.5)
            except TimeoutError:
                continue
            try:
                result = await self.editor.handle(payload, datetime.now(UTC))
                print(json.dumps({"outcome": "editor_request", **result}), flush=True)
            except EditorRejected as error:
                print(json.dumps({"outcome": "editor_rejected", "code": error.code}), flush=True)
                with contextlib.suppress(EditorRejected):
                    request = decode_request(payload, datetime.now(UTC), check_age=False)
                    async with self.editor.operation_lock:
                        self.editor._remember(
                            {
                                "request_id": request["request_id"],
                                "operation": request["operation"],
                                "outcome": "error",
                                "code": error.code,
                            }
                        )
            finally:
                self.queue.task_done()
                self.changed.set()


async def run_editor_service(editor: ScheduleEditor, config: MqttConfig) -> None:
    """Caller holds runner flock. SIGTERM drains an in-flight row before releasing it."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    if hasattr(editor.backend, "read_retry"):
        editor.backend.read_retry.stop_requested = stop.is_set
    mqtt = EditorMqtt(editor, config)

    async def drive():
        # Publisher and pause requests remain available during startup read retries.
        await editor.initialize(datetime.now(ZONE))
        while not stop.is_set():
            await editor.tick(datetime.now(ZONE))
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.5)
            except TimeoutError:
                pass

    driver = asyncio.create_task(drive())
    consumer = asyncio.create_task(mqtt.consume(stop))
    connection = asyncio.create_task(mqtt.connected(stop))
    try:
        # A programming/storage failure stops this actor, rather than silently losing one task.
        waiter = asyncio.create_task(stop.wait())
        done, _ = await asyncio.wait(
            (driver, consumer, connection, waiter), return_when=asyncio.FIRST_COMPLETED
        )
        stop.set()
        for task in done:
            if task is not waiter:
                task.result()
    finally:
        stop.set()
        # Never cancel the consumer/driver while the coordinator might be sending a child.
        await asyncio.gather(driver, consumer, return_exceptions=True)
        connection.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await connection
        if not waiter.done():
            waiter.cancel()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)
