"""Home Assistant bridge for a jebao-flowd instance."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CARD_URL, CONF_TOPIC_PREFIX, DOMAIN, PLATFORMS
from .runtime import JebaoFlowRuntime

_FRONTEND_REGISTERED = "frontend_registered"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = JebaoFlowRuntime(
        hass,
        entry.entry_id,
        entry.data[CONF_TOPIC_PREFIX],
    )
    entry.runtime_data = runtime

    domain_data = hass.data.setdefault(DOMAIN, {})
    if not domain_data.get(_FRONTEND_REGISTERED):
        card_path = Path(__file__).parent / "frontend" / "jebao-flow-card.js"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, str(card_path), cache_headers=False)]
        )
        domain_data[_FRONTEND_REGISTERED] = True

    await runtime.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    await entry.runtime_data.async_stop()
    return True
