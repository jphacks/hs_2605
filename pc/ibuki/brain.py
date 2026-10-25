"""IBUKI Brain: turns board lines into states, breathing rate and alerts.

States (see pc/README.md):
  EMPTY  nobody in bed
  OK     in bed and moving (awake); breathing can't be read while moving
  STILL  in bed, still, breathing found (asleep)
  CHECK  in bed, still, no breathing found for a few seconds (counting)
  ALERT  no breathing for NO_BREATH_S seconds
Events: alert no_breath / left_bed, info woke_up / fast_breathing.

How it works, per D line (~16.6/s):
  presence  how much the echoes change over 3 s, compared with an empty room
  leaving   when it goes quiet, is the person's echo gone? 3 s after going quiet, leaving left
            2-6% of their usual echo in our recordings; with someone still there it never fell
            below 59%. So a quiet room with their echo still there is "in bed, maybe not
            breathing", even right after moving
  movement  how much the echo STRENGTHS change within 22 cm of the chest (the person's body),
            relative to the person's echo. Breathing moves the chest a few mm and mostly turns
            the echo's phase; moving the body changes the strengths a lot. A hand by the board
            or someone walking past further away is not the sleeper moving.
  chest     the person's 3 strongest echo slots (re-picked every 3 s), from 28 cm (closer is the
            board's own leakage, or a hand by the board) and only where the echo changes
            smoothly like a breathing chest, not jumping like someone moving. Weak slots are
            left out: their phase is mostly noise
  breathing each slot's phase -> mm, band-passed 0.1-1.5 Hz (adults and babies). Each slot is
            compared with its own normal breathing size, and the middle of the 3 ratios counts.
            A clean breath-hold drops to 12-15% at the strongest slots. Natural breathing after
            deep breaths can be much smaller (Manami: 16-30%), so "normal" is only the last 15 s.
            It is forgotten when the person leaves: the next one is compared with their own
  rate      FFT peak over 20 s, checked against counted breaths; None if they disagree
  shake     the board itself moved (shake.py): breathing unknown, never an alarm

Thresholds were set from Ramadhan's 8 recordings on 2026-10-24 (seated, 20-70 cm) and checked
on the 24 recordings of 3 people (ramadhan, lemonwasp, manami). Not a medical device.
"""
from __future__ import annotations

import collections
import math
import os

import numpy as np
from scipy.signal import butter, filtfilt

from .serial_reader import Button, Config, Data, Status, parse_line
from .shake import ShakeDetector

LAMBDA_MM = 5.0

# --- thresholds (from the recordings; see the docstring) ---------------------------------
PRESENT_ON = 25.0       # 3 s echo activity; empty room 1-5, a person 100+
PRESENT_OFF = 15.0
ABSENT_S = 3.0          # seconds below PRESENT_OFF before EMPTY
GONE_RATIO = 0.20       # echo at their 3 strongest slots vs its 30 s average: left 0.02-0.06, there 0.59+
ECHO_MEMORY_S = 30.0    # the person's echo is averaged over ~30 s, so walking away barely moves it
LEAK_CM = 28.0          # under this, the board's own TX->RX leakage, not a person
JUMP_MAX = 1.0          # line-to-line echo change / its change over 10 s: breathing chests 0.15-0.87
                        # (99%), someone moving 1.12-1.48, empty-room noise ~1.34
MOVE_ON = 1.6           # relative strength change; breathing 0.7-1.0, moving ~2.6
MOVE_OFF = 1.25
MOVE_SPAN_SLOTS = 6     # movement counts within 6 slots (22 cm) of the chest: the person's body.
                        # 3 slots missed a person settling in, so "normal" was learned too big
MIN_LEVEL = 100.0      # a real person's echo level is 125-440; room noise motion ~12
NO_BREATH_RATIO = 0.30  # wave size vs this person's normal; clean holds drop to 0.12-0.25
BREATH_OK_RATIO = 0.40  # back to "breathing" (hysteresis); also when normal size is learned
NORM_S = 15.0           # "normal" = the middle of the last 15 s of clear breathing, so deep breaths
                        # are forgotten soon (30 s gave Manami a false alert after paced breathing)
RATE_RATIO = 0.35       # show a breathing rate only above this
CHECK_AFTER_S = 0.0     # show CHECK as soon as no breathing is seen
WINDOW_LAG_S = 3.0      # the 6 s size window notices a hold ~3 s late: count from there
WOKE_UP_AFTER_STILL_S = 600.0  # "woke up" only after 10 min asleep, not after every short rest
LEFT_BED_AFTER_PRESENT_S = 10.0
FAST_BREATHING_BPM = 50.0   # WHO "fast breathing" for 2-12 months
FAST_BREATHING_FOR_S = 300.0
SNOOZE_AFTER_BUTTON_S = 30.0


class Brain:
    name = "brain"

    def __init__(self, no_breath_s: float | None = None, min_cm: float = 15.0):
        self.no_breath_s = float(no_breath_s or os.environ.get("IBUKI_NO_BREATH_S", 10.0))
        self.min_cm = min_cm
        self.cfg = Config(15.0, 37.5, 24, 10.0)
        self.fs = 16.64
        self.hist: collections.deque = collections.deque(maxlen=int(20 * 16.64))  # (t, z)
        self.shake = ShakeDetector()
        self._filters()
        self.reset_tracking()
        self.state = "EMPTY"
        self.present = False
        self.moving = False
        self.absent_since = None
        self.present_since = None
        self.still_since = None
        self.no_breath_since = None
        self.fast_since = None
        self.snooze_until = -math.inf
        self.alerted = False
        self.left_bed_sent = False
        # the echo strength per slot while someone is there, averaged over ~30 s. Kept through
        # reset_tracking() (a bump must not make them "leave"); cleared when they leave.
        self.person_echo = None
        self.echo_now = None  # echo strength per slot, smoothed over ~0.5 s
        self.act_hist: collections.deque = collections.deque(maxlen=int(5 * 16.64))
        self.was_shaking = False
        self.norms: dict = {}  # (slot, 'phase'|'echo') -> recent normal sizes
        self.slots: list[int] = []  # the person's strongest slots
        self.ratio = None
        self.bpm = None
        self.pending: list[float] = []
        self.motion_acc: list[float] = []
        self.count = 0
        self.last_t = None
        self.dt_ms = 60.0
        self.events: list[dict] = []

    # ------------------------------------------------------------------ helpers
    def _filters(self):
        nyq = self.fs / 2
        self.b, self.a = butter(2, [0.1 / nyq, 1.5 / nyq], btype="band")

    def reset_tracking(self):
        self.hist = collections.deque(maxlen=int(20 * self.fs))
        self.chest = None
        self.slots = []
        self.ratio = None
        self.chest_picked_t = -math.inf
        self.shake.reset()

    def _cm(self, n):
        return self.cfg.first_cm + np.arange(n) * self.cfg.step_mm / 10.0

    def _use(self, n):
        return self._cm(n) >= self.min_cm

    def _window(self, seconds):
        t_now = self.hist[-1][0]
        rows = [(t, z) for t, z in self.hist if t >= t_now - seconds]
        return np.array([t for t, _ in rows]), np.array([z for _, z in rows])

    def _bandpass(self, x):
        if len(x) < 3 * max(len(self.a), len(self.b)) + 1:
            return x - np.mean(x)
        return filtfilt(self.b, self.a, x - np.mean(x), axis=0)

    def _pick_chest(self, t):
        T, Z = self._window(10.0)
        if len(T) < 5 * self.fs:
            return
        # not under LEAK_CM: a hand or a laptop right next to the board changes those echoes far
        # more than a chest 50 cm away (in the presentation the "chest" ended up at 22.5 cm)
        use = self._use(Z.shape[1]) & (self._cm(Z.shape[1]) >= LEAK_CM)
        change = np.abs(Z - Z.mean(axis=0)).mean(axis=0)
        # and not where the echo jumps from one line to the next: someone walking past or a hand
        # moving changes it far more than a breathing chest, but not smoothly
        jumpy = np.abs(np.diff(Z, axis=0)).mean(axis=0) > JUMP_MAX * change
        activity = np.where(use & ~jumpy, change, -1.0)
        order = [int(k) for k in np.argsort(activity)[::-1][:3] if activity[k] >= PRESENT_OFF]
        if not order:
            return
        if self.chest is None or activity[order[0]] > 1.3 * activity[self.chest]:
            self.chest = order[0]
        self.slots = order
        self.chest_picked_t = t

    def _breath_wave(self, seconds, slot=None):
        # The still echo is estimated over the whole 20 s history. A short window would be
        # wrong during a breath-hold: subtracting a 6 s average from an almost unchanged
        # signal leaves only noise, whose random phase looks like big movement.
        T, Z = self._window(20.0)
        z = Z[:, self.chest if slot is None else slot]
        mm = np.unwrap(np.angle(z - z.mean())) * LAMBDA_MM / (4 * np.pi)
        y = self._bandpass(mm)
        keep = T >= T[-1] - seconds
        return T[keep], y[keep]

    def _breath_ratio(self, learn: bool):
        """Middle of (breathing now / that slot's normal) over the strongest slots.

        Two sizes per slot, and the smaller ratio counts:
          phase size  the chest movement in mm. Drops during a breath-hold even when the body
                      sways a little (sway mostly changes the echo strength).
          echo size   how much the echo itself changes. Collapses when the person is perfectly
                      still; then the phase is just noise and would look like breathing.
        """
        T, Z = self._window(20.0)
        recent = T >= T[-1] - 6.0
        ratios = []
        for k in self.slots:
            zc = Z[:, k] - Z[:, k].mean()
            y = self._bandpass(np.unwrap(np.angle(zc)) * LAMBDA_MM / (4 * np.pi))
            sizes = (float(np.std(y[recent])), float(np.std(np.abs(self._bandpass(zc)[recent]))))
            slot_ratios = []
            for name, size in zip(("phase", "echo"), sizes):
                norm = self.norms.setdefault((k, name), collections.deque(maxlen=int(NORM_S * self.fs / 2)))
                if len(norm) >= 30:
                    slot_ratios.append(size / max(float(np.median(norm)), 1e-6))
                if learn:
                    norm.append(size)
            if slot_ratios:
                ratios.append(min(slot_ratios))
        return float(np.median(ratios)) if ratios else None

    def _rate(self, T, y):
        if len(y) < 10 * self.fs:
            return None
        p = np.abs(np.fft.rfft((y - y.mean()) * np.hanning(len(y)), n=4096)) ** 2
        f = np.fft.rfftfreq(4096, 1 / self.fs) * 60.0
        band = (f >= 6) & (f <= 90)
        fft_bpm = float(f[band][np.argmax(p[band])])
        ups = int(np.sum((y[:-1] < 0) & (y[1:] >= 0)))
        counted = ups / (T[-1] - T[0]) * 60.0
        if counted <= 0 or abs(counted - fft_bpm) / fft_bpm > 0.25:
            return None  # the two methods disagree: say unknown, never guess
        return round(fft_bpm, 1)

    # ------------------------------------------------------------------ main entry
    def feed(self, line: str) -> list[dict]:
        m = parse_line(line)
        if isinstance(m, Config):
            self.cfg = m
            return []
        if isinstance(m, Button):
            # no extra status here: it would repeat the last time with no wave, and the dashboard
            # takes that as a restart and starts its graph from zero. The next line shows the change
            self._button()
            return self._take_events()
        if isinstance(m, Status) and "radar restart" in m.text:
            self.reset_tracking()
            return []
        if not isinstance(m, Data):
            return []

        t = m.t_ms / 1000.0
        z = m.z(self.cfg)
        if self.last_t is not None and (t < self.last_t or t - self.last_t > 0.5):
            self.reset_tracking()  # board rebooted, radar restarted or lines lost
        elif self.last_t is not None:
            self.dt_ms = round(0.95 * self.dt_ms + 0.05 * (t - self.last_t) * 1000.0, 1)
        self.last_t = t
        use = self._use(len(z))
        shaking = self.shake.feed(z, (self.cfg.first_cm + np.arange(len(z)) * self.cfg.step_mm / 10) >= 28)
        if self.was_shaking and not shaking:
            self.reset_tracking()  # the board may rest in a new position: re-learn the room
        self.was_shaking = shaking
        prev = self.hist[-1][1] if self.hist else None
        self.hist.append((t, z))

        # presence: echo activity over 3 s
        T3, Z3 = self._window(3.0)
        act = np.abs(Z3 - Z3.mean(axis=0)).mean(axis=0)
        activity = float(np.max(np.where(use, act, 0.0)))
        mag = np.abs(z)
        self.echo_now = mag if self.echo_now is None else self.echo_now + 0.12 * (mag - self.echo_now)
        if self.present and not shaking:
            k = 1.0 / (ECHO_MEMORY_S * self.fs)
            self.person_echo = (self.echo_now.copy() if self.person_echo is None
                                else self.person_echo + k * (self.echo_now - self.person_echo))
        self._presence(t, activity)

        # movement: echo-strength change relative to the person's echo, only near their chest:
        # a hand by the board or someone walking past must not make the sleeper look awake
        near = use if self.chest is None else use & (np.abs(np.arange(len(z)) - self.chest) <= MOVE_SPAN_SLOTS)
        if prev is not None and activity > 0:
            mag_change = float(np.abs(np.abs(z[near]) - np.abs(prev[near])).sum())
            self.motion_acc.append(mag_change)
        self.act_hist.append(float(np.max(np.where(near, act, 0.0))))
        # the person's echo level over the last 5 s; never below MIN_LEVEL, so the room's own
        # noise can't look like movement when a person lies perfectly still
        level = max(max(self.act_hist), MIN_LEVEL)
        rel = float(np.mean(self.motion_acc[-17:])) / level if self.motion_acc else 0.0
        if self.moving:
            self.moving = rel > MOVE_OFF
        else:
            self.moving = rel > MOVE_ON

        # chest and breathing
        if self.present and (self.chest is None or t - self.chest_picked_t >= 3.0):
            self._pick_chest(t)
        ratio = None
        if self.present and self.chest is not None and len(self.hist) > 6 * self.fs and not shaking:
            # learn "normal" only while still and clearly breathing (or before we know)
            learn = (not self.moving and self.no_breath_since is None and
                     (self.ratio is None or self.ratio >= BREATH_OK_RATIO))
            ratio = self._breath_ratio(learn) if self.count % 2 == 0 else self.ratio
            T20, y20 = self._breath_wave(20.0)
            if self.count % 8 == 0:  # the rate changes slowly: ~2 updates per second
                self.bpm = (self._rate(T20, y20)
                            if (not self.moving and ratio is not None and ratio >= RATE_RATIO)
                            else None)
            self.pending.append(round(float(y20[-1]), 3))
        else:
            self.bpm = None
        self.ratio = ratio

        self._update_state(t, shaking, ratio)
        self.count += 1
        if self.count % 2:
            return self._take_events()
        out = [self._status(m.t_ms, shaking)] + self._take_events()
        return out

    # ------------------------------------------------------------------ state machine
    def _presence(self, t, activity):
        if activity >= PRESENT_ON:
            if not self.present:
                self.present_since = t
            self.present = True
            self.absent_since = None
        elif activity < PRESENT_OFF:
            if self.absent_since is None:
                self.absent_since = t
            if self.present and t - self.absent_since >= ABSENT_S:
                if not self._echo_gone():
                    return  # quiet, but their echo is still there: in bed, maybe not breathing
                self._leave(t)

    def _leave(self, t):
        was_settled = self.present_since is not None and t - self.present_since >= LEFT_BED_AFTER_PRESENT_S
        self.present = False
        self.person_echo = None
        self.norms = {}  # the next person breathes differently: learn their normal afresh
        self.reset_tracking()
        if was_settled and not self.left_bed_sent:
            self.events.append({"type": "alert", "reason": "left_bed", "since_s": 0})
            self.left_bed_sent = True

    def _echo_gone(self) -> bool:
        """Has the echo gone from the slots where it is usually strongest while someone is there?
        Up to 3 slots, each at least a quarter of the strongest: the person, not the room."""
        if self.person_echo is None:
            return True
        cm = self.cfg.first_cm + np.arange(len(self.person_echo)) * self.cfg.step_mm / 10.0
        echo = np.where(cm >= LEAK_CM, self.person_echo, -1.0)
        top = [k for k in np.argsort(echo)[::-1][:3] if echo[k] >= echo.max() / 4]
        return float(self.echo_now[top].sum()) < GONE_RATIO * float(self.person_echo[top].sum())

    def _update_state(self, t, shaking, ratio):
        if not self.present:
            self._set("EMPTY", t)
            self.no_breath_since = None
            return
        self.left_bed_sent = False
        if shaking:
            self.no_breath_since = None  # unknown: never count a shake as no breathing
            return
        if self.moving:
            if self.state in ("STILL",) and self.still_since is not None and \
                    t - self.still_since >= WOKE_UP_AFTER_STILL_S:
                self.events.append({"type": "info", "reason": "woke_up", "since_s": 0})
            self.no_breath_since = None
            self._set("OK", t)
            return
        if ratio is None or t < self.snooze_until:
            if self.state in ("EMPTY", "OK"):
                self._set("STILL", t)
            return
        if ratio >= BREATH_OK_RATIO:
            self.no_breath_since = None
            self.alerted = False
            self._set("STILL", t)
            self._fast(t)
            return
        if ratio < NO_BREATH_RATIO:
            if self._echo_gone():
                # Walking off slowly: breathing vanishes before the room is quiet. Their echo is
                # gone too, so they left. Never the no-breathing alarm (and its siren) for that.
                self._leave(t)
                self._set("EMPTY", t)
                self.no_breath_since = None
                return
            if self.no_breath_since is None:
                self.no_breath_since = t - WINDOW_LAG_S
            gone = t - self.no_breath_since
            if gone >= self.no_breath_s:
                if not self.alerted:
                    self.events.append({"type": "alert", "reason": "no_breath",
                                        "since_s": int(round(gone))})
                    self.alerted = True
                self._set("ALERT", t)
            elif gone >= CHECK_AFTER_S:
                self._set("CHECK", t)

    def _fast(self, t):
        if self.bpm is not None and self.bpm >= FAST_BREATHING_BPM:
            if self.fast_since is None:
                self.fast_since = t
            elif t - self.fast_since >= FAST_BREATHING_FOR_S:
                self.events.append({"type": "info", "reason": "fast_breathing", "bpm": self.bpm,
                                    "since_s": int(t - self.fast_since)})
                self.fast_since = t + 3600  # at most once an hour
        elif self.bpm is not None:
            self.fast_since = None

    def _set(self, state, t):
        if state != self.state:
            if state == "STILL":
                self.still_since = t
            self.state = state

    def _button(self):
        """A parent pressed the button (or the dashboard's cancel): acknowledge and snooze."""
        if self.state in ("CHECK", "ALERT"):
            self.state = "OK"
        self.no_breath_since = None
        self.alerted = False
        self.snooze_until = (self.last_t or 0) + SNOOZE_AFTER_BUTTON_S

    def _take_events(self):
        ev, self.events = self.events, []
        return ev

    def _status(self, t_ms, shaking=False):
        chest = None if self.chest is None else round(float(self.cfg.first_cm + self.chest * self.cfg.step_mm / 10.0), 2)
        motion = round(float(np.mean(self.motion_acc[-17:])), 2) if self.motion_acc else 0.0
        if len(self.motion_acc) > 200:
            self.motion_acc = self.motion_acc[-100:]
        status = {
            "t": int(t_ms),
            "state": self.state,
            "bpm": self.bpm,
            "disp_mm": [] if shaking else self.pending,
            "dt_ms": self.dt_ms,
            "motion": motion,
            "present": bool(self.present),
            "chest_cm": chest,
            "shaking": bool(shaking),
            "source": self.name,
        }
        self.pending = []
        return status
