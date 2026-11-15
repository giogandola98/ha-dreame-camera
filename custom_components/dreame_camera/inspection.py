"""Inspection rounds: drive the robot to points, face given headings, take photos.

The robot plans its own path to each point (go-to / cruise, with obstacle avoidance);
we only rotate it on the spot (velocity 0) and take a photo once its pose is stable,
because the pose reported by the map lags a few seconds behind reality.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path

from homeassistant.components.ffmpeg import async_get_image
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .analysis import CHANGE_THRESHOLD, LocalAnalyzer
from .const import (
    CONF_AI_CONFIRM_MODEL,
    CONF_AI_MODEL,
    CONF_AI_URL,
    DEFAULT_AI_CONFIRM_MODEL,
    DEFAULT_AI_MODEL,
    DEFAULT_AI_URL,
)
from .photodiff import difference, signature
from .session import CameraError, CameraSession

_LOGGER = logging.getLogger(__name__)

ARRIVE_MM = 400
LIGHT_SETTLE_S = 3  # camera auto-exposure after a light is switched on
ARRIVE_TIMEOUT = 240
HEADING_TOLERANCE = 15
MEDIA_SUBDIR = "dreame_camera"
REFERENCE_SUBDIR = "riferimenti"


def _compare(new: bytes, ref_path: Path) -> float | None:
    if not ref_path.exists():
        return None
    return difference(signature(new), signature(ref_path.read_bytes()))


def _save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _pose(device) -> tuple[float, float, float] | None:
    status = getattr(device, "status", None)
    current = getattr(status, "current_map", None) or getattr(device, "current_map", None)
    p = getattr(current, "robot_position", None)
    if p is None or p.a is None:
        return None
    return float(p.x), float(p.y), float(p.a)


def _resize_jpeg(image: bytes, width: int) -> bytes:
    """Scale a JPEG down to `width` px (small images are much faster for local vision models)."""
    import io
    from PIL import Image  # Pillow ships with Home Assistant

    img = Image.open(io.BytesIO(image))
    if img.width <= width:
        return image
    img = img.convert("RGB").resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _angle_diff(target: float, actual: float) -> float:
    return ((target - actual + 180) % 360) - 180


class Inspector:
    def __init__(self, hass: HomeAssistant, session: CameraSession) -> None:
        self.hass = hass
        self.session = session
        self._lock = asyncio.Lock()
        self._context: Context | None = None

    @property
    def _device(self):
        return self.session.coordinator().device

    async def _stable_pose(self, timeout: float = 25) -> tuple[float, float, float] | None:
        last, start = None, time.monotonic()
        while time.monotonic() - start < timeout:
            p = _pose(self._device)
            if p and last and abs(p[0] - last[0]) < 30 and abs(p[1] - last[1]) < 30 \
                    and abs(_angle_diff(p[2], last[2])) < 3:
                return p
            last = p
            await asyncio.sleep(3)
        return last

    async def _go_to(self, x: int, y: int) -> bool:
        await self.hass.async_add_executor_job(self._device.go_to, x, y)
        start = time.monotonic()
        while time.monotonic() - start < ARRIVE_TIMEOUT:
            self.session._extend()  # noqa: SLF001 - keep the camera alive during the round
            p = _pose(self._device)
            if p and abs(p[0] - x) < ARRIVE_MM and abs(p[1] - y) < ARRIVE_MM:
                await asyncio.sleep(6)  # let the cruise settle
                return True
            await asyncio.sleep(2)
        return False

    async def _face(self, heading: float) -> tuple[float, float, float] | None:
        p = None
        for _ in range(8):
            self.session._extend()  # noqa: SLF001
            p = await self._stable_pose()
            if p is None:
                return None
            err = _angle_diff(heading, p[2])
            if abs(err) < HEADING_TOLERANCE:
                return p
            rot = max(-90, min(90, int(err)))
            await self.hass.async_add_executor_job(self._device.remote_control_move_step, rot, 0, False)
            await asyncio.sleep(3)
        return p

    def _analyzer(self) -> LocalAnalyzer:
        cfg = {**self.session.entry.data, **self.session.entry.options}
        return LocalAnalyzer(self.hass, cfg.get(CONF_AI_URL, DEFAULT_AI_URL),
                             cfg.get(CONF_AI_MODEL, DEFAULT_AI_MODEL),
                             cfg.get(CONF_AI_CONFIRM_MODEL, DEFAULT_AI_CONFIRM_MODEL))

    async def _lights(self, service: str, entity_ids: list[str]) -> None:
        # homeassistant.turn_on/off: works for lights and for relays exposed as switches
        try:
            await self.hass.services.async_call("homeassistant", service, {"entity_id": entity_ids},
                                                blocking=True, context=self._context)
        except Exception:  # noqa: BLE001 - a light must never break the patrol
            _LOGGER.warning("Inspection: light.%s %s failed", service, entity_ids, exc_info=True)

    async def _shoot_point(self, point: dict, name: str, width: int, compare: bool, out_dir: Path,
                           stamp: str, media_root: Path, photos: list[dict],
                           pending: list[tuple[bytes, Path, dict]]) -> None:
        for heading in point.get("headings") or [None]:
            pose = await self._face(heading) if heading is not None else await self._stable_pose()
            image = await async_get_image(self.hass, self.session.rtsp_url, width=width,
                                          extra_cmd="-rtsp_transport tcp")
            if not image:
                continue
            image = await self.hass.async_add_executor_job(_resize_jpeg, image, width)
            fname = f"{name}_{int(heading) if heading is not None else 'x'}.jpg".replace(" ", "_")
            path = out_dir / fname
            await self.hass.async_add_executor_job(path.write_bytes, image)
            rel = f"{MEDIA_SUBDIR}/{stamp}/{fname}"
            ref_path = media_root / MEDIA_SUBDIR / REFERENCE_SUBDIR / fname
            diff = await self.hass.async_add_executor_job(_compare, image, ref_path) if compare else None
            changed = None if diff is None else diff > CHANGE_THRESHOLD
            photo = {
                "name": name,
                "heading": heading,
                "pose": {"x": pose[0], "y": pose[1], "a": pose[2]} if pose else None,
                "path": str(path),
                "media_content_id": f"media-source://media_source/local/{rel}",
                "url": f"/media/local/{rel}",
                "difference": None if diff is None else round(diff, 3),
                "changed": changed,
                "analysis": None,
            }
            # the same dict is filled in with the analysis later: it must be the returned one
            pending.append((image, ref_path, photo))
            photos.append(photo)

    async def async_run(self, points: list[dict], return_to_base: bool = True, width: int = 640,
                        compare: bool = True, analyze: str = "changed", context: Context | None = None) -> dict:
        if self._lock.locked():
            raise HomeAssistantError("An inspection is already running")
        async with self._lock:
            self._context = context  # lights switched by the patrol are logged as caused by its caller
            device = self._device
            if getattr(device.status, "started", False) and not getattr(device.status, "docked", False):
                raise HomeAssistantError("The robot is busy (cleaning): inspection not started")

            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            media_root = Path(self.hass.config.media_dirs.get("local") or self.hass.config.path("media"))
            out_dir = media_root / MEDIA_SUBDIR / stamp
            await self.hass.async_add_executor_job(lambda: out_dir.mkdir(parents=True, exist_ok=True))

            photos: list[dict] = []
            skipped: list[str] = []
            pending: list[tuple[bytes, Path, dict]] = []
            try:
                await self.session.async_start()
                for point in points:
                    name = point.get("name") or f"{point['x']},{point['y']}"
                    # lights of this point: switched on only if off, back off after its photos
                    lit = [e for e in point.get("lights") or [] if self.hass.states.is_state(e, "off")]
                    try:
                        if lit:
                            await self._lights("turn_on", lit)
                        if not await self._go_to(int(point["x"]), int(point["y"])):
                            _LOGGER.warning("Inspection: point %s not reached", name)
                            skipped.append(name)
                            continue
                        if lit:
                            await asyncio.sleep(LIGHT_SETTLE_S)
                        await self._shoot_point(point, name, width, compare, out_dir, stamp, media_root,
                                                photos, pending)
                    finally:
                        if lit:
                            await self._lights("turn_off", lit)
            except CameraError as err:
                raise HomeAssistantError(f"Dreame camera: {err}") from err
            finally:
                if return_to_base:
                    try:
                        await self.hass.async_add_executor_job(device.return_to_base)
                    except Exception:  # noqa: BLE001
                        _LOGGER.warning("Inspection: return to base failed", exc_info=True)
                await self.session.async_stop("inspection finished")

            analyzer = self._analyzer() if analyze != "never" else None
            for image, ref_path, extra in pending:
                if extra["changed"] is None:
                    # prima volta per questo punto: diventa la foto di riferimento
                    await self.hass.async_add_executor_job(_save, ref_path, image)
                    if analyze != "always":
                        continue
                must = analyze == "always" or (analyze == "changed" and extra["changed"])
                result = None
                if analyzer and must:
                    try:
                        result = await analyzer.async_analyze(image)
                    except Exception as err:  # noqa: BLE001
                        _LOGGER.warning("Inspection: local analysis failed: %s", err)
                        result = {"error": str(err), "anomaly": None}
                    extra["analysis"] = result
                # aggiorna il riferimento quando la scena è normale (si adatta a luce e piccoli spostamenti)
                if extra["changed"] is False or (result is not None and result.get("anomaly") is False):
                    await self.hass.async_add_executor_job(_save, ref_path, image)
            anomalies = [p for p in photos if (p.get("analysis") or {}).get("anomaly")]
            return {"folder": f"{MEDIA_SUBDIR}/{stamp}", "photos": photos, "skipped": skipped,
                    "anomalies": anomalies, "anomaly": bool(anomalies)}
