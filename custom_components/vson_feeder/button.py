"""One-shot VSON SMART feed action."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import VsonApiError
from .entity import VsonFeederEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([VsonFeedNowButton(entry.runtime_data)])


class VsonFeedNowButton(VsonFeederEntity, ButtonEntity):
    """A single cloud feed request, with no automatic retry."""

    _attr_name = "Feed now"
    _attr_icon = "mdi:fish-food"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "feed_now")

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.query_state("status") == "fresh"
            and str(self.coordinator.data["status"].get("equipmentState")) == "0"
        )

    async def async_press(self) -> None:
        if not self.available:
            raise HomeAssistantError("Feeder is not confirmed online")
        try:
            await self.coordinator.api.feed_once()
        except VsonApiError as error:
            raise HomeAssistantError(str(error)) from error
        await self.coordinator.async_request_refresh()
