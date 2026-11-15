"""Config flow for Dreame Camera."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_AI_CONFIRM_MODEL,
    CONF_AI_MODEL,
    CONF_AI_URL,
    DEFAULT_AI_CONFIRM_MODEL,
    DEFAULT_AI_MODEL,
    DEFAULT_AI_URL,
    CONF_AUDIO,
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_BRIDGE_TOKEN,
    CONF_MAX_MINUTES,
    CONF_PIN,
    CONF_VACUUM_ENTRY,
    DEFAULT_BRIDGE_HOST,
    DEFAULT_BRIDGE_PORT,
    DEFAULT_MAX_MINUTES,
    DOMAIN,
    DREAME_VACUUM_DOMAIN,
)

PIN_SELECTOR = selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))


class DreameCameraConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        vacuums = {e.entry_id: e.title for e in self.hass.config_entries.async_entries(DREAME_VACUUM_DOMAIN)}
        if not vacuums:
            return self.async_abort(reason="no_dreame_vacuum")
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = str(user_input[CONF_PIN]).strip()
            if not (pin.isdigit() and len(pin) == 4):
                errors[CONF_PIN] = "invalid_pin"
            elif len(user_input[CONF_BRIDGE_TOKEN]) < 16:
                errors[CONF_BRIDGE_TOKEN] = "short_token"
            else:
                await self.async_set_unique_id(user_input[CONF_VACUUM_ENTRY])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=vacuums[user_input[CONF_VACUUM_ENTRY]],
                    data={**user_input, CONF_PIN: pin},
                )
        schema = vol.Schema({
            vol.Required(CONF_VACUUM_ENTRY): selector.SelectSelector(selector.SelectSelectorConfig(
                options=[selector.SelectOptionDict(value=k, label=v) for k, v in vacuums.items()])),
            vol.Required(CONF_PIN): PIN_SELECTOR,
            vol.Required(CONF_BRIDGE_TOKEN): PIN_SELECTOR,
            vol.Required(CONF_BRIDGE_HOST, default=DEFAULT_BRIDGE_HOST): str,
            vol.Required(CONF_BRIDGE_PORT, default=DEFAULT_BRIDGE_PORT): int,
            vol.Required(CONF_MAX_MINUTES, default=DEFAULT_MAX_MINUTES): vol.All(int, vol.Range(min=1, max=60)),
            vol.Required(CONF_AUDIO, default=True): bool,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DreameCameraOptionsFlow()


class DreameCameraOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        schema = vol.Schema({
            vol.Required(CONF_MAX_MINUTES, default=current.get(CONF_MAX_MINUTES, DEFAULT_MAX_MINUTES)):
                vol.All(int, vol.Range(min=1, max=60)),
            vol.Required(CONF_AI_URL, default=current.get(CONF_AI_URL, DEFAULT_AI_URL)): str,
            vol.Required(CONF_AI_MODEL, default=current.get(CONF_AI_MODEL, DEFAULT_AI_MODEL)): str,
            vol.Optional(CONF_AI_CONFIRM_MODEL, default=current.get(CONF_AI_CONFIRM_MODEL, DEFAULT_AI_CONFIRM_MODEL)): str,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
