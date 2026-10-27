"""The trained models (ml/) deciding the baby's state, in place of the hand-written Brain rules.

    ./ibuki.command --brain ibuki.model_brain:Brain

The rule-based Brain (brain.py) still runs underneath for what the dashboard draws (the
breathing wave, the rate, the chest distance). The models decide the state and the alarms:
every 8 lines (~0.5 s) the breathing model answers "is something breathing here?" for all 24
distances, and the state model reads the last 30 s of those answers and says breathing /
stopped / moving / empty. The only rules left are the alarm's (as in ml/state_model.py):
"stopped" without a break for no_breath_s is no_breath; "empty" for 5 s after someone was there
is left_bed.

Needs the exported models in ml/data/models/ (../ml-env/bin/python -m ml.runtime --export).
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the repo root, for ml/

from ml.features import NUM_BINS, SCALE, WINDOW, window_features  # noqa: E402
from ml.runtime import Model  # noqa: E402

from .brain import Brain as RuleBrain  # noqa: E402

# the same settings as ml/state_model.py, where they were chosen on the validation set
HOP = 8
SEQ = 60
LAG_S = 6.0
LEFT_S = 5.0
SHOWN = {0: "STILL", 1: "CHECK", 2: "OK", 3: "EMPTY"}  # breathing, stopped, moving, empty


class Brain(RuleBrain):
    name = "state_model"

    def __init__(self, no_breath_s: float | None = None, min_cm: float = 15.0):
        super().__init__(no_breath_s, min_cm)
        self.breath_model = Model("breath_slot")
        self.state_model = Model("state")
        self._clear()

    def _clear(self):
        self.m_lines = deque(maxlen=WINDOW)   # the last 12 s of complex slot values
        self.m_changes = deque(maxlen=17)     # echo-strength change per line, the last ~1 s
        self.m_steps = deque(maxlen=SEQ)      # the state model's inputs, the last 30 s
        self.m_count = 0
        self.m_state = None
        self.m_stopped_since = None
        self.m_empty_since = None
        self.m_present = False
        self.m_alerted = False

    def _step(self, t: float) -> list[dict]:
        """Both models on the last lines, then the alarm rules. Same inputs as ml/state_model.py."""
        z = np.array(self.m_lines)
        p = self.breath_model.run(window_features(z))[:, 0]
        last3 = z[-50:]
        move = np.abs(last3 - last3.mean(axis=0)).mean(axis=0)
        chg = np.mean(self.m_changes, axis=0)
        level = np.abs(last3.mean(axis=0))
        self.m_steps.append(np.stack([p, np.log10(1.0 + move) / 3.0, np.log10(1.0 + chg) / 2.0,
                                    np.log10(1.0 + level) / 3.0], axis=-1).astype(np.float32))
        seq = np.zeros((SEQ, NUM_BINS, 4), np.float32)
        seq[SEQ - len(self.m_steps):] = np.array(self.m_steps)
        s = int(self.state_model.run(seq[None])[0].argmax())
        self.m_state = s
        events = []
        if s == 3:
            self.m_empty_since = t if self.m_empty_since is None else self.m_empty_since
            if self.m_present and t - self.m_empty_since >= LEFT_S:
                events.append({"type": "alert", "reason": "left_bed", "since_s": 0})
                self.m_present = False
        else:
            self.m_empty_since = None
            self.m_present = True
        if s != 1:
            self.m_stopped_since, self.m_alerted = None, False
        else:
            if self.m_stopped_since is None:
                self.m_stopped_since = t - LAG_S
            snoozed = t < (getattr(self, "snooze_until", None) or 0)
            if not self.m_alerted and not snoozed and t - self.m_stopped_since >= self.no_breath_s:
                self.m_alerted = True
                events.append({"type": "alert", "reason": "no_breath",
                               "since_s": int(round(t - self.m_stopped_since))})
        return events

    def feed(self, line: str) -> list[dict]:
        out = super().feed(line)
        p = line.split(",")
        events = []
        if p[0] == "B":
            self.m_stopped_since, self.m_alerted = None, False  # acknowledged, like the Brain
        elif p[0] == "S" and "radar restart" in line:
            self._clear()
        elif p[0] == "D" and len(p) == 3 + 2 * NUM_BINS:
            v = np.array(p[3:], float) / SCALE
            z = v[0::2] + 1j * v[1::2]
            self.m_changes.append(np.abs(np.abs(z) - np.abs(self.m_lines[-1])) if self.m_lines else np.zeros(NUM_BINS))
            self.m_lines.append(z)
            self.m_count += 1
            if len(self.m_lines) == WINDOW and self.m_count % HOP == 0:
                events = self._step(int(p[2]) / 1000.0)
        # the models decide: their state replaces the rules' state, and the rules' own
        # no_breath / left_bed alerts are dropped (info events such as woke_up stay)
        kept = []
        for m in out:
            if m.get("type") == "alert" and m.get("reason") in ("no_breath", "left_bed"):
                continue
            if "state" in m and self.m_state is not None:
                m = {**m, "state": "ALERT" if self.m_alerted else SHOWN[self.m_state]}
            kept.append(m)
        return kept + events
