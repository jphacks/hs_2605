"""Brains for server tests."""


class AlertBrain:
    """Sends OK statuses, CHECK from 20 D lines, then after 30 an alert event and ALERT statuses."""

    name = "alert-test"

    def __init__(self):
        self.d = 0
        self.alerted = False

    def feed(self, line):
        if line.startswith("B,1"):
            self.d = -10_000  # button cancels: back to OK
            self.alerted = False
            return [{"t": 0, "state": "OK", "bpm": None, "disp_mm": [], "motion": 0,
                     "present": True, "chest_cm": 48.75}]
        if not line.startswith("D,"):
            return []
        self.d += 1
        out = []
        if self.d == 30 and not self.alerted:
            self.alerted = True
            out.append({"type": "alert", "reason": "no_breath", "since_s": 10})
        state = "ALERT" if self.alerted else "CHECK" if self.d >= 20 else "OK"
        if self.d % 2 == 0:
            out.append({"t": self.d * 60, "state": state, "bpm": None, "disp_mm": [0.0, 0.0],
                        "motion": 1.0, "present": True, "chest_cm": 48.75})
        return out


class RecoverBrain:
    """No-breathing alert after 20 D lines, then breathing again (STILL) from 40, no button."""

    name = "recover-test"

    def __init__(self):
        self.d = 0

    def feed(self, line):
        if not line.startswith("D,"):
            return []
        self.d += 1
        out = []
        if self.d == 20:
            out.append({"type": "alert", "reason": "no_breath", "since_s": 10})
        if self.d % 2 == 0:
            state = "STILL" if self.d < 20 or self.d >= 40 else "ALERT"
            out.append({"t": self.d * 60, "state": state, "bpm": None, "disp_mm": [0.0, 0.0],
                        "motion": 1.0, "present": True, "chest_cm": 48.75})
        return out


class StoryBrain:
    """Baby story by D-line count: awake, woke-up info, left the bed (alert), back asleep."""

    name = "story-test"

    def __init__(self):
        self.d = 0

    def feed(self, line):
        if not line.startswith("D,"):
            return []
        self.d += 1
        d, out = self.d, []
        if d == 20:
            out.append({"type": "info", "reason": "woke_up"})
        if d == 30:
            out.append({"type": "alert", "reason": "left_bed"})
        if d % 2 == 0:
            state = "OK" if d < 30 else "EMPTY" if d < 60 else "STILL"
            out.append({"t": d * 60, "state": state, "bpm": None, "disp_mm": [], "motion": 1.0,
                        "present": state != "EMPTY", "chest_cm": 48.75})
        return out


class CrashBrain:
    name = "crash-test"

    def feed(self, line):
        raise RuntimeError("boom")
