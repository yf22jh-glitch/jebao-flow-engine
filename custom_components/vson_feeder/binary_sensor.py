"""Online and food-level signals reported by the VSON cloud."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import VsonFeederEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            VsonFeederBinarySensor(coordinator, "online"),
            VsonFeederBinarySensor(coordinator, "food_low"),
            VsonFeederBinarySensor(coordinator, "food_reminder"),
        ]
    )


class VsonFeederBinarySensor(VsonFeederEntity, BinarySensorEntity):
    """Report only values returned by the actual WP8621N status endpoint."""

    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self.key = key
        self._attr_name = {
            "online": "Online",
            "food_low": "Food low",
            "food_reminder": "Food reminder",
        }[key]
        self._attr_icon = {
            "online": "mdi:wifi",
            "food_low": "mdi:food-off",
            "food_reminder": "mdi:bell-alert",
        }[key]
        if key == "online":
            self._attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
        elif key == "food_low":
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self) -> bool | None:
        status = self.coordinator.data["status"]
        field = {
            "online": "equipmentState",
            "food_low": "isMaterial",
            "food_reminder": "isMaterialRemind",
        }[self.key]
        value = str(status.get(field, ""))
        if value not in {"0", "1"}:
            return None
        return value == ("0" if self.key == "online" else "1")
