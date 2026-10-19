"""UI setup for a VSON SMART WP8621N feeder."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import (
    VsonApiError,
    VsonAuthError,
    VsonDeviceNotFound,
    VsonFeederApi,
    VsonTransportError,
)
from .const import CONF_DEVICE_ID, CONF_MAC, CONF_TOKEN, DOMAIN


def normalize_mac(value: str) -> str:
    """Use the MAC syntax sent by VSON's app without logging the identifier."""
    raw = value.strip().lower().replace("-", ":")
    if re.fullmatch(r"[0-9a-f]{12}", raw):
        raw = ":".join(raw[index : index + 2] for index in range(0, 12, 2))
    if not re.fullmatch(r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", raw):
        raise ValueError("invalid MAC")
    return raw


class VsonFeederConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create one entry per feeder."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mac = normalize_mac(user_input[CONF_MAC])
            except ValueError:
                errors[CONF_MAC] = "invalid_mac"
            else:
                api = VsonFeederApi(async_get_clientsession(self.hass), mac)
                try:
                    await api.status()
                except VsonApiError:
                    errors["base"] = "cannot_connect"
                else:
                    device_id = str(user_input.get(CONF_DEVICE_ID, "")).strip()
                    if device_id:
                        try:
                            await VsonFeederApi(
                                async_get_clientsession(self.hass), mac, device_id
                            ).settings()
                        except VsonApiError:
                            errors[CONF_DEVICE_ID] = "invalid_device_id"
                    if not errors:
                        await self.async_set_unique_id(mac.replace(":", ""))
                        self._abort_if_unique_id_configured()
                        data = {CONF_MAC: mac}
                        if device_id:
                            data[CONF_DEVICE_ID] = device_id
                        return self.async_create_entry(title="VSON Smart Feeder", data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MAC): str,
                    vol.Optional(CONF_DEVICE_ID): str,
                }
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> VsonFeederOptionsFlow:
        return VsonFeederOptionsFlow()


class VsonFeederOptionsFlow(config_entries.OptionsFlow):
    """Let an already configured feeder gain native schedule access."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        return self.async_show_menu(step_id="init", menu_options=["account", "manual_id"])

    async def async_step_account(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            session = async_get_clientsession(self.hass)
            mac = self.config_entry.data[CONF_MAC]
            api = VsonFeederApi(session, mac)
            try:
                device_id, token = await api.discover_from_account(
                    user_input[CONF_USERNAME].strip(), user_input[CONF_PASSWORD]
                )
                await VsonFeederApi(session, mac, device_id, token).settings()
            except VsonTransportError:
                errors["base"] = "cannot_connect"
            except VsonAuthError:
                errors["base"] = "invalid_auth"
            except VsonDeviceNotFound:
                errors["base"] = "device_not_found"
            except VsonApiError:
                errors["base"] = "cannot_read_schedules"
            else:
                # HA stores only the cloud ID and bearer token, never the password.
                return self.async_create_entry(
                    title="", data={CONF_DEVICE_ID: device_id, CONF_TOKEN: token}
                )
        return self.async_show_form(
            step_id="account",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_manual_id(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        existing_id = self.config_entry.options.get(
            CONF_DEVICE_ID, self.config_entry.data.get(CONF_DEVICE_ID, "")
        )
        if user_input is not None:
            device_id = str(user_input.get(CONF_DEVICE_ID, "")).strip()
            token = self.config_entry.options.get(CONF_TOKEN) if device_id == existing_id else None
            if device_id:
                api = VsonFeederApi(
                    async_get_clientsession(self.hass),
                    self.config_entry.data[CONF_MAC],
                    device_id,
                    token,
                )
                try:
                    await api.settings()
                except VsonApiError:
                    return self.async_show_form(
                        step_id="manual_id",
                        data_schema=vol.Schema({vol.Optional(CONF_DEVICE_ID): str}),
                        errors={CONF_DEVICE_ID: "invalid_device_id"},
                    )
            return self.async_create_entry(
                title="", data={CONF_DEVICE_ID: device_id, CONF_TOKEN: token or ""}
            )
        return self.async_show_form(
            step_id="manual_id",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_DEVICE_ID,
                        default=existing_id,
                    ): str
                }
            ),
        )
