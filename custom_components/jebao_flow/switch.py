"""Group power switches."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
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
        lambda runtime, group: (
            [JebaoFlowGroupSwitch(runtime, group)]
            if not runtime.observer_mode
            and "enabled" in runtime.group_controls(str(group.get("id", "")))
            else []
        ),
    )
    async_setup_device_entities(
        entry.runtime_data,
        entry,
        async_add_entities,
        lambda runtime, device: (
            [JebaoFlowDeviceSwitch(runtime, device)]
            if not runtime.observer_mode and "enabled" in device.get("controls", ())
            else []
        ),
    )


class JebaoFlowGroupSwitch(JebaoFlowGroupEntity, SwitchEntity):
    _attr_name = "운전"
    _attr_icon = "mdi:waves"

    def __init__(self, runtime, group: dict[str, Any]) -> None:
        super().__init__(runtime, group, "enabled")

    @property
    def available(self) -> bool:
        return self.group_control_available("enabled")

    @property
    def is_on(self) -> bool | None:
        if self.state_payload is None:
            return None
        return bool(self.state_payload.get("enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.runtime.async_group_command(self.group_id, enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.runtime.async_group_command(self.group_id, enabled=False)


class JebaoFlowDeviceSwitch(JebaoFlowDeviceEntity, SwitchEntity):
    _attr_name = "개별 운전"
    _attr_icon = "mdi:pump"

    def __init__(self, runtime, device: dict[str, Any]) -> None:
        super().__init__(runtime, device, "device_enabled")

    @property
    def available(self) -> bool:
        return (
            super().available
            and not self.runtime.observer_mode
            and self.advertises_control("enabled")
        )

    @property
    def is_on(self) -> bool | None:
        if self.state_payload is None:
            return None
        return bool(self.state_payload.get("enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.runtime.async_device_command(self.device_id, enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.runtime.async_device_command(self.device_id, enabled=False)
