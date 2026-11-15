"""Dreame Camera: live camera of Dreame vacuums (Tencent XP2P) via the Dreame Camera Bridge add-on."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, entity_registry as er

from .const import DOMAIN
from .inspection import Inspector
from .session import CameraSession

POINT_SCHEMA = vol.Schema({
    vol.Optional("name"): cv.string,
    vol.Required("x"): vol.Coerce(int),
    vol.Required("y"): vol.Coerce(int),
    vol.Optional("headings", default=[]): [vol.Coerce(float)],
    vol.Optional("lights", default=[]): cv.entity_ids,
})
INSPECT_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("points"): vol.All(cv.ensure_list, [POINT_SCHEMA]),
    vol.Optional("return_to_base", default=True): cv.boolean,
    vol.Optional("width", default=640): vol.All(vol.Coerce(int), vol.Range(min=320, max=1280)),
    vol.Optional("compare", default=True): cv.boolean,
    vol.Optional("analyze", default="changed"): vol.In(["never", "changed", "always"]),
})


async def async_setup(hass: HomeAssistant, config) -> bool:
    async def _inspect(call: ServiceCall) -> ServiceResponse:
        reg = er.async_get(hass)
        ent = reg.async_get(call.data["entity_id"])
        session = hass.data.get(DOMAIN, {}).get(ent.config_entry_id) if ent else None
        if session is None:
            raise HomeAssistantError("Not a Dreame Camera entity")
        inspector = hass.data[DOMAIN].setdefault("_inspectors", {}).setdefault(
            ent.config_entry_id, Inspector(hass, session))
        return await inspector.async_run(call.data["points"], call.data["return_to_base"], call.data["width"],
                                         call.data["compare"], call.data["analyze"], context=call.context)

    hass.services.async_register(DOMAIN, "inspect", _inspect, schema=INSPECT_SCHEMA,
                                 supports_response=SupportsResponse.OPTIONAL)
    return True

PLATFORMS = [Platform.CAMERA, Platform.SWITCH]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = CameraSession(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = session
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session: CameraSession = hass.data[DOMAIN][entry.entry_id]
    await session.async_stop("unload")
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
