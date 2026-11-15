"""Camera session: Dreame cloud activation (via dreame_vacuum) + media bridge add-on."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from urllib.parse import quote

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    AIID_ACCESS_CODE,
    AIID_CAMERA_OPERATE,
    AIID_VOICE_OPERATE,
    CONF_AUDIO,
    CONF_BRIDGE_HOST,
    CONF_BRIDGE_PORT,
    CONF_BRIDGE_TOKEN,
    CONF_MAX_MINUTES,
    CONF_PIN,
    CONF_VACUUM_ENTRY,
    DEFAULT_MAX_MINUTES,
    DEFAULT_RTSP_PORT,
    DREAME_VACUUM_DOMAIN,
    KEEP_ALIVE_SECONDS,
    PIID_CODE_OPEN,
    PIID_CODE_VERIFY,
    PIID_DEVICE_KEEP_ALIVE,
    PIID_KEEP_ALIVE,
    PIID_MONITOR,
    PIID_VOICE_OPERATE,
    SIID_CAMERA,
    SIID_DEVICE_KEEP_ALIVE,
)

_LOGGER = logging.getLogger(__name__)


class CameraError(Exception):
    """Raised when the camera session cannot be started."""


class CameraSession:
    """One robot camera. All Dreame cloud calls reuse dreame_vacuum's logged-in protocol."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._keepalive_task: asyncio.Task | None = None
        self._identity: tuple[str, str] | None = None
        self.session_id: str | None = None
        self.active = False
        self.started_at: float | None = None
        self.deadline: float | None = None
        self.link: str | None = None
        self.last_error: str | None = None
        self.audio_enabled: bool = bool({**entry.data, **entry.options}.get(CONF_AUDIO, True))

    # ------------------------------------------------------------------ config
    @property
    def _cfg(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    @property
    def _bridge_url(self) -> str:
        return f"http://{self._cfg[CONF_BRIDGE_HOST]}:{self._cfg[CONF_BRIDGE_PORT]}"

    @property
    def rtsp_url(self) -> str:
        token = quote(self._cfg[CONF_BRIDGE_TOKEN], safe="")
        return f"rtsp://ha:{token}@{self._cfg[CONF_BRIDGE_HOST]}:{DEFAULT_RTSP_PORT}/live"

    def coordinator(self):
        coord = self.hass.data.get(DREAME_VACUUM_DOMAIN, {}).get(self._cfg[CONF_VACUUM_ENTRY])
        if coord is None or getattr(coord, "device", None) is None:
            raise CameraError("dreame_vacuum is not loaded")
        return coord

    def _protocol(self):
        return self.coordinator().device._protocol  # noqa: SLF001 - shared, already logged in

    # ------------------------------------------------------------- listeners
    @callback
    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    # ------------------------------------------------------- blocking (executor)
    def _action(self, aiid: int, piid: int, value: dict) -> bool:
        value = {**value, "session": self.session_id}
        res = self._protocol().action(
            SIID_CAMERA, aiid, [{"piid": piid, "value": json.dumps(value, separators=(",", ":"))}]
        )
        ok = isinstance(res, dict) and res.get("code") == 0
        if not ok:
            _LOGGER.debug("camera action %s.%s returned %s", aiid, piid, res)
        return ok

    def _video_api(self, path: str, body: dict) -> dict:
        resp = self._protocol().cloud._api_call(f"dreame-third-video/{path}", body)  # noqa: SLF001
        if not resp or not resp.get("success"):
            raise CameraError(f"{path}: {resp and resp.get('code')} {resp and resp.get('msg')}")
        return resp["data"]["data"]

    def _did(self) -> str:
        return str(self._protocol().cloud.device_id)

    def _activate_sync(self) -> dict:
        did = self._did()
        if self._identity is None:
            data = self._video_api("tx/mgr/dev/getIdentity", {"did": did, "os": "ios"})
            self._identity = (data["productId"], data["deviceName"])
        product_id, device_name = self._identity
        self._action(AIID_ACCESS_CODE, PIID_CODE_OPEN, {"open": True})
        pin_hash = hashlib.sha256(str(self._cfg[CONF_PIN]).encode()).hexdigest()
        if not self._action(AIID_ACCESS_CODE, PIID_CODE_VERIFY, {"oldcode": pin_hash, "lazymode": 0}):
            raise CameraError("camera PIN rejected by the robot")
        if not self._action(AIID_CAMERA_OPERATE, PIID_MONITOR, {
            "token": "tx", "channelId": f"{product_id}/{device_name}",
            "operType": "monitor", "operation": "start",
        }):
            raise CameraError("robot refused to start the camera monitor")
        if self.audio_enabled:
            # Microfono del robot: dopo questo comando il robot inserisce l'audio (AAC) nello
            # stesso flusso FLV del video. Va fatto PRIMA di avviare il bridge.
            if not self._action(AIID_VOICE_OPERATE, PIID_VOICE_OPERATE, {"operType": "intercom", "operation": "start"}):
                _LOGGER.debug("robot did not confirm microphone (intercom) start")
        time.sleep(1)
        info = self._video_api("tx/dev/getP2PInfo", {"did": did})["p2pInfo"]
        return {"product_id": product_id, "device_name": device_name, "xp2p_info": info,
                "audio": self.audio_enabled}

    def _keepalive_sync(self) -> None:
        self._protocol().set_property(SIID_DEVICE_KEEP_ALIVE, PIID_DEVICE_KEEP_ALIVE, 1)
        self._action(AIID_CAMERA_OPERATE, PIID_KEEP_ALIVE, {"operType": "keep_alive", "videoStatus": "opened"})

    def _end_monitor_sync(self) -> None:
        if not self._identity:
            return
        product_id, device_name = self._identity
        if self.audio_enabled:
            self._action(AIID_VOICE_OPERATE, PIID_VOICE_OPERATE, {"operType": "intercom", "operation": "end"})
        self._action(AIID_CAMERA_OPERATE, PIID_MONITOR, {
            "token": "tx", "channelId": f"{product_id}/{device_name}",
            "operType": "monitor", "operation": "end",
        })

    # ---------------------------------------------------------------- bridge
    async def _bridge(self, method: str, path: str, body: dict | None = None, timeout: int = 10) -> dict:
        session = async_get_clientsession(self.hass)
        headers = {"Authorization": f"Bearer {self._cfg[CONF_BRIDGE_TOKEN]}"}
        try:
            async with session.request(method, f"{self._bridge_url}{path}", json=body, headers=headers,
                                       timeout=aiohttp.ClientTimeout(total=timeout)) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    raise CameraError(f"bridge {path}: {resp.status} {data.get('error') if data else ''}")
                return data
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise CameraError(f"bridge add-on unreachable: {err}") from err

    async def async_bridge_available(self) -> bool:
        try:
            await self._bridge("GET", "/status", timeout=5)
            return True
        except CameraError:
            return False

    # ------------------------------------------------------------- lifecycle
    def _extend(self) -> None:
        minutes = int(self._cfg.get(CONF_MAX_MINUTES, DEFAULT_MAX_MINUTES))
        self.deadline = time.time() + minutes * 60

    async def async_start(self) -> None:
        async with self._lock:
            if self.active:
                self._extend()
                return
            self.session_id = uuid.uuid4().hex
            try:
                params = await self.hass.async_add_executor_job(self._activate_sync)
                await self._bridge("POST", "/start", params, timeout=70)
            except Exception as err:
                self.last_error = str(err)
                _LOGGER.warning("Dreame camera start failed: %s", err)
                await self.hass.async_add_executor_job(self._safe_end)
                self._notify()
                raise CameraError(str(err)) from err
            self.active = True
            self.last_error = None
            self.started_at = time.time()
            self.link = None
            self._extend()
            self._keepalive_task = self.entry.async_create_background_task(
                self.hass, self._keepalive_loop(), "dreame_camera_keepalive"
            )
            _LOGGER.info("Dreame camera live")
            self._notify()

    def _safe_end(self) -> None:
        try:
            self._end_monitor_sync()
        except Exception:  # noqa: BLE001
            _LOGGER.debug("monitor end failed", exc_info=True)

    async def async_stop(self, reason: str = "requested") -> None:
        async with self._lock:
            if not self.active:
                return
            self.active = False
            task, self._keepalive_task = self._keepalive_task, None
            if task and task is not asyncio.current_task():
                task.cancel()
            try:
                await self._bridge("POST", "/stop")
            except CameraError as err:
                _LOGGER.debug("bridge stop: %s", err)
            await self.hass.async_add_executor_job(self._safe_end)
            self.started_at = self.deadline = None
            _LOGGER.info("Dreame camera stopped (%s)", reason)
            self._notify()

    @property
    def device_info(self):
        from homeassistant.helpers.device_registry import DeviceInfo
        from .const import CONF_VACUUM_ENTRY, DOMAIN
        try:
            return DeviceInfo(identifiers={(DREAME_VACUUM_DOMAIN, self.coordinator().device.mac)})
        except CameraError:
            return DeviceInfo(identifiers={(DOMAIN, self.entry.data[CONF_VACUUM_ENTRY])},
                              name=self.entry.title, manufacturer="Dreame")

    async def async_set_audio(self, enabled: bool) -> None:
        """Toggle the robot microphone. If the camera is on, restart the stream so that
        ffmpeg re-probes it (the RTSP track list is fixed when the stream starts)."""
        if enabled == self.audio_enabled:
            return
        was_active = self.active
        if was_active:
            await self.async_stop("audio change")
        self.audio_enabled = enabled
        self._notify()
        if was_active:
            await self.async_start()

    async def _keepalive_loop(self) -> None:
        while self.active:
            await asyncio.sleep(KEEP_ALIVE_SECONDS)
            if not self.active:
                return
            if self.deadline and time.time() > self.deadline:
                self.hass.async_create_task(self.async_stop("time limit"))
                return
            try:
                status = await self._bridge("GET", "/status", timeout=5)
                if not status.get("running"):
                    self.last_error = status.get("error") or "stream ended"
                    self.hass.async_create_task(self.async_stop("stream ended"))
                    return
                if status.get("link") != self.link:
                    self.link = status.get("link")
                    self._notify()
                await self.hass.async_add_executor_job(self._keepalive_sync)
            except Exception as err:  # noqa: BLE001
                _LOGGER.debug("keep-alive failed: %s", err)
