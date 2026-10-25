"""IBUKI server: board -> Brain -> dashboard (WebSocket /ws), phone/wearable notification, board LED.

Run from the repo root:
  ../env/bin/python pc/ibuki/server.py                       # live board, auto-detected port
  ../env/bin/python pc/ibuki/server.py --replay recordings/rec_breathing_50cm_....csv --loop
  ../env/bin/python pc/ibuki/server.py --brain ibuki.states:Brain   # pick the Brain class

Endpoints (port 8000, also reachable from phones on the same Wi-Fi at http://<laptop-ip>:8000):
  WS   /ws                     Brain statuses and events as JSON, one message per text frame
  WS   /ws/raw                 every raw board line (live viewer / recorder use it via --server)
  GET  /api/health             board connection, lost lines, Brain name, clients, ntfy on/off
  GET  /api/status             latest Brain status
  POST /api/test-notification  sends a test push to the phone
  GET  /                       the built dashboard (web/dist), if it exists

Messages on /ws:
  Brain status   {"t", "state", "bpm", "disp_mm", "motion", "present", "chest_cm", ...}
  Brain alert    {"type": "alert", "reason": "no_breath"|"left_bed", ...}   -> LED red + urgent push
  Brain info     {"type": "info", "reason": "woke_up"|"fast_breathing", ...} -> normal push, no LED
  Board state    {"type": "board", "connected": true|false}
A client may send {"type": "button"} to act like the board's user button (B,1).
The LED goes off when a status says OK or STILL again, or when the button is pressed.
For no_breath the laptop also plays a siren until someone acknowledges it (the dashboard's stop
button = POST /api/button, the board's SW1, or Enter in this window); IBUKI_SIREN=0 turns it off.

Notifications: put IBUKI_NTFY_TOPIC=<topic> in <repo>/.env (git-ignored, never commit it) or set
it in the shell. If ntfy fails (no internet), the LED and dashboard alert keep working.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import wave
from contextlib import asynccontextmanager

PC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_DIR = os.path.dirname(PC_DIR)
if PC_DIR not in sys.path:
    sys.path.insert(0, PC_DIR)

import numpy as np  # noqa: E402
import requests  # noqa: E402
import uvicorn  # noqa: E402
from dotenv import load_dotenv  # noqa: E402
from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

from ibuki.placeholder_brain import PlaceholderBrain  # noqa: E402
from ibuki.serial_reader import (BoardLink, Button, Data, FileReplay, SeqTracker,  # noqa: E402
                                 Status, parse_line)

DEFAULT_BRAINS = ["ibuki.brain:Brain", "ibuki.states:Brain", "ibuki.processing:Brain"]


def log(msg: str):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def to_jsonable(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not JSON serializable: {type(o)}")


def load_brain(spec: str):
    """'auto' tries the team's Brain locations, then falls back to the placeholder."""
    if spec == "placeholder":
        return PlaceholderBrain()
    specs = DEFAULT_BRAINS if spec == "auto" else [spec]
    for s in specs:
        mod_name, _, cls_name = s.partition(":")
        if spec == "auto" and importlib.util.find_spec(mod_name) is None:
            continue  # that file does not exist (yet)
        try:
            brain = getattr(importlib.import_module(mod_name), cls_name or "Brain")()
            log(f"Brain: {s}")
            return brain
        except Exception as e:  # the file exists but is broken: say why, loudly
            if spec != "auto":
                raise SystemExit(f"cannot load Brain {s}: {e!r}")
            log(f"Brain {s} exists but FAILED to load: {e!r}")
    log("Brain: team Brain not found, using the PLACEHOLDER (no bpm, no alerts)")
    return PlaceholderBrain()


class Notifier:
    """Push notification through ntfy (the iPhone app forwards it to the watch)."""

    def __init__(self, topic: str | None, url: str = "https://ntfy.sh", min_interval_s: float = 60.0):
        self.topic = topic
        self.url = url.rstrip("/")
        self.min_interval_s = min_interval_s
        self.last_sent: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.topic)

    # Seconds between two pushes for the same reason (a baby waking up and dozing off again
    # should not buzz the phone every minute).
    MIN_INTERVAL_S = {"woke_up": 300.0}

    # reason -> (ntfy priority, tag shown as an icon). On the iPhone, 5 (urgent) and 4 (high)
    # arrive as Time Sensitive and can break through Focus; 2 (low) arrives silently.
    # CHECK sends nothing: it flickers on during short pauses, and too many pings get ignored.
    LEVELS = {
        "no_breath": (5, "rotating_light"),
        "left_bed": (4, "warning"),
        "fast_breathing": (4, "warning"),
        "woke_up": (2, "baby"),
    }

    # The title starts with the urgency, so it can be read from the lock screen.
    TITLES = {
        "no_breath": "【緊急】呼吸が確認できません",
        "left_bed": "【注意】ベッドにいません",
        "fast_breathing": "【注意】呼吸が速い状態が続いています",
        "woke_up": "【お知らせ】起きました",
    }

    @classmethod
    def level_for(cls, event: dict, urgent: bool) -> tuple[int, str]:
        default = (5, "warning") if urgent else (3, "baby")
        return cls.LEVELS.get(str(event.get("reason")), default)

    @classmethod
    def title_for(cls, event: dict, urgent: bool) -> str:
        return cls.TITLES.get(str(event.get("reason")), "【緊急】息吹～IBUKI～" if urgent else "【お知らせ】息吹～IBUKI～")

    @staticmethod
    def message_for(event: dict) -> str:
        reason, since, bpm = event.get("reason"), event.get("since_s"), event.get("bpm")
        if reason == "no_breath":
            gone = f"{since}秒間" if since else ""
            return f"赤ちゃんの呼吸が{gone}見えません。すぐに様子を見てください。"
        if reason == "left_bed":
            return "赤ちゃんがベッドにいません。様子を確認してください。"
        if reason == "woke_up":
            return "赤ちゃんが起きたようです。"
        if reason == "fast_breathing":
            rate = f"1分に{round(bpm)}回の" if bpm else ""
            return f"{rate}速い呼吸が続いています。様子を見てください。"
        return f"赤ちゃんの様子を確認してください（{reason}）"

    def send(self, title: str, message: str, key: str = "", force: bool = False,
             priority: int = 5, tag: str = "warning") -> tuple[bool, str]:
        if not self.enabled:
            return False, "ntfy disabled (set IBUKI_NTFY_TOPIC)"
        now = time.time()
        wait = self.MIN_INTERVAL_S.get(key, self.min_interval_s)
        if not force and key and now - self.last_sent.get(key, 0.0) < wait:
            return False, "skipped (sent recently)"
        try:
            r = requests.post(self.url, json={"topic": self.topic, "title": title,
                                              "message": message, "priority": priority,
                                              "tags": [tag]}, timeout=5)
            ok = r.status_code < 300
            if ok and key:
                self.last_sent[key] = now
            return ok, f"HTTP {r.status_code}"
        except requests.RequestException as e:
            return False, f"failed: {type(e).__name__}"


class Siren:
    """Loud two-tone alarm from the laptop for the no-breathing alert. Like a baby monitor, it
    keeps sounding until a person acknowledges it: the dashboard's stop button, the board's SW1,
    or Enter in the server window. Breathing coming back alone does not stop it."""

    def __init__(self, enabled: bool):
        self.enabled = enabled and sys.platform == "darwin" and shutil.which("afplay") is not None
        self.on = False
        self._proc = None
        self._wav = None

    def start(self):
        if self.on:
            return
        self.on = True
        log("SIREN on (stop it with the dashboard's stop button, the board's SW1, or Enter here)")
        if self.enabled:
            threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        if not self.on:
            return
        self.on = False
        log("SIREN off")
        proc = self._proc
        if proc and proc.poll() is None:
            proc.terminate()

    def _loop(self):
        self._wav = self._wav or self._make_wav()
        while self.on:
            self._proc = subprocess.Popen(["afplay", self._wav],
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self._proc.wait()

    @staticmethod
    def _make_wav() -> str:
        rate = 22050
        t = np.arange(int(rate * 0.25)) / rate
        fade = np.minimum(1.0, np.minimum(t, t[::-1]) / 0.005)  # no clicks between the tones
        tones = [np.sin(2 * np.pi * f * t) * fade for f in (960, 720, 960, 720)]
        pcm = (np.concatenate(tones) * 0.8 * 32767).astype("<i2")
        path = os.path.join(tempfile.gettempdir(), "ibuki_siren.wav")
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        return path


def enter_acknowledges(hub):
    """Enter in the server's window works like the board button (stops the siren)."""
    for _ in sys.stdin:
        log("Enter pressed: same as the board button")
        hub.on_line("B,1")


class Hub:
    """Owns the Brain. Board lines arrive from a thread and are handled on the asyncio loop."""

    def __init__(self, brain, notifier: Notifier, siren: Siren | None = None):
        self.brain = brain
        self.notifier = notifier
        self.siren = siren or Siren(enabled=False)
        self.clients: set[WebSocket] = set()
        self.raw_clients: set[WebSocket] = set()
        self.latest: dict | None = None
        self.board_connected = False
        self.last_board_status: str | None = None
        self.seq = SeqTracker()
        self.last_state: str | None = None
        self.source = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.queue: asyncio.Queue | None = None
        self.brain_errors = 0
        self.led_on = False
        self.lines = 0

    # called from the board thread
    def on_line(self, text: str):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, ("line", text))

    def on_state(self, connected: bool):
        self.loop.call_soon_threadsafe(self.queue.put_nowait, ("board", connected))

    async def run(self):
        while True:
            kind, value = await self.queue.get()
            if kind == "board":
                self.board_connected = value
                log("board connected" if value else "board DISCONNECTED (waiting to reconnect)")
                if value and self.led_on:
                    self.set_led(True, force=True)  # a replugged board forgets its LED
                await self.broadcast({"type": "board", "connected": value})
            else:
                await self.handle_line(value)

    async def handle_line(self, text: str):
        self.lines += 1
        for ws in list(self.raw_clients):  # live viewer / recorder while the server owns the port
            try:
                await ws.send_text(text)
            except Exception:
                self.raw_clients.discard(ws)
        m = parse_line(text)
        if isinstance(m, Data):
            lost = self.seq.feed(m.seq)
            if lost > 0:
                log(f"lost {lost} line(s) from the board")
            elif lost < 0:
                log("board restarted")
        elif isinstance(m, Button):
            log("button pressed (B,1)")
            self.set_led(False)  # a parent saw it: acknowledge
            self.siren.stop()
        elif isinstance(m, Status):
            self.last_board_status = m.text
            log(f"board: {m.text}")
        try:
            outputs = self.brain.feed(text) or []
        except Exception:
            self.brain_errors += 1
            if self.brain_errors <= 5 or self.brain_errors % 100 == 0:
                log(f"Brain error #{self.brain_errors}:\n{traceback.format_exc()}")
            outputs = []
        for o in outputs:
            await self.handle_output(o)

    async def handle_output(self, o: dict):
        kind = o.get("type")
        if kind in ("alert", "info"):
            # alert (no_breath, left_bed): LED red + urgent push.
            # info (woke_up, fast_breathing): normal push only.
            # The dashboard only accepts events with a numeric since_s, so always send one.
            o = {**o, "since_s": o.get("since_s", 0)}
            log(f"{kind.upper()} event: {o}")
            if kind == "alert":
                self.set_led(True)
            if kind == "alert" and o.get("reason") == "no_breath":
                self.siren.start()
            asyncio.get_running_loop().run_in_executor(None, self._notify, o, kind == "alert")
        elif "state" in o:
            self.latest = o
            state = o["state"]
            if state == "ALERT" and self.last_state != "ALERT":
                self.set_led(True)
            elif state in ("OK", "STILL") and self.led_on and not self.siren.on:
                # the baby is there and fine again. Not while the no-breathing alarm is still
                # unacknowledged: like the siren, the LED keeps blinking until someone presses stop
                self.set_led(False)
            self.last_state = state
        await self.broadcast(o)

    def _notify(self, event: dict, urgent: bool = True):
        reason = str(event.get("reason"))
        priority, tag = Notifier.level_for(event, urgent)
        ok, info = self.notifier.send(Notifier.title_for(event, urgent), Notifier.message_for(event),
                                      key=reason, priority=priority, tag=tag)
        log(f"ntfy [{reason}, priority {priority}]: {'sent' if ok else 'not sent'} ({info})")

    def set_led(self, on: bool, force: bool = False):
        if on == self.led_on and not force:
            return
        self.led_on = on
        cmd = "L,1" if on else "L,0"
        sent = self.source.send(cmd) if self.source else False
        log(f"LED {'red ON' if on else 'off'} ({cmd}{'' if sent else ', board not connected'})")

    def press_button(self):
        """Dashboard cancel: same as the board's user button."""
        log("button from dashboard (B,1)")
        self.on_line("B,1")

    async def broadcast(self, msg: dict):
        if not self.clients:
            return
        text = json.dumps(msg, default=to_jsonable, ensure_ascii=False)
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                self.clients.discard(ws)

    def health(self) -> dict:
        return {
            "board_connected": self.board_connected,
            "source": type(self.source).__name__ if self.source else None,
            "brain": getattr(self.brain, "name", type(self.brain).__name__),
            "clients": len(self.clients),
            "raw_clients": len(self.raw_clients),
            "lines": self.lines,
            "seq": {"lost": self.seq.lost, "gaps": self.seq.gaps, "resets": self.seq.resets},
            "last_board_status": self.last_board_status,
            "brain_errors": self.brain_errors,
            "led_on": self.led_on,
            "siren": self.siren.on,
            "ntfy": self.notifier.enabled,
        }


def create_app(args) -> FastAPI:
    notifier = Notifier(os.environ.get("IBUKI_NTFY_TOPIC"),
                        os.environ.get("IBUKI_NTFY_URL", "https://ntfy.sh"))
    hub = Hub(load_brain(args.brain), notifier, Siren(os.environ.get("IBUKI_SIREN", "1") != "0"))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        hub.loop = asyncio.get_running_loop()
        hub.queue = asyncio.Queue()
        if args.replay:
            hub.source = FileReplay(args.replay, hub.on_line, hub.on_state,
                                    speed=args.speed, loop=args.loop)
            log(f"source: replay {args.replay} (speed x{args.speed}{', looped' if args.loop else ''})")
        else:
            hub.source = BoardLink(args.port, hub.on_line, hub.on_state)
            log(f"source: board {args.port or '/dev/cu.usbmodem* (auto)'}")
        log(f"ntfy: {'on' if notifier.enabled else 'off (set IBUKI_NTFY_TOPIC to enable)'}")
        log(f"siren: {'on' if hub.siren.enabled else 'off'} (IBUKI_SIREN=0 turns it off)")
        task = asyncio.create_task(hub.run())
        hub.source.start()
        if sys.stdin and sys.stdin.isatty():
            threading.Thread(target=enter_acknowledges, args=(hub,), daemon=True).start()
        yield
        hub.siren.stop()
        hub.source.stop()
        task.cancel()

    app = FastAPI(title="IBUKI", lifespan=lifespan)
    app.state.hub = hub

    @app.get("/api/health")
    def health():
        return hub.health()

    @app.get("/api/status")
    def status():
        return hub.latest or {}

    @app.post("/api/button")
    def button():
        """The dashboard's stop button: the same as the board's SW1 (stops the siren)."""
        hub.press_button()
        return {"ok": True}

    @app.post("/api/test-notification")
    def test_notification():
        ok, info = notifier.send("【テスト】息吹～IBUKI～の通知", "通知は正しく届いています。", force=True,
                                 priority=3, tag="white_check_mark")
        log(f"test notification: {'sent' if ok else 'not sent'} ({info})")
        return {"ok": ok, "info": info}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        hub.clients.add(websocket)
        await websocket.send_text(json.dumps({"type": "board", "connected": hub.board_connected}))
        if hub.latest:
            await websocket.send_text(json.dumps(hub.latest, default=to_jsonable, ensure_ascii=False))
        try:
            while True:
                text = await websocket.receive_text()
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("type") == "button":
                    hub.press_button()
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(websocket)

    @app.websocket("/ws/raw")
    async def ws_raw(websocket: WebSocket):
        """Every raw board line as a text frame (for the live viewer and the recorder)."""
        await websocket.accept()
        hub.raw_clients.add(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.raw_clients.discard(websocket)

    dist = args.web_dist
    if dist and os.path.isdir(dist):
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
        log(f"dashboard: serving {dist} at /")
    return app


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description="IBUKI server")
    ap.add_argument("--port", help="board serial port (default: first /dev/cu.usbmodem*)")
    ap.add_argument("--replay", help="play a recording instead of the live board")
    ap.add_argument("--speed", type=float, default=1.0, help="replay speed factor")
    ap.add_argument("--loop", action="store_true", help="loop the replay")
    ap.add_argument("--brain", default="auto", help="'auto', 'placeholder' or module:Class")
    ap.add_argument("--host", default="0.0.0.0", help="0.0.0.0 = phones on the same Wi-Fi can connect")
    ap.add_argument("--http-port", type=int, default=8000)
    ap.add_argument("--web-dist", default=os.path.join(REPO_DIR, "web", "dist"))
    ap.add_argument("--env-file", default=os.path.join(REPO_DIR, ".env"),
                    help="file with IBUKI_NTFY_TOPIC=...; '' to skip")
    return ap.parse_args(argv)


def main():
    args = parse_args()
    # IBUKI_NTFY_TOPIC can live in <repo>/.env (git-ignored). A variable already set wins.
    if args.env_file:
        load_dotenv(args.env_file, override=False)
    app = create_app(args)
    log(f"open http://localhost:{args.http_port}/api/health  (WebSocket ws://localhost:{args.http_port}/ws)")
    uvicorn.run(app, host=args.host, port=args.http_port, log_level="warning")


if __name__ == "__main__":
    main()
