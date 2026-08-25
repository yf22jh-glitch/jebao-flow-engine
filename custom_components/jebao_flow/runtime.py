"""Push runtime backed exclusively by jebao-flowd MQTT topics."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any
from uuid import uuid4

from homeassistant.components import mqtt
from homeassistant.components.mqtt.models import ReceiveMessage
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)


class JebaoFlowRuntime:
    """Live state for one daemon instance.

    This class deliberately has no imports from Jebao/Gizwits protocol modules. Home Assistant
    talks only to the daemon's versioned MQTT contract.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, topic_prefix: str) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.topic_prefix = topic_prefix.strip("/")
        self.online = False
        self.system_config: dict[str, Any] = {}
        self.group_states: dict[str, dict[str, Any]] = {}
        self.device_states: dict[str, dict[str, Any]] = {}
        self.last_results: dict[str, dict[str, Any]] = {}
        self._listeners: set[Callable[[], None]] = set()
        self._unsubscribers: list[Callable[[], None]] = []

    @property
    def instance_id(self) -> str:
        return str(self.system_config.get("instance_id", self.entry_id))

    @property
    def instance_name(self) -> str:
        return str(self.system_config.get("name", "Jebao Flow Engine"))

    @property
    def runtime_mode(self) -> str:
        return str(self.system_config.get("runtime_mode", "observer"))

    @property
    def observer_mode(self) -> bool:
        return self.runtime_mode == "observer"

    @property
    def groups(self) -> tuple[dict[str, Any], ...]:
        groups = self.system_config.get("groups", ())
        return tuple(group for group in groups if isinstance(group, dict))

    @property
    def patterns(self) -> tuple[str, ...]:
        patterns = self.system_config.get("patterns", ())
        return tuple(str(pattern) for pattern in patterns)

    @property
    def devices(self) -> tuple[dict[str, Any], ...]:
        devices = self.system_config.get("devices", ())
        return tuple(device for device in devices if isinstance(device, dict))

    async def async_start(self) -> None:
        subscriptions = (
            (f"{self.topic_prefix}/system/availability", self._receive_availability),
            (f"{self.topic_prefix}/system/config", self._receive_system_config),
            (f"{self.topic_prefix}/groups/+/state", self._receive_group_state),
            (f"{self.topic_prefix}/devices/+/state", self._receive_device_state),
            (f"{self.topic_prefix}/requests/+/result", self._receive_result),
        )
        for topic, handler in subscriptions:
            unsubscribe = await mqtt.async_subscribe(
                self.hass,
                topic,
                handler,
                qos=1,
                encoding="utf-8",
            )
            self._unsubscribers.append(unsubscribe)

    async def async_stop(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)

        @callback
        def remove_listener() -> None:
            self._listeners.discard(listener)

        return remove_listener

    async def async_group_command(self, group_id: str, **changes: Any) -> str:
        if self.observer_mode:
            raise HomeAssistantError("jebao-flowd is in read-only observer mode")
        if not self.online:
            raise HomeAssistantError("jebao-flowd is offline; command was not sent")
        request_id = uuid4().hex
        payload = {
            "request_id": request_id,
            "source": "home_assistant",
            **changes,
        }
        await mqtt.async_publish(
            self.hass,
            f"{self.topic_prefix}/groups/{group_id}/command",
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        return request_id

    async def async_device_command(self, device_id: str, **changes: Any) -> str:
        if self.observer_mode:
            raise HomeAssistantError("jebao-flowd is in read-only observer mode")
        if not self.online:
            raise HomeAssistantError("jebao-flowd is offline; command was not sent")
        request_id = uuid4().hex
        payload = {
            "request_id": request_id,
            "source": "home_assistant",
            **changes,
        }
        await mqtt.async_publish(
            self.hass,
            f"{self.topic_prefix}/devices/{device_id}/command",
            json.dumps(payload, separators=(",", ":")),
            qos=1,
            retain=False,
        )
        return request_id

    @callback
    def _receive_availability(self, message: ReceiveMessage) -> None:
        self.online = str(message.payload).strip().lower() == "online"
        self._notify()

    @callback
    def _receive_system_config(self, message: ReceiveMessage) -> None:
        payload = self._decode_object(message.payload, "system config")
        if payload is None or payload.get("schema_version") != 1:
            return
        if not isinstance(payload.get("groups"), list):
            _LOGGER.warning("Ignoring daemon config without a groups list")
            return
        if not isinstance(payload.get("devices"), list):
            _LOGGER.warning("Ignoring daemon config without a devices list")
            return
        if not isinstance(payload.get("patterns"), list):
            _LOGGER.warning("Ignoring daemon config without a patterns list")
            return
        if payload.get("runtime_mode", "observer") not in {"observer", "control"}:
            _LOGGER.warning("Ignoring daemon config with an invalid runtime mode")
            return
        self.system_config = payload
        self._notify()

    @callback
    def _receive_group_state(self, message: ReceiveMessage) -> None:
        payload = self._decode_object(message.payload, "group state")
        if payload is None or payload.get("schema_version") != 1:
            return
        group_id = self._parse_segment(str(message.topic), "groups", "state")
        if group_id is None or payload.get("group_id") != group_id:
            _LOGGER.warning("Ignoring group state with a mismatched topic and group_id")
            return
        self.group_states[group_id] = payload
        self._notify()

    @callback
    def _receive_device_state(self, message: ReceiveMessage) -> None:
        payload = self._decode_object(message.payload, "device state")
        if payload is None or payload.get("schema_version") != 1:
            return
        device_id = self._parse_segment(str(message.topic), "devices", "state")
        if device_id is None or payload.get("device_id") != device_id:
            _LOGGER.warning("Ignoring device state with a mismatched topic and device_id")
            return
        self.device_states[device_id] = payload
        self._notify()

    @callback
    def _receive_result(self, message: ReceiveMessage) -> None:
        payload = self._decode_object(message.payload, "command result")
        if payload is None or payload.get("schema_version") != 1:
            return
        request_id = self._parse_segment(str(message.topic), "requests", "result")
        if request_id is None or payload.get("request_id") != request_id:
            return
        self.last_results[request_id] = payload
        if len(self.last_results) > 128:
            del self.last_results[next(iter(self.last_results))]
        self._notify()

    def _parse_segment(self, topic: str, collection: str, suffix: str) -> str | None:
        base = f"{self.topic_prefix}/{collection}/"
        ending = f"/{suffix}"
        if not topic.startswith(base) or not topic.endswith(ending):
            return None
        segment = topic[len(base) : -len(ending)]
        if not segment or "/" in segment:
            return None
        return segment

    @staticmethod
    def _decode_object(payload: str | bytes, label: str) -> dict[str, Any] | None:
        try:
            decoded = json.loads(payload)
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            _LOGGER.warning("Ignoring invalid JSON %s payload", label)
            return None
        if not isinstance(decoded, dict):
            _LOGGER.warning("Ignoring non-object %s payload", label)
            return None
        return decoded

    @callback
    def _notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()
