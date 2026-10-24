"""Placeholder Brain: same output shape as the team's Brain, so the server and dashboard can run now.

It finds the most active range bin, removes that bin's static echo, and turns the phase into a
displacement wave (mm). It NEVER reports a breathing rate (bpm is always None) and NEVER raises
alerts. "state" is only EMPTY (nothing moving in range) or OK (someone there). Every status carries
"source": "placeholder" so nobody mistakes it for the real breathing check, and "shaking": true
while the board itself moves (then disp_mm is empty: breathing unknown).

Replace it by passing --brain module:Class to the server once the real Brain exists.
"""
from __future__ import annotations

import collections
import math

import numpy as np

from .serial_reader import Config, Data, Status, parse_line
from .shake import ShakeDetector

LAMBDA_MM = 5.0  # 60 GHz wavelength, ~4.96 mm


class PlaceholderBrain:
    name = "placeholder"

    def __init__(self, min_cm: float = 28.0, window_s: float = 20.0, profile_s: float = 3.0,
                 repick_s: float = 5.0, present_level: float = 15.0, status_every: int = 2,
                 highpass_s: float = 8.0):
        self.cfg = Config(15.0, 37.5, 24, 10.0)
        self.min_cm = min_cm
        self.window_s = window_s
        self.profile_s = profile_s
        self.repick_s = repick_s
        self.present_level = present_level  # activity (|z - mean|, value units) meaning "someone"
        self.status_every = status_every
        self.highpass_s = highpass_s
        self.hist: collections.deque = collections.deque()  # (t_s, z)
        self.bin = None
        self.bin_picked_t = -math.inf
        self.prev_phase = None
        self.unwrapped = 0.0
        self.slow = None
        self.pending: list[float] = []
        self.motion_acc: list[float] = []
        self.count = 0
        self.present = False
        self.shake = ShakeDetector()
        self.was_shaking = False
        self.max_gap_s = 0.5  # a t_ms jump bigger than this breaks phase continuity
        self.last_t = None    # board time of the previous D line (also during shakes)

    # ------------------------------------------------------------------ helpers
    def _use(self, n: int) -> np.ndarray:
        rng = self.cfg.first_cm + np.arange(n) * self.cfg.step_mm / 10.0
        return rng >= self.min_cm

    def _activity(self, seconds: float) -> np.ndarray:
        t_now = self.hist[-1][0]
        zs = np.array([z for t, z in self.hist if t >= t_now - seconds])
        return np.abs(zs - zs.mean(axis=0)).mean(axis=0)

    def _reset_phase(self):
        self.prev_phase = None
        self.unwrapped = 0.0
        self.slow = None

    # ------------------------------------------------------------------ main entry
    def feed(self, line: str) -> list[dict]:
        m = parse_line(line)
        if isinstance(m, Config):
            self.cfg = m
            return []
        if isinstance(m, Status) and "radar restart" in m.text:
            self._relearn()  # the radar FIFO was cleared: phase continuity is broken
            self.shake.reset()
            return []
        if not isinstance(m, Data):
            return []

        t = m.t_ms / 1000.0
        z = m.z(self.cfg)
        if self.last_t is not None and (t < self.last_t or t - self.last_t > self.max_gap_s):
            self._relearn()  # board rebooted, radar restarted, or lines lost: start fresh
            self.shake.reset()
        self.last_t = t
        use = self._use(len(z))
        motion = 0.0
        if self.hist and len(self.hist[-1][1]) == len(z):
            motion = float(np.abs(z[use] - self.hist[-1][1][use]).sum())

        if self.shake.feed(z, use):
            # The board itself is moving: breathing is unknown. Send no wave samples (never a
            # fake flat line), keep the last presence, and re-learn the room when it stops.
            self.was_shaking = True
            self.motion_acc.append(motion)
            self.count += 1
            return [] if self.count % self.status_every else [self._status(m.t_ms, True)]
        if self.was_shaking:
            self.was_shaking = False
            self._relearn()  # the board may rest in a new position: old echoes are wrong now

        self.hist.append((t, z))
        while self.hist and self.hist[0][0] < t - self.window_s:
            self.hist.popleft()
        self.motion_acc.append(motion)

        n = len(z)
        learned = self.hist[-1][0] - self.hist[0][0] >= self.profile_s  # 3 s seen since (re)start
        if learned and (self.bin is None or self.bin >= n or t - self.bin_picked_t >= self.repick_s):
            act = np.where(self._use(n), self._activity(self.profile_s), -1.0)
            new_bin = int(np.argmax(act))
            if new_bin != self.bin:
                self._reset_phase()
            self.bin = new_bin
            self.bin_picked_t = t

        if self.bin is not None:
            b = self.bin
            clutter = np.mean([zz[b] for _, zz in self.hist])
            phase = math.atan2((z[b] - clutter).imag, (z[b] - clutter).real)
            if self.prev_phase is not None:
                d = phase - self.prev_phase
                d = (d + math.pi) % (2 * math.pi) - math.pi
                self.unwrapped += d
            self.prev_phase = phase
            mm = self.unwrapped * LAMBDA_MM / (4 * math.pi)
            dt = (t - self.hist[-2][0]) if len(self.hist) > 1 else 0.06
            alpha = min(1.0, dt / self.highpass_s)
            self.slow = mm if self.slow is None else self.slow + alpha * (mm - self.slow)
            self.pending.append(round(mm - self.slow, 3))

        self.count += 1
        if self.count % self.status_every:
            return []
        if learned and self.bin is not None:
            self.present = float(self._activity(self.profile_s)[self.bin]) >= self.present_level
        return [self._status(m.t_ms, False)]

    def _relearn(self):
        self.hist.clear()
        self.bin = None
        self._reset_phase()

    def _status(self, t_ms: int, shaking: bool) -> dict:
        chest = (None if self.bin is None else
                 round(float(self.cfg.first_cm + self.bin * self.cfg.step_mm / 10.0), 2))
        status = {
            "t": t_ms,
            "state": "OK" if self.present else "EMPTY",
            "bpm": None,
            "disp_mm": self.pending,
            "motion": round(float(np.mean(self.motion_acc)) if self.motion_acc else 0.0, 2),
            "present": bool(self.present),
            "chest_cm": chest,
            "shaking": shaking,
            "source": self.name,
        }
        self.pending = []
        self.motion_acc = []
        return status
