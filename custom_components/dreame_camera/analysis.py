"""Local image analysis for inspections: change detection + yes/no questions to a local Ollama model.

Small vision models answer simple direct questions far better than long structured prompts,
and after the first question the image stays in Ollama's cache, so follow-ups take seconds.
"""
from __future__ import annotations

import base64
import io
import logging

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

CHANGE_THRESHOLD = 0.40   # calibrato: stessa scena ~0.01-0.14, scene diverse 0.7-1.4
AI_WIDTH = 512            # una sola "tessera" per SmolVLM2: molto più veloce

QUESTIONS = {
    "person_or_animal": "Is there a person or an animal in this image? Answer only yes or no.",
    "water": "Is there water or another liquid spilled on the floor in this image? Answer only yes or no.",
    "smoke_or_fire": "Is there smoke or fire in this image? Answer only yes or no.",
    "broken": "Is there broken glass or a broken object on the floor in this image? Answer only yes or no.",
}
LABELS_IT = {
    "person_or_animal": "persona o animale",
    "water": "acqua o liquido a terra",
    "smoke_or_fire": "fumo o fuoco",
    "broken": "vetri o oggetti rotti",
}


def _to_b64(jpeg: bytes, width: int = AI_WIDTH) -> str:
    from PIL import Image

    img = Image.open(io.BytesIO(jpeg)).convert("RGB")
    if img.width > width:
        img = img.resize((width, round(img.height * width / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return base64.b64encode(buf.getvalue()).decode()


class LocalAnalyzer:
    def __init__(self, hass: HomeAssistant, url: str, model: str, confirm_model: str | None) -> None:
        self.hass = hass
        self.url = url.rstrip("/")
        self.model = model
        self.confirm_model = confirm_model or None

    async def _ask(self, model: str, image_b64: str, prompt: str, num_predict: int = 3) -> str:
        session = async_get_clientsession(self.hass)
        body = {"model": model, "prompt": prompt, "images": [image_b64], "stream": False,
                "keep_alive": "10m", "options": {"temperature": 0, "num_predict": num_predict}}
        async with session.post(f"{self.url}/api/generate", json=body,
                                timeout=aiohttp.ClientTimeout(total=900)) as resp:
            data = await resp.json(content_type=None)
            if resp.status != 200:
                raise RuntimeError(data.get("error") if isinstance(data, dict) else resp.status)
            return str(data.get("response", "")).strip()

    async def _check(self, model: str, image_b64: str) -> dict[str, bool]:
        found = {}
        for key, question in QUESTIONS.items():
            answer = (await self._ask(model, image_b64, question)).lower()
            found[key] = answer.startswith("yes") or answer.startswith("sì") or answer.startswith("si")
        return found

    async def async_analyze(self, jpeg: bytes) -> dict:
        image_b64 = await self.hass.async_add_executor_job(_to_b64, jpeg)
        found = await self._check(self.model, image_b64)
        hits = [k for k, v in found.items() if v]
        result = {"checked_by": self.model, "findings": hits, "anomaly": bool(hits), "confirmed": None,
                  "description": None}
        if hits:
            result["description"] = await self._ask(
                self.model, image_b64, "Describe what you see in this image in one sentence.", num_predict=60)
            if self.confirm_model:
                # seconda opinione solo sulle domande risultate positive
                confirm = {}
                for key in hits:
                    answer = (await self._ask(self.confirm_model, image_b64, QUESTIONS[key])).lower()
                    confirm[key] = answer.startswith("yes")
                confirmed = [k for k, v in confirm.items() if v]
                result.update(confirmed=bool(confirmed), findings=confirmed, anomaly=bool(confirmed))
        result["findings_it"] = [LABELS_IT[k] for k in result["findings"]]
        return result
