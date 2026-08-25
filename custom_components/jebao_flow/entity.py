"""Entity base classes for daemon-owned logical groups."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import (
    ATTR_CONTROL,
    ATTR_DEVICE_ID,
    ATTR_DEVICE_TYPE,
    ATTR_ENTRY_ID,
    ATTR_GROUP_ID,
    ATTR_INSTANCE_ID,
    ATTR_TOPIC_PREFIX,
    DOMAIN,
)
from .runtime import JebaoFlowRuntime


class JebaoFlowGroupEntity(Entity):
    """Base for a control surfaced by the server's group contract."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        runtime: JebaoFlowRuntime,
        group: dict[str, Any],
        control: str,
    ) -> None:
        self.runtime = runtime
        self.group_id = str(group["id"])
        self.group_name = str(group["name"])
        self.control = control
        self._attr_unique_id = f"{runtime.entry_id}_{self.group_id}_{control}"

    @property
    def state_payload(self) -> dict[str, Any] | None:
        return self.runtime.group_states.get(self.group_id)

    @property
    def available(self) -> bool:
        return self.runtime.online and self.state_payload is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.runtime.entry_id}:{self.group_id}")},
            name=self.group_name,
            manufacturer="Jebao Flow Engine",
            model="Logical flow group",
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_INSTANCE_ID: self.runtime.instance_id,
            ATTR_ENTRY_ID: self.runtime.entry_id,
            ATTR_TOPIC_PREFIX: self.runtime.topic_prefix,
            ATTR_GROUP_ID: self.group_id,
            ATTR_CONTROL: self.control,
            "jebao_flow_runtime_mode": self.runtime.runtime_mode,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.runtime.async_add_listener(self.async_write_ha_state)
        )


def async_setup_group_entities(runtime, entry, async_add_entities, factory) -> None:
    """Discover retained daemon groups and add platform entities once."""

    known: set[str] = set()

    def discover() -> None:
        entities = []
        for group in runtime.groups:
            group_id = str(group.get("id", ""))
            if not group_id or group_id in known:
                continue
            discovered = factory(runtime, group)
            if not discovered:
                continue
            known.add(group_id)
            entities.extend(discovered)
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(runtime.async_add_listener(discover))
    discover()


class JebaoFlowDeviceEntity(Entity):
    """Base for physical-device controls routed through jebao-flowd."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        runtime: JebaoFlowRuntime,
        device: dict[str, Any],
        control: str,
    ) -> None:
        self.runtime = runtime
        self.device_id = str(device["id"])
        self.device_name = str(device["name"])
        self.device_type = str(device["type"])
        self.controls = tuple(str(value) for value in device.get("controls", ()))
        self.control = control
        self._attr_unique_id = f"{runtime.entry_id}_{self.device_id}_{control}"

    @property
    def state_payload(self) -> dict[str, Any] | None:
        return self.runtime.device_states.get(self.device_id)

    @property
    def available(self) -> bool:
        return self.runtime.online and self.state_payload is not None

    @property
    def device_info(self) -> DeviceInfo:
        models = {
            "wavemaker": "Wavemaker",
            "return_pump": "Return pump",
            "dosing_pump": "Dosing pump",
        }
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.runtime.entry_id}:device:{self.device_id}")},
            name=self.device_name,
            manufacturer="Jebao",
            model=models.get(self.device_type, "Pump"),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            ATTR_INSTANCE_ID: self.runtime.instance_id,
            ATTR_ENTRY_ID: self.runtime.entry_id,
            ATTR_TOPIC_PREFIX: self.runtime.topic_prefix,
            ATTR_DEVICE_ID: self.device_id,
            ATTR_DEVICE_TYPE: self.device_type,
            ATTR_CONTROL: self.control,
            "jebao_flow_runtime_mode": self.runtime.runtime_mode,
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.runtime.async_add_listener(self.async_write_ha_state)
        )


def async_setup_device_entities(runtime, entry, async_add_entities, factory) -> None:
    """Discover retained physical devices and add supported controls once."""

    known: set[str] = set()

    def discover() -> None:
        entities = []
        for device in runtime.devices:
            device_id = str(device.get("id", ""))
            if not device_id or device_id in known:
                continue
            discovered = factory(runtime, device)
            if not discovered:
                continue
            known.add(device_id)
            entities.extend(discovered)
        if entities:
            async_add_entities(entities)

    entry.async_on_unload(runtime.async_add_listener(discover))
    discover()
