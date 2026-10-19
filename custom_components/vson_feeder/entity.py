"""Common entity properties for VSON feeder entities."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import VsonFeederCoordinator


class VsonFeederEntity(CoordinatorEntity[VsonFeederCoordinator]):
    """One entity attached to the feeder config entry."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VsonFeederCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.api.mac.replace(':', '')}_{key}"
        self.source = "history" if key in {"last_feed", "last_feed_portions"} else "schedules" if key == "native_schedule" else "status"

    @property
    def available(self) -> bool:
        if not self.coordinator.last_update_success:
            return False
        if self.source == "schedules" and not self.coordinator.api.device_id:
            return True
        if self.source == "status":
            return self.coordinator.source_recent("status")
        return self.coordinator.health[self.source].last_success is not None

    @property
    def extra_state_attributes(self):
        return self.coordinator.query_attributes(self.source)

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.api.mac.replace(":", ""))},
            name="VSON Smart Feeder",
            manufacturer="VSON",
            model="WP8621N",
            sw_version=str(self.coordinator.data["status"].get("version", "")),
        )
