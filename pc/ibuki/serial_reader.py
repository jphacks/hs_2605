"""Board line protocol: parse C/D/S/B lines, track seq gaps, read from the board or a recording.

Protocol (see firmware/README.md), one message per line, comma-separated:
  C,first_cm,step_mm,n,scale      config, at boot and every 5 s
  D,seq,t_ms,I0,Q0,...            ~16.6/s, complex range bins nearest first, value = int / scale
  S,t_ms,text                     status / errors
  B,1                             user button
  L,1 / L,0                       PC -> board: red LED on / off
Any other line is ignored.
"""
from __future__ import annotations

import glob
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Union

import numpy as np

BAUD = 115200


@dataclass
class Config:
    first_cm: float
    step_mm: float
    n: int
    scale: float

    def ranges_cm(self) -> np.ndarray:
        return self.first_cm + np.arange(self.n) * self.step_mm / 10.0


@dataclass
class Data:
    seq: int
    t_ms: int
    raw: np.ndarray  # complex, integer units; divide by Config.scale

    def z(self, cfg: Config) -> np.ndarray:
        return self.raw / cfg.scale


@dataclass
class Status:
    t_ms: int
    text: str


@dataclass
class Button:
    pass


Message = Union[Config, Data, Status, Button]


def parse_line(line: str) -> Optional[Message]:
    """Parse one board line. Returns None for anything that is not a valid protocol line."""
    p = line.strip().split(",")
    try:
        kind = p[0]
        if kind == "D" and len(p) >= 5 and (len(p) - 3) % 2 == 0:
            v = np.array(p[3:], dtype=np.int64)
            return Data(int(p[1]), int(p[2]), v[0::2] + 1j * v[1::2])
        if kind == "C" and len(p) == 5:
            return Config(float(p[1]), float(p[2]), int(p[3]), float(p[4]))
        if kind == "S" and len(p) >= 3:
            return Status(int(p[1]), ",".join(p[2:]))
        if kind == "B" and len(p) == 2 and p[1] == "1":
            return Button()
    except ValueError:
        return None
    return None


class SeqTracker:
    """Counts lost D lines from the seq field. A backwards jump means the board restarted."""

    def __init__(self):
        self.last: Optional[int] = None
        self.lost = 0
        self.gaps = 0
        self.resets = 0

    def feed(self, seq: int) -> int:
        """Returns lines lost before this one (0 if none, -1 if the board restarted)."""
        result = 0
        if self.last is not None:
            if seq <= self.last:
                self.resets += 1
                result = -1
            elif seq != self.last + 1:
                result = seq - self.last - 1
                self.lost += result
                self.gaps += 1
        self.last = seq
        return result


def find_port() -> Optional[str]:
    ports = sorted(glob.glob("/dev/cu.usbmodem*"))
    return ports[0] if ports else None


class BoardLink:
    """Reads board lines in a background thread and reconnects by itself after an unplug.

    on_line(text) is called for every received line; on_state(connected: bool) on changes.
    """

    def __init__(self, port: Optional[str] = None,
                 on_line: Callable[[str], None] = lambda s: None,
                 on_state: Callable[[bool], None] = lambda c: None):
        self.port = port
        self.on_line = on_line
        self.on_state = on_state
        self.connected = False
        self._ser = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def send(self, text: str) -> bool:
        with self._lock:
            if self._ser is None:
                return False
            try:
                self._ser.write((text + "\n").encode("ascii"))
                return True
            except Exception:
                return False

    def _set_connected(self, value: bool):
        if value != self.connected:
            self.connected = value
            self.on_state(value)

    def _run(self):
        import serial

        while not self._stop.is_set():
            port = self.port or find_port()
            if port is None:
                time.sleep(1.0)
                continue
            try:
                ser = serial.Serial(port, BAUD, timeout=0.2)
            except Exception:
                time.sleep(1.0)
                continue
            with self._lock:
                self._ser = ser
            self._set_connected(True)
            rest = b""
            try:
                while not self._stop.is_set():
                    rest += ser.read(4096)
                    *lines, rest = rest.split(b"\n")
                    for ln in lines:
                        self.on_line(ln.decode("ascii", "replace").rstrip("\r"))
            except Exception:
                pass  # unplugged or port error: reconnect below
            with self._lock:
                self._ser = None
            try:
                ser.close()
            except Exception:
                pass
            self._set_connected(False)
            time.sleep(1.0)


class FileReplay:
    """Plays a recording (one board line per row) like a live board, optionally faster or looped.

    Lines sent with send() are only logged, so the rest of the system runs without hardware.
    """

    def __init__(self, path: str, on_line: Callable[[str], None] = lambda s: None,
                 on_state: Callable[[bool], None] = lambda c: None,
                 speed: float = 1.0, loop: bool = False):
        self.path = path
        self.on_line = on_line
        self.on_state = on_state
        self.speed = speed
        self.loop = loop
        self.connected = False
        self.sent: list[str] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def send(self, text: str) -> bool:
        self.sent.append(text)
        print(f"[replay] would send to board: {text}")
        return True

    def _run(self):
        self.connected = True
        self.on_state(True)
        while not self._stop.is_set():
            t0_wall, t0_board = time.time(), None
            with open(self.path, encoding="ascii", errors="replace") as f:
                for ln in f:
                    if self._stop.is_set():
                        break
                    if ln.startswith("D,"):
                        try:
                            t_board = int(ln.split(",")[2]) / 1000.0
                        except (IndexError, ValueError):
                            t_board = None
                        if t_board is not None:
                            t0_board = t_board if t0_board is None else t0_board
                            delay = (t_board - t0_board) / self.speed - (time.time() - t0_wall)
                            if delay > 0:
                                time.sleep(delay)
                    self.on_line(ln.rstrip("\r\n"))
            if not self.loop:
                break
        self.connected = False
        self.on_state(False)
