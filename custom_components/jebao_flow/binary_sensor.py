"""Connectivity and safety indicators for logical groups."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
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
        lambda runtime, group: [
            JebaoFlowAvailabilitySensor(runtime, group),
            JebaoFlowHardwareLockSensor(runtime, group),
        ],
    )
    async_setup_device_entities(
        entry.runtime_data,
        entry,
        async_add_entities,
        lambda runtime, device: [JebaoFlowDeviceAvailabilitySensor(runtime, device)],
    )


class JebaoFlowAvailabilitySensor(JebaoFlowGroupEntity, BinarySensorEntity):
    _attr_name = "연결"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime, group: dict[str, Any]) -> None:
        super().__init__(runtime, group, "availability")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.runtime.online and self.state_payload is not None


class JebaoFlowHardwareLockSensor(JebaoFlowGroupEntity, BinarySensorEntity):
    _attr_name = "하드웨어 쓰기 잠금"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:shield-lock-outline"

    def __init__(self, runtime, group: dict[str, Any]) -> None:
        super().__init__(runtime, group, "hardware_lock")

    @property
    def is_on(self) -> bool | None:
        if self.state_payload is None:
            return None
        return bool(self.state_payload.get("hardware_writes_locked", True))


class JebaoFlowDeviceAvailabilitySensor(JebaoFlowDeviceEntity, BinarySensorEntity):
    _attr_name = "개별 연결"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime, device: dict[str, Any]) -> None:
        super().__init__(runtime, device, "device_availability")

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return bool(
            self.runtime.online
            and self.state_payload is not None
            and self.state_payload.get("online") is True
        )
