"""Canonical MQTT topic construction and parsing."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MqttTopics:
    prefix: str

    def __post_init__(self) -> None:
        normalized = self.prefix.strip("/")
        if not normalized or "+" in normalized or "#" in normalized:
            raise ValueError("MQTT topic prefix must be non-empty and contain no wildcards")
        object.__setattr__(self, "prefix", normalized)

    @property
    def availability(self) -> str:
        return f"{self.prefix}/system/availability"

    @property
    def system_config(self) -> str:
        return f"{self.prefix}/system/config"

    @property
    def system_status(self) -> str:
        return f"{self.prefix}/system/status"

    @property
    def group_command_wildcard(self) -> str:
        return f"{self.prefix}/groups/+/command"

    @property
    def device_command_wildcard(self) -> str:
        return f"{self.prefix}/devices/+/command"

    def group_command(self, group_id: str) -> str:
        return f"{self.prefix}/groups/{_segment(group_id)}/command"

    def group_state(self, group_id: str) -> str:
        return f"{self.prefix}/groups/{_segment(group_id)}/state"

    def device_command(self, device_id: str) -> str:
        return f"{self.prefix}/devices/{_segment(device_id)}/command"

    def device_state(self, device_id: str) -> str:
        return f"{self.prefix}/devices/{_segment(device_id)}/state"

    def request_result(self, request_id: str) -> str:
        return f"{self.prefix}/requests/{_segment(request_id)}/result"

    def parse_group_command(self, topic: str) -> str | None:
        base = f"{self.prefix}/groups/"
        if not topic.startswith(base) or not topic.endswith("/command"):
            return None
        group_id = topic[len(base) : -len("/command")]
        if not group_id or "/" in group_id:
            return None
        return group_id

    def parse_device_command(self, topic: str) -> str | None:
        base = f"{self.prefix}/devices/"
        if not topic.startswith(base) or not topic.endswith("/command"):
            return None
        device_id = topic[len(base) : -len("/command")]
        if not device_id or "/" in device_id:
            return None
        return device_id


def _segment(value: str) -> str:
    if not value or "/" in value or "+" in value or "#" in value:
        raise ValueError(f"invalid MQTT topic segment {value!r}")
    return value
