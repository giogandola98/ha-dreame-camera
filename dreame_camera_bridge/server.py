#!/usr/bin/env python3
"""Dreame Camera Bridge - add-on control API (stdlib only).

The dreame_camera integration does everything that needs the Dreame cloud (login, PIN,
starting the robot's monitor mode) and then calls POST /start with the Tencent identity
and xp2p_info. This server:
  1. runs xp2p_bridge (Tencent XP2P SDK) -> local HTTP-FLV URL
  2. republishes it with ffmpeg (no re-encode) to MediaMTX on rtsp://127.0.0.1:8554/live
  3. MediaMTX serves it to Home Assistant on the internal network, user "ha" + api_token

API (all require "Authorization: Bearer <api_token>"):
  POST /start {product_id, device_name, xp2p_info}  -> {"ok": true, "path": "live", "link": ...}
  POST /stop                                        -> {"ok": true}
  GET  /status                                      -> {"running": bool, "link": ..., "since": ...}
"""
from __future__ import annotations

import hmac
import json
import logging
import os
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

OPTIONS = "/data/options.json"
MTX_CONFIG = "/tmp/mediamtx.yml"
RTSP_LOCAL = "rtsp://127.0.0.1:8554/live"
MTX_API = "http://127.0.0.1:9997/v3/paths/get/live"
LINK_NAMES = {"62": "lan", "63": "relay"}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bridge")


def load_options() -> dict:
    try:
        with open(OPTIONS) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


OPTS = load_options()
TOKEN = str(OPTS.get("api_token") or "")
QUALITY = str(OPTS.get("quality") or "high")


def write_mediamtx_config() -> None:
    # Local processes (ffmpeg, our API polling) are trusted; HA reads with user "ha".
    cfg = f"""logLevel: warn
rtsp: yes
rtspAddress: :8554
rtmp: no
hls: no
webrtc: no
srt: no
api: yes
apiAddress: 127.0.0.1:9997
readTimeout: 30s
writeTimeout: 30s
authMethod: internal
authInternalUsers:
  - user: any
    ips: ['127.0.0.1/32', '::1/128']
    permissions:
      - action: publish
      - action: read
      - action: api
  - user: ha
    pass: {json.dumps(TOKEN)}
    permissions:
      - action: read
paths:
  live:
"""
    with open(MTX_CONFIG, "w") as f:
        f.write(cfg)
    os.chmod(MTX_CONFIG, 0o600)


class Stream:
    """One live session: xp2p_bridge + ffmpeg republisher."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.bridge: subprocess.Popen | None = None
        self.ffmpeg: subprocess.Popen | None = None
        self.link: str | None = None
        self.since: float | None = None
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return bool(self.bridge and self.bridge.poll() is None and self.ffmpeg and self.ffmpeg.poll() is None)

    def _reader(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:
            line = raw.strip()
            if line.startswith("LINK "):
                self.link = LINK_NAMES.get(line[5:], self.link)
            elif line:
                log.info("xp2p: %s", line if not line.startswith("READY") else "READY")

    def start(self, product_id: str, device_name: str, xp2p_info: str, audio: bool = True) -> dict:
        with self.lock:
            self._stop_locked()
            env = {**os.environ, "XP2P_PRODUCT_ID": product_id, "XP2P_DEVICE_NAME": device_name,
                   "XP2P_INFO": xp2p_info, "XP2P_QUALITY": QUALITY}
            self.bridge = subprocess.Popen(["xp2p_bridge"], env=env, stdin=subprocess.PIPE,
                                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                           text=True, bufsize=1)
            url = None
            deadline = time.time() + 25
            while time.time() < deadline:
                line = self.bridge.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line.startswith("READY "):
                    url = line.split(" ", 1)[1]
                    break
                if line.startswith("ERROR"):
                    self.last_error = line
                    break
            if not url:
                self._stop_locked()
                raise RuntimeError(self.last_error or "xp2p_bridge did not become ready")
            threading.Thread(target=self._reader, args=(self.bridge,), daemon=True).start()

            self.ffmpeg = subprocess.Popen(
                ["ffmpeg", "-hide_banner", "-loglevel", "warning", "-fflags", "+genpts",
                 # sonda abbastanza a lungo da vedere anche l'audio del microfono (se attivo)
                 "-analyzeduration", "4000000", "-probesize", "4000000",
                 "-i", url, "-map", "0:v:0", *(["-map", "0:a:0?"] if audio else ["-an"]),
                 # video copiato senza ricodifica; audio AAC -> Opus (WebRTC/go2rtc non supporta AAC)
                 "-c:v", "copy",
                 # il robot manda timestamp audio leggermente fuori ordine: aresample li riallinea
                 "-af", "aresample=async=1000:first_pts=0",
                 "-c:a", "libopus", "-b:a", "32k", "-ar", "48000", "-ac", "1",
                 "-f", "rtsp", "-rtsp_transport", "tcp", RTSP_LOCAL],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            threading.Thread(target=self._log_ffmpeg, args=(self.ffmpeg,), daemon=True).start()

            deadline = time.time() + 25
            while time.time() < deadline:
                if self.ffmpeg.poll() is not None or self.bridge.poll() is not None:
                    break
                if path_ready():
                    self.since = time.time()
                    self.last_error = None
                    threading.Timer(6, self._ask_link).start()
                    log.info("stream live")
                    return {"ok": True, "path": "live"}
                time.sleep(0.5)
            self._stop_locked()
            raise RuntimeError("stream did not reach MediaMTX")

    def _ask_link(self) -> None:
        try:
            if self.bridge and self.bridge.poll() is None:
                self.bridge.stdin.write("link\n")
                self.bridge.stdin.flush()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def _log_ffmpeg(proc: subprocess.Popen) -> None:
        for line in proc.stderr:
            log.warning("ffmpeg: %s", line.strip())

    def stop(self) -> None:
        with self.lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if self.ffmpeg and self.ffmpeg.poll() is None:
            self.ffmpeg.terminate()
            try:
                self.ffmpeg.wait(5)
            except subprocess.TimeoutExpired:
                self.ffmpeg.kill()
        if self.bridge and self.bridge.poll() is None:
            try:
                self.bridge.stdin.write("quit\n")
                self.bridge.stdin.flush()
                self.bridge.wait(5)
            except Exception:  # noqa: BLE001
                self.bridge.kill()
        if self.since:
            log.info("stream stopped")
        self.bridge = self.ffmpeg = None
        self.since = None
        self.link = None

    def watchdog(self) -> None:
        while True:
            time.sleep(2)
            with self.lock:
                if self.since and not self.running:
                    self.last_error = "stream process exited"
                    log.warning("stream process exited, cleaning up")
                    self._stop_locked()


def path_tracks() -> list:
    try:
        with urllib.request.urlopen(MTX_API, timeout=2) as r:
            return json.load(r).get("tracks") or []
    except Exception:  # noqa: BLE001
        return []


def path_ready() -> bool:
    try:
        with urllib.request.urlopen(MTX_API, timeout=2) as r:
            return bool(json.load(r).get("ready"))
    except Exception:  # noqa: BLE001
        return False


STREAM = Stream()


class Handler(BaseHTTPRequestHandler):
    server_version = "DreameCameraBridge/0.2"

    def log_message(self, fmt, *args):  # quieter access log
        log.debug(fmt, *args)

    def _authorized(self) -> bool:
        got = self.headers.get("Authorization", "")
        return bool(TOKEN) and hmac.compare_digest(got.encode(), f"Bearer {TOKEN}".encode())

    def _reply(self, code: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if not self._authorized():
            return self._reply(401, {"error": "unauthorized"})
        if self.path == "/status":
            return self._reply(200, {"running": STREAM.running, "link": STREAM.link,
                                     "since": STREAM.since, "error": STREAM.last_error,
                                     "tracks": path_tracks() if STREAM.running else []})
        self._reply(404, {"error": "not found"})

    def do_POST(self):
        if not self._authorized():
            return self._reply(401, {"error": "unauthorized"})
        if self.path == "/stop":
            STREAM.stop()
            return self._reply(200, {"ok": True})
        if self.path == "/start":
            try:
                length = min(int(self.headers.get("Content-Length", 0)), 65536)
                body = json.loads(self.rfile.read(length) or b"{}")
                result = STREAM.start(str(body["product_id"]), str(body["device_name"]), str(body["xp2p_info"]),
                                      bool(body.get("audio", True)))
                return self._reply(200, result)
            except KeyError as err:
                return self._reply(400, {"error": f"missing {err}"})
            except Exception as err:  # noqa: BLE001
                log.warning("start failed: %s", err)
                return self._reply(502, {"error": str(err)})
        self._reply(404, {"error": "not found"})


def run_mediamtx() -> None:
    while True:
        proc = subprocess.Popen(["mediamtx", MTX_CONFIG])
        proc.wait()
        log.warning("mediamtx exited (%s), restarting", proc.returncode)
        time.sleep(2)


def main() -> None:
    if not TOKEN or len(TOKEN) < 16:
        log.error("api_token must be set (at least 16 characters) in the add-on configuration")
    write_mediamtx_config()
    threading.Thread(target=run_mediamtx, daemon=True).start()
    threading.Thread(target=STREAM.watchdog, daemon=True).start()
    log.info("Dreame Camera Bridge ready on :8099 (quality=%s)", QUALITY)
    ThreadingHTTPServer(("0.0.0.0", 8099), Handler).serve_forever()


if __name__ == "__main__":
    main()
