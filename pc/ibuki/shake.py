"""Board-shake detector from the radar data alone.

When the board itself moves (bumped crib, hand-held, table knocked), every still object in the
room (wall, furniture, crib, mattress) moves by the same amount relative to the radar, so all of
their echoes turn together. A person or baby moving changes only the bins they occupy.

So we only judge with strong, still references: bins whose echo is far above the noise and that
are away from the most active bins (the person). The board moved when those references all turn
together, much more than they normally do.

If the room has no strong still objects (for example an open space behind the board), a person
walking toward or away from the board looks exactly like a board shake. Then we give no verdict
and never flag, instead of guessing. Big movements still make breathing unknown through the motion
check, so nothing unsafe happens.

While shaking, breathing is UNKNOWN: never read it as breathing, never as "no breathing", and
restart phase tracking afterwards.
"""
from __future__ import annotations

import collections
import warnings

import numpy as np


class ShakeDetector:
    def __init__(self, history: int = 160, strength_x_noise: float = 20.0, guard_bins: int = 3,
                 coherence: float = 0.8, ratio: float = 5.0, floor_rad: float = 0.25,
                 hold_lines: int = 17, min_ref_bins: int = 3):
        """history: lines (~10 s) used to learn the noise and each bin's normal change.
        strength_x_noise: a reference must be this many times stronger than the noise.
        guard_bins: bins next to the two most active bins (the person) are never references.
        coherence: how well the references must turn together (1 = exactly together).
        ratio / floor_rad: shake when the references change > ratio x their normal AND > floor_rad.
        hold_lines: keep reporting the shake this many lines (~1 s) after the last trigger.
        """
        self.history = history
        self.strength_x_noise = strength_x_noise
        self.guard_bins = guard_bins
        self.coherence = coherence
        self.ratio = ratio
        self.floor_rad = floor_rad
        self.hold_lines = hold_lines
        self.min_ref_bins = min_ref_bins
        self.prev = None
        self.dphi_hist: collections.deque = collections.deque(maxlen=history)
        self.dz_hist: collections.deque = collections.deque(maxlen=history)
        self.hold = 0
        self.score = 0.0
        self.ref_bins = 0  # how many references the last verdict used (0 = no verdict possible)

    def reset(self):
        self.prev = None
        self.dphi_hist.clear()
        self.dz_hist.clear()
        self.hold = 0

    def _references(self, z, prev, use):
        hist_dz = np.array(self.dz_hist)
        noise = max(float(np.median(np.percentile(hist_dz, 10, axis=0))), 0.5)
        floor = self.strength_x_noise * noise
        strong = use & (np.abs(z) >= floor) & (np.abs(prev) >= floor)
        activity = np.median(hist_dz, axis=0)
        busy = np.argsort(np.where(use, activity, -1.0))[-2:]
        near_busy = np.zeros(len(z), dtype=bool)
        for b in busy:
            near_busy[max(0, b - self.guard_bins):b + self.guard_bins + 1] = True
        return strong & ~near_busy

    def feed(self, z: np.ndarray, use: np.ndarray | None = None) -> bool:
        """z: complex bins of one D line (value units). use: bins to consider. Returns shaking."""
        if use is None:
            use = np.ones(len(z), dtype=bool)
        if self.prev is None or len(self.prev) != len(z):
            self.prev = z
            return False
        prev = self.prev
        self.prev = z
        rot = z * np.conj(prev)
        dphi = np.abs(np.angle(rot))
        dz = np.abs(z - prev)

        shaking_now = False
        self.ref_bins = 0
        if len(self.dphi_hist) >= 30:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                normal = np.nanmedian(np.array(self.dphi_hist), axis=0)  # each bin's usual change
            ref = self._references(z, prev, use) & np.isfinite(normal)
            self.ref_bins = int(ref.sum())
            if self.ref_bins >= self.min_ref_bins:
                now = float(np.median(dphi[ref]))
                usual = max(float(np.median(normal[ref])), 1e-3)
                self.score = now / usual
                unit = rot[ref] / np.maximum(np.abs(rot[ref]), 1e-12)
                weight = np.abs(z[ref]) * np.abs(prev[ref])
                together = abs(np.sum(weight * unit) / np.sum(weight)) >= self.coherence
                shaking_now = now > self.floor_rad and self.score > self.ratio and together
        if shaking_now:
            self.hold = self.hold_lines
        elif self.hold > 0:
            self.hold -= 1
        if not shaking_now and self.hold == 0:  # learn only from non-shake lines
            self.dphi_hist.append(dphi)
            self.dz_hist.append(dz)
        return self.hold > 0
