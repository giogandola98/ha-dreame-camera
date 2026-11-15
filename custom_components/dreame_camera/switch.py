"""Audio switch: turns the robot microphone on/off for the camera stream."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import CONF_AUDIO, DOMAIN
from .session import CameraSession


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    session: CameraSession = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DreameCameraAudioSwitch(session, entry)])


class DreameCameraAudioSwitch(SwitchEntity, RestoreEntity):
    """On: the robot microphone is armed and its audio is part of the stream."""

    _attr_has_entity_name = True
    _attr_translation_key = "audio"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, session: CameraSession, entry: ConfigEntry) -> None:
        self._session = session
        self._attr_unique_id = f"{entry.entry_id}_audio"
        self._attr_device_info = session.device_info

    async def async_added_to_hass(self) -> None:
        last = await self.async_get_last_state()
        if last is not None and last.state in ("on", "off"):
            self._session.audio_enabled = last.state == "on"
        else:
            self._session.audio_enabled = bool({**self._session.entry.data, **self._session.entry.options}.get(CONF_AUDIO, True))
        self.async_on_remove(self._session.async_add_listener(self.async_write_ha_state))

    @property
    def is_on(self) -> bool:
        return self._session.audio_enabled

    @property
    def icon(self) -> str:
        return "mdi:volume-high" if self.is_on else "mdi:volume-off"

    async def async_turn_on(self, **kwargs) -> None:
        await self._session.async_set_audio(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._session.async_set_audio(False)
