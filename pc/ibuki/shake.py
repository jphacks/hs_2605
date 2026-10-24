"""Board-shake detector from the radar data alone.

When the board itself moves (bumped crib, hand-held, table knocked), every static echo in the
room (walls, furniture, crib bars) changes phase at the same time. A person or baby moving only
changes the few range bins they occupy. So: learn which bins are normally calm, watch only those,
and call it a shake when they all jump together.

While shaking, breathing is UNKNOWN: never read it as breathing, never as "no breathing", and
restart phase tracking afterwards.
"""
from __future__ import annotations

import collections
import warnings

import numpy as np


class ShakeDetector:
    def __init__(self, history: int = 160, min_strength: float = 15.0, ratio: float = 5.0,
                 floor_rad: float = 0.25, hold_lines: int = 17, min_ref_bins: int = 3):
        """history: lines (~10 s) used to learn each bin's normal phase change.
        min_strength: |z| (value units) for a bin to count as a static reflector.
        ratio / floor_rad: shake when the calm bins change > ratio x their normal AND > floor_rad.
        hold_lines: keep reporting the shake this many lines (~1 s) after the last trigger.
        """
        self.history = history
        self.min_strength = min_strength
        self.ratio = ratio
        self.floor_rad = floor_rad
        self.hold_lines = hold_lines
        self.min_ref_bins = min_ref_bins
        self.prev = None
        self.dphi_hist: collections.deque = collections.deque(maxlen=history)
        self.hold = 0
        self.score = 0.0

    def reset(self):
        self.prev = None
        self.dphi_hist.clear()
        self.hold = 0

    def feed(self, z: np.ndarray, use: np.ndarray | None = None) -> bool:
        """z: complex bins of one D line (value units). use: bins to consider. Returns shaking."""
        if use is None:
            use = np.ones(len(z), dtype=bool)
        if self.prev is None or len(self.prev) != len(z):
            self.prev = z
            return False
        strong = use & (np.abs(z) >= self.min_strength) & (np.abs(self.prev) >= self.min_strength)
        dphi = np.abs(np.angle(z * np.conj(self.prev)))
        self.prev = z
        dphi = np.where(strong, dphi, np.nan)

        shaking_now = False
        if len(self.dphi_hist) >= 30:
            with warnings.catch_warnings():  # bins that were never strong are all-NaN: fine
                warnings.simplefilter("ignore", RuntimeWarning)
                normal = np.nanmedian(np.array(self.dphi_hist), axis=0)  # each bin's usual change
            ok = strong & np.isfinite(normal)
            if ok.sum() >= self.min_ref_bins:
                calm_cut = np.nanmedian(normal[ok])
                ref = ok & (normal <= calm_cut)  # the calmer half: static reflectors
                if ref.sum() >= self.min_ref_bins:
                    now = float(np.median(dphi[ref]))
                    usual = max(float(np.median(normal[ref])), 1e-3)
                    self.score = now / usual
                    shaking_now = now > self.floor_rad and self.score > self.ratio
        if shaking_now:
            self.hold = self.hold_lines
        elif self.hold > 0:
            self.hold -= 1
        if not shaking_now and self.hold == 0:
            self.dphi_hist.append(dphi)  # learn only from non-shake lines
        return self.hold > 0
