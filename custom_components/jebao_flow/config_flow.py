"""Config flow for the MQTT-only Home Assistant bridge."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import CONF_TOPIC_PREFIX, DEFAULT_TOPIC_PREFIX, DOMAIN


def _normalize_prefix(value: str) -> str:
    prefix = value.strip().strip("/")
    if not prefix or "+" in prefix or "#" in prefix:
        raise vol.Invalid("MQTT topic prefix must be non-empty and contain no wildcards")
    return prefix


class JebaoFlowConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a jebao-flowd MQTT instance, never a physical pump."""

    VERSION = 1

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                topic_prefix = _normalize_prefix(user_input[CONF_TOPIC_PREFIX])
            except vol.Invalid:
                errors[CONF_TOPIC_PREFIX] = "invalid_topic_prefix"
            else:
                await self.async_set_unique_id(topic_prefix)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input.get("name", "Jebao Flow Engine"),
                    data={CONF_TOPIC_PREFIX: topic_prefix},
                )

        schema = vol.Schema(
            {
                vol.Required("name", default="Jebao Flow Engine"): str,
                vol.Required(
                    CONF_TOPIC_PREFIX,
                    default=DEFAULT_TOPIC_PREFIX,
                ): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )
