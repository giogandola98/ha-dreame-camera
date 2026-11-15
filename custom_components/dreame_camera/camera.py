"""Camera entity for the Dreame vacuum camera."""
from __future__ import annotations

import io
import logging
import time

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.ffmpeg import async_get_image
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .session import CameraError, CameraSession

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    session: CameraSession = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DreameCamera(session, entry)])


class DreameCamera(Camera):
    """Live camera of a Dreame vacuum. Off by default: the robot camera starts only on demand."""

    _attr_has_entity_name = True
    _attr_translation_key = "robot_camera"
    _attr_supported_features = CameraEntityFeature.STREAM | CameraEntityFeature.ON_OFF
    _attr_brand = "Dreame"

    def __init__(self, session: CameraSession, entry: ConfigEntry) -> None:
        super().__init__()
        # We override async_handle_async_webrtc_offer only to wake the robot before delegating to the
        # registered provider (go2rtc); we are NOT a native WebRTC camera, so keep providers enabled.
        self._supports_native_async_webrtc = False
        self._session = session
        self._attr_unique_id = f"{entry.entry_id}_camera"
        self._last_image: bytes | None = None
        self._last_image_at: float | None = None
        self._placeholder: bytes | None = None
        self._attr_device_info = session.device_info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._session.async_add_listener(self._on_session_change))

    # --- placeholder shown while the robot camera is off ------------------------
    @staticmethod
    def _make_placeholder() -> bytes:
        from PIL import Image, ImageDraw  # Pillow ships with Home Assistant

        img = Image.new("RGB", (1280, 720), (38, 38, 38))
        draw = ImageDraw.Draw(img)
        text = "OFF"
        try:
            from PIL import ImageFont

            font = ImageFont.load_default(size=120)
        except Exception:  # noqa: BLE001 - very old Pillow
            font = None
        box = draw.textbbox((0, 0), text, font=font)
        draw.text(((1280 - (box[2] - box[0])) / 2 - box[0], (720 - (box[3] - box[1])) / 2 - box[1]), text, fill=(170, 170, 170), font=font)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return buf.getvalue()

    @callback
    def _on_session_change(self) -> None:
        if not self._session.active and self.stream is not None:
            # Drop the HA stream so the next viewer calls stream_source() and restarts the robot camera.
            stream, self.stream = self.stream, None
            self.hass.async_create_task(stream.stop())
        self.async_write_ha_state()

    # is_on stays True (the entity is always available, so dashboards can show the last frame);
    # whether the robot camera is actually transmitting is reported by is_streaming / state.

    @property
    def is_streaming(self) -> bool:
        return self._session.active

    @property
    def extra_state_attributes(self) -> dict:
        s = self._session
        return {
            "link": s.link,
            "started_at": s.started_at,
            "auto_off_at": s.deadline,
            "last_snapshot_at": self._last_image_at,
            "last_error": s.last_error,
        }

    async def stream_source(self) -> str | None:
        # HA (e.g. go2rtc provider discovery) calls this without anyone watching:
        # only hand out the URL here, never wake the robot camera.
        return self._session.rtsp_url

    async def _ensure_started(self) -> None:
        try:
            await self._session.async_start()
        except CameraError as err:
            raise HomeAssistantError(f"Dreame camera: {err}") from err

    async def async_handle_async_webrtc_offer(self, offer_sdp, session_id, send_message) -> None:
        """A viewer opened the live view over WebRTC: start the robot camera first."""
        await self._ensure_started()
        await super().async_handle_async_webrtc_offer(offer_sdp, session_id, send_message)

    async def async_create_stream(self):
        """A viewer requested an HLS stream: start the robot camera first."""
        await self._ensure_started()
        return await super().async_create_stream()

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        # Camera off: always the "OFF" placeholder (never wakes the robot).
        if self._session.active:
            image = await async_get_image(self.hass, self._session.rtsp_url, width=width, height=height,
                                          extra_cmd="-rtsp_transport tcp")
            if image:
                self._last_image, self._last_image_at = image, time.time()
                return image
        if self._placeholder is None:
            self._placeholder = await self.hass.async_add_executor_job(self._make_placeholder)
        return self._placeholder

    async def async_turn_on(self) -> None:
        await self._ensure_started()

    async def async_turn_off(self) -> None:
        await self._session.async_stop("turned off")
