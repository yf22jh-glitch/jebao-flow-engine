"""Group status and diagnostic sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import (
    JebaoFlowDeviceEntity,
    JebaoFlowGroupEntity,
    async_setup_device_entities,
    async_setup_group_entities,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_setup_group_entities(
        entry.runtime_data,
        entry,
        async_add_entities,
        lambda runtime, group: [JebaoFlowStatusSensor(runtime, group)],
    )
    async_setup_device_entities(
        entry.runtime_data,
        entry,
        async_add_entities,
        lambda runtime, device: [JebaoFlowDeviceStatusSensor(runtime, device)],
    )


class JebaoFlowStatusSensor(JebaoFlowGroupEntity, SensorEntity):
    _attr_name = "상태"
    _attr_icon = "mdi:waves-arrow-right"

    def __init__(self, runtime, group: dict[str, Any]) -> None:
        super().__init__(runtime, group, "status")

    @property
    def native_value(self) -> str | None:
        if self.state_payload is None:
            return None
        value = self.state_payload.get("status")
        return str(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        if self.state_payload is None:
            return attributes
        attributes.update(
            {
                "revision": self.state_payload.get("revision"),
                "hardware_writes_locked": self.state_payload.get(
                    "hardware_writes_locked",
                    True,
                ),
                "members": self.state_payload.get("members", {}),
                "last_request_id": self.state_payload.get("last_request_id"),
            }
        )
        return attributes


class JebaoFlowDeviceStatusSensor(JebaoFlowDeviceEntity, SensorEntity):
    _attr_name = "개별 상태"
    _attr_icon = "mdi:list-status"

    def __init__(self, runtime, device: dict[str, Any]) -> None:
        super().__init__(runtime, device, "device_status")

    @property
    def native_value(self) -> str | None:
        if self.state_payload is None:
            return None
        value = self.state_payload.get("status")
        return str(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        if self.state_payload is None:
            return attributes
        attributes.update(
            {
                "control_mode": self.state_payload.get("control_mode"),
                "group_ids": self.state_payload.get("group_ids", []),
                "hardware_writes_locked": self.state_payload.get(
                    "hardware_writes_locked",
                    True,
                ),
                "actual_enabled": self.state_payload.get("actual_enabled"),
                "actual_power": self.state_payload.get("actual_power"),
            }
        )
        return attributes
