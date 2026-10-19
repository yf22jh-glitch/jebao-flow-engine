"""Home Assistant integration for a VSON SMART WP8621N feeder."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import entity_registry as er

from .api import VsonApiError, VsonFeederApi
from .const import CONF_DEVICE_ID, CONF_MAC, CONF_TOKEN, DOMAIN, PLATFORMS
from .coordinator import VsonFeederCoordinator

_SCHEDULE_KEY = "native_schedule"


def _entity_schema(extra: dict[Any, Any]) -> vol.Schema:
    return vol.Schema({vol.Required("entity_id"): str, **extra})


SET_SCHEDULE_SCHEMA = _entity_schema(
    {
        vol.Optional("number"): vol.All(str, vol.Match(r"\d+")),
        vol.Required("open_time"): vol.All(str, vol.Match(r"^(?:[01]\d|2[0-3]):[0-5]\d(?::00)?$")),
        vol.Required("week"): vol.All(str, vol.Match(r"^(?:0|[1-7](?:&[1-7])*)$")),
        vol.Required("frequency"): vol.All(vol.Coerce(int), vol.Range(min=1, max=31)),
        vol.Required("enabled", default=True): bool,
    }
)
DELETE_SCHEDULE_SCHEMA = _entity_schema(
    {vol.Required("number"): vol.All(str, vol.Match(r"\d+"))}
)


def _coordinator_for_entity(hass: HomeAssistant, entity_id: str) -> VsonFeederCoordinator:
    registry_entry = er.async_get(hass).async_get(entity_id)
    if (
        registry_entry is None
        or registry_entry.platform != DOMAIN
        or not registry_entry.unique_id.endswith("_" + _SCHEDULE_KEY)
        or registry_entry.config_entry_id is None
    ):
        raise HomeAssistantError("Select this feeder's Native schedule sensor")
    entry = hass.config_entries.async_get_entry(registry_entry.config_entry_id)
    if entry is None or entry.domain != DOMAIN or entry.runtime_data is None:
        raise HomeAssistantError("Feeder integration is not loaded")
    coordinator: VsonFeederCoordinator = entry.runtime_data
    if not coordinator.api.device_id:
        raise HomeAssistantError("Configure the VSON cloud device ID before editing native schedules")
    return coordinator


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register explicit native-schedule actions once."""

    async def set_schedule(call: ServiceCall) -> None:
        coordinator = _coordinator_for_entity(hass, call.data["entity_id"])
        try:
            await coordinator.api.upsert_schedule(
                number=call.data.get("number"),
                open_time=call.data["open_time"][:5],
                week=call.data["week"],
                frequency=call.data["frequency"],
                enabled=call.data["enabled"],
            )
        except VsonApiError as error:
            raise HomeAssistantError(f"Schedule result unconfirmed; do not resend: {error}") from error
        await coordinator.async_request_refresh()

    async def delete_schedule(call: ServiceCall) -> None:
        coordinator = _coordinator_for_entity(hass, call.data["entity_id"])
        try:
            await coordinator.api.delete_schedule(call.data["number"])
        except VsonApiError as error:
            raise HomeAssistantError(f"Schedule result unconfirmed; do not resend: {error}") from error
        await coordinator.async_request_refresh()

    hass.services.async_register(DOMAIN, "set_schedule", set_schedule, SET_SCHEDULE_SCHEMA)
    hass.services.async_register(DOMAIN, "delete_schedule", delete_schedule, DELETE_SCHEDULE_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start status/history polling for one configured feeder."""
    device_id = entry.options.get(CONF_DEVICE_ID, entry.data.get(CONF_DEVICE_ID)) or None
    token = entry.options.get(CONF_TOKEN) or None
    api = VsonFeederApi(async_get_clientsession(hass), entry.data[CONF_MAC], device_id, token)
    coordinator = VsonFeederCoordinator(hass, api, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
