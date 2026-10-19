"""Last feeding, history and native timetable reported by VSON."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import EntityCategory
from homeassistant.util import dt as dt_util

from .entity import VsonFeederEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            VsonFeederSensor(coordinator, "last_feed"),
            VsonFeederSensor(coordinator, "last_feed_portions"),
            VsonFeederSensor(coordinator, "native_schedule"),
            VsonConnectionSensor(coordinator, "cloud_status"),
        ]
    )


class VsonFeederSensor(VsonFeederEntity, SensorEntity):
    """A diagnostic derived from the cloud's actual response."""

    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self.key = key
        self._attr_name = {
            "last_feed": "Last feed",
            "last_feed_portions": "Last feed portions",
            "native_schedule": "Native schedule",
        }[key]
        self._attr_icon = {
            "last_feed": "mdi:clock-check-outline",
            "last_feed_portions": "mdi:fish-food",
            "native_schedule": "mdi:calendar-clock",
        }[key]
        if key == "last_feed":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self) -> datetime | int | str | None:
        data = self.coordinator.data
        records = data["history"]
        if self.key == "native_schedule":
            schedules = data["schedules"]
            if not self.coordinator.api.device_id:
                return "ID 미등록"
            if schedules is None:
                return "조회 실패"
            return f"{len(schedules)} slots"
        if not records:
            return None
        first = records[0]
        if self.key == "last_feed_portions":
            raw = first.get("frequency")
            return int(raw) if str(raw).isdigit() else None
        raw_time = first.get("time")
        if not isinstance(raw_time, str):
            return None
        try:
            parsed = datetime.strptime(raw_time, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
        zone = dt_util.get_time_zone(self.hass.config.time_zone)
        return parsed.replace(tzinfo=zone)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attributes = super().extra_state_attributes
        if self.key == "last_feed":
            return {**attributes, "recent_records": self.coordinator.data["history"][:10]}
        if self.key == "native_schedule":
            return {
                **attributes,
                "slots": self.coordinator.data["schedules"],
                "error": self.coordinator.schedule_error,
            }
        return attributes


class VsonConnectionSensor(VsonFeederEntity, SensorEntity):
    """Separate cloud communication failures from a reported offline device."""

    _attr_name = "Cloud status"
    _attr_icon = "mdi:cloud-check-outline"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> str:
        return self.coordinator.connection_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"queries": {key: self.coordinator.query_attributes(key) for key in self.coordinator.health},
                "poll_interval_seconds": 30, "stale_after_seconds": 90}
