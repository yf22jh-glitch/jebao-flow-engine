"""Group status and diagnostic sensors."""

from __future__ import annotations

import math
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

_SCHEDULE_SLOT_CAPACITY = 48


def _is_schedule_time(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 5 or value[2] != ":":
        return False
    hour, minute = value[:2], value[3:]
    if not hour.isdigit() or not minute.isdigit():
        return False
    if value == "24:00":
        return True
    return int(hour) < 24 and int(minute) < 60


def _schedule_scalar(
    value: object,
) -> tuple[bool, bool | int | float | str | None]:
    if value is None or isinstance(value, bool | int):
        return True, value
    if isinstance(value, float):
        return (True, value) if math.isfinite(value) else (False, None)
    if isinstance(value, str):
        return True, value[:80]
    return False, None


def _schedule_entries(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []

    entries: list[dict[str, Any]] = []
    for item in value[:_SCHEDULE_SLOT_CAPACITY]:
        if not isinstance(item, dict):
            continue
        slot = item.get("slot")
        start = item.get("start")
        end = item.get("end")
        mode = item.get("mode")
        mode_code = item.get("mode_code")
        if (
            isinstance(slot, bool)
            or not isinstance(slot, int)
            or not 0 <= slot < _SCHEDULE_SLOT_CAPACITY
            or not _is_schedule_time(start)
            or not _is_schedule_time(end)
            or not isinstance(mode, str)
            or not mode.strip()
            or isinstance(mode_code, bool)
            or not isinstance(mode_code, int)
            or not 0 <= mode_code <= 255
        ):
            continue

        parameters = item.get("parameters")
        safe_parameters: dict[str, bool | int | float | str | None] = {}
        if isinstance(parameters, dict):
            for key, parameter in list(parameters.items())[:16]:
                if not isinstance(key, str):
                    continue
                normalized_key = key.strip()[:64]
                if (
                    not normalized_key
                    or "hex" in normalized_key.lower()
                    or normalized_key.lower().startswith("raw")
                ):
                    continue
                is_safe, safe_parameter = _schedule_scalar(parameter)
                if is_safe:
                    safe_parameters[normalized_key] = safe_parameter

        entries.append(
            {
                "slot": slot,
                "start": start,
                "end": end,
                "mode": mode.strip()[:64],
                "mode_code": mode_code,
                "parameters": safe_parameters,
            }
        )
    return entries


def _invalid_schedule_slots(value: object) -> list[int]:
    if not isinstance(value, list):
        return []
    return [
        slot
        for slot in value[:_SCHEDULE_SLOT_CAPACITY]
        if not isinstance(slot, bool)
        and isinstance(slot, int)
        and 0 <= slot < _SCHEDULE_SLOT_CAPACITY
    ]


def _schedule_slot_capacity(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value == _SCHEDULE_SLOT_CAPACITY else None


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
        )
        + (
            [JebaoFlowScheduleSensor(runtime, device)]
            if "schedule" in device.get("observables", ())
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


class JebaoFlowScheduleSensor(JebaoFlowDeviceEntity, SensorEntity):
    """Expose the decoded device schedule without its fast-changing local clock."""

    _attr_name = "장비 시간표"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, runtime, device: dict[str, Any]) -> None:
        super().__init__(runtime, device, "schedule")

    @property
    def available(self) -> bool:
        return super().available and self._schedule_payload is not None

    @property
    def native_value(self) -> int | None:
        schedule = self._schedule_payload
        if schedule is None:
            return None
        return len(_schedule_entries(schedule.get("entries")))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attributes = super().extra_state_attributes
        schedule = self._schedule_payload
        if schedule is None:
            return attributes

        enabled = schedule.get("enabled")
        attributes.update(
            {
                "enabled": enabled if isinstance(enabled, bool) else None,
                "slot_capacity": _schedule_slot_capacity(
                    schedule.get("slot_capacity")
                ),
                "entries": _schedule_entries(schedule.get("entries")),
                "invalid_slots": _invalid_schedule_slots(
                    schedule.get("invalid_slots")
                ),
            }
        )
        return attributes

    @property
    def _schedule_payload(self) -> dict[str, Any] | None:
        if self.state_payload is None:
            return None
        schedule = self.state_payload.get("schedule")
        return schedule if isinstance(schedule, dict) else None
