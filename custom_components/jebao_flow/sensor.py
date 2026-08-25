"""Group status and diagnostic sensors."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
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
        lambda runtime, device: [JebaoFlowDeviceStatusSensor(runtime, device)]
        + (
            [JebaoFlowActualPowerSensor(runtime, device)]
            if "power" in device.get("observables", ())
            else []
        )
        + (
            [JebaoFlowActualModeSensor(runtime, device)]
            if "mode" in device.get("observables", ())
            else []
        ),
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
                "actual_enabled": self.state_payload.get("actual_enabled"),
                "online_member_count": self.state_payload.get("online_member_count", 0),
                "member_count": self.state_payload.get("member_count", 0),
                "last_seen_at": self.state_payload.get("last_seen_at"),
                "last_changed_at": self.state_payload.get("last_changed_at"),
                "last_configuration_changed_at": self.state_payload.get(
                    "last_configuration_changed_at"
                ),
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
                "actual_mode": self.state_payload.get("actual_mode"),
                "actual_frequency": self.state_payload.get("actual_frequency"),
                "online": self.state_payload.get("online"),
                "error": self.state_payload.get("error"),
                "last_seen_at": self.state_payload.get("last_seen_at"),
                "last_changed_at": self.state_payload.get("last_changed_at"),
                "last_configuration_changed_at": self.state_payload.get(
                    "last_configuration_changed_at"
                ),
                "observed_attributes": self.state_payload.get("observed_attributes", {}),
                "observation_source": self.state_payload.get("observation_source"),
                "change_source": self.state_payload.get("change_source"),
            }
        )
        return attributes


class JebaoFlowActualPowerSensor(JebaoFlowDeviceEntity, SensorEntity):
    _attr_name = "실제 출력"
    _attr_icon = "mdi:gauge"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, runtime, device: dict[str, Any]) -> None:
        super().__init__(runtime, device, "actual_power")

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.state_payload is not None
            and self.state_payload.get("online") is True
        )

    @property
    def native_value(self) -> int | None:
        if self.state_payload is None:
            return None
        value = self.state_payload.get("actual_power")
        return int(value) if isinstance(value, int | float) else None


class JebaoFlowActualModeSensor(JebaoFlowDeviceEntity, SensorEntity):
    _attr_name = "실제 모드"
    _attr_icon = "mdi:sine-wave"

    def __init__(self, runtime, device: dict[str, Any]) -> None:
        super().__init__(runtime, device, "actual_mode")

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.state_payload is not None
            and self.state_payload.get("online") is True
        )

    @property
    def native_value(self) -> str | None:
        if self.state_payload is None:
            return None
        value = self.state_payload.get("actual_mode")
        return str(value) if value is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        if self.state_payload is not None:
            attributes["actual_frequency"] = self.state_payload.get("actual_frequency")
        return attributes
