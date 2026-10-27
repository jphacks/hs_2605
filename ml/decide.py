"""Alarm rules on top of the breathing model, scored side by side with the Brain.

The model (ml/train.py) says every 0.5 s, for every distance slot, how likely it is that
something is breathing there. These rules turn that into what the dashboard shows:
    EMPTY   no baby found yet: nothing has breathed steadily anywhere
    STILL   the baby is breathing
    CHECK   the baby's slot stopped breathing; counting
    ALERT   no breathing for no_breath_s; one no_breath alert
The rules are deliberately few and plain, so they can be read, tested and written in C:

Which slot is the baby? The NEAREST slot that has been breathing steadily (60% of the last 5 s),
moves like a body (MIN_MOVE, REL_MOVE) and is beyond the board's own leakage (MIN_CM). Once found, the
slot is kept even when it goes still: a baby that stopped breathing barely moves.
The board is mounted so the baby is nearer than a parent sleeping beside it; a parent breathing
further away is then never mistaken for the baby, and when the baby stops, the parent's breathing
can't hide it. If a nearer slot starts breathing steadily, that becomes the baby. A body spreads
over neighbouring slots, so the baby counts as breathing if its slot or a neighbour does.

When did breathing stop? The model looks at 12 s windows, so it still sees the last breaths for
a few seconds after a stop. Like the Brain's WINDOW_LAG_S, the count starts LAG_S earlier.

Not in these rules yet: leaving the bed (the Brain's echo rule still does that) and the breathing
rate. The comparison below is about stops and false alarms only.

Run with the AI environment, after ml.train:
    ../ml-env/bin/python -m ml.decide              # test set, thresholds 10 s and 20 s
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from ml import evaluate
from ml.features import NUM_BINS, STEP_CM, read_csv, windows

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "ml" / "data"

HOP = 8              # lines between model runs: ~0.5 s
P_ON = 0.5           # the model's "breathing" threshold
STEADY_N = 10        # the last 10 runs (~5 s)...
STEADY_FRAC = 0.6    # ...of which 60% say "breathing" = steadily breathing
LAG_S = 5.0          # the 12 s window still holds the last breaths for a few seconds after a stop
CONFIRM = (2, 3)     # "breathing again" needs 2 of the last 3 runs: during a stop the model says
                     # "breathing" by mistake in ~7% of runs, and one such blip must not reset
                     # a 20 s count
MIN_CM = 25.0        # never pick a slot nearer than this: the board's own TX->RX leakage lives
                     # in the 15-22.5 cm slots
MIN_MOVE = 10.0      # a body's echo moves at least this much (mean |z - mean| over the last 3 s);
                     # an empty room moves ~3
REL_MOVE = 0.3       # ...and at least 30% as much as the strongest breathing slot in view. The
                     # live "empty" room from the night test drifts by 8-27 at 22-52 cm (a fan or
                     # a curtain?): next to a real baby (30-300) that is never a body. A baby
                     # beside a parent moves about as much as the parent, so it still counts.


class Decider:
    """The rules above, fed one model result (24 probabilities) at a time."""

    def __init__(self, no_breath_s: float):
        self.no_breath_s = no_breath_s
        self.recent = [[] for _ in range(NUM_BINS)]  # last STEADY_N yes/no per slot
        self.baby = None          # the baby's slot, once found
        self.baby_recent = []     # last runs: did the baby's slot (or a neighbour) say "breathing"?
        self.last_breath_t = None
        self.state = "EMPTY"
        self.alerted = False

    def feed(self, t: float, p: np.ndarray, move: np.ndarray) -> list[dict]:
        """t: time (s); p: the model's P(breathing) per slot; move: each slot's echo movement."""
        events = []
        for k in range(NUM_BINS):
            self.recent[k] = (self.recent[k] + [bool(p[k] >= P_ON)])[-STEADY_N:]
        cm = 15.0 + np.arange(NUM_BINS) * STEP_CM
        breathing = [k for k in range(NUM_BINS)
                     if len(self.recent[k]) == STEADY_N and np.mean(self.recent[k]) >= STEADY_FRAC
                     and cm[k] >= MIN_CM]
        strongest = max((move[k] for k in breathing), default=0.0)
        steady = [k for k in breathing if move[k] >= max(MIN_MOVE, REL_MOVE * strongest)]
        # the baby = the nearest steadily breathing slot; a nearer one takes over
        if steady and (self.baby is None or min(steady) < self.baby - 1):
            self.baby = min(steady)
        if self.baby is None:
            self.state = "EMPTY"
            return events
        near = p[max(0, self.baby - 1):self.baby + 2]  # the baby's slot and its neighbours
        need, of = CONFIRM
        self.baby_recent = (self.baby_recent + [float(near.max()) >= P_ON])[-of:]
        if sum(self.baby_recent) >= need:
            self.last_breath_t = t
            if self.state in ("CHECK", "ALERT", "EMPTY"):
                self.alerted = False
            self.state = "STILL"
            return events
        not_breathing = (t - self.last_breath_t) + LAG_S if self.last_breath_t is not None else 0.0
        if not_breathing >= self.no_breath_s:
            if not self.alerted:
                events.append({"type": "alert", "reason": "no_breath", "since_s": int(round(not_breathing))})
                self.alerted = True
            self.state = "ALERT"
        else:
            self.state = "CHECK"
        return events


def run_session(model, csv: Path, no_breath_s: float) -> dict:
    """The model on every window of one session, then the rules; results per D line like evaluate.replay."""
    t_ms, z = read_csv(csv)
    ends, feats, moves = [], [], []
    for end, f in windows(t_ms, z, hop=HOP):
        ends.append(end)
        feats.append(f)
        last3 = z[max(0, end - 49):end + 1]  # ~3 s of lines
        moves.append(np.abs(last3 - last3.mean(axis=0)).mean(axis=0))
    probs = model.predict(np.concatenate(feats), batch_size=4096, verbose=0)[:, 0].reshape(len(ends), NUM_BINS)
    d = Decider(no_breath_s)
    states, chests, events = ["EMPTY"] * len(t_ms), [None] * len(t_ms), []
    for n, (end, p) in enumerate(zip(ends, probs)):
        for ev in d.feed(t_ms[end] / 1000.0, p, moves[n]):
            events.append({"t_ms": int(t_ms[end]), **ev})
        stop = ends[n + 1] if n + 1 < len(ends) else len(t_ms)
        for i in range(end, stop):  # hold the state until the next model run
            states[i] = d.state
            chests[i] = None if d.baby is None else 15.0 + d.baby * STEP_CM
    return {"states": states, "chests": chests, "bpms": [None] * len(t_ms), "events": events}


def catchable(truth: dict, no_breath_s: float) -> bool:
    """A stop long enough that any detector with this threshold could ring before it ended."""
    s = truth["stop"]
    return bool(s) and (s[1] - s[0]) >= no_breath_s + 3.0


def compare(model, folder: Path, no_breath_s: float, who: list[str]) -> list[dict]:
    rows = []
    runners = {"brain": lambda f: evaluate.replay(f, no_breath_s),
               "model": lambda f: run_session(model, f, no_breath_s)}
    for kind in ("alone", "cosleep"):
        files = sorted((folder / kind).glob("*.csv"))
        for name, runner in ((w, runners[w]) for w in who):
            res, can = [], []
            for f in files:
                truth = json.loads(f.with_suffix(".json").read_text())
                r = evaluate.score(truth, runner(f), no_breath_s)
                res.append(r)
                can.append(catchable(truth, no_breath_s))
            row = evaluate.report(kind, no_breath_s, res)
            caught = sum(1 for r, c in zip(res, can) if c and r["caught"])
            row["catchable_caught"] = f"{caught}/{sum(can)}"
            row["who"] = name
            rows.append(row)
            keys = ["who", "kind", "no_breath_s", "catchable_caught", "stops_caught", "median_delay_s",
                    "false_alarms_per_hour", "pause_alarms", "seen_pct", "on_baby_pct"]
            print({k: row.get(k) for k in keys if k in row})
    return rows


def main():
    ap = argparse.ArgumentParser(description="Score the model + rules against the Brain on the test set.")
    ap.add_argument("--no-breath", type=float, nargs="*", default=[10.0, 20.0])
    ap.add_argument("--set", default="test", help="folder under ml/data/ (test is unseen by the model)")
    ap.add_argument("--who", nargs="*", default=["brain", "model"], help="brain, model or both")
    args = ap.parse_args()
    from tensorflow import keras
    model = keras.models.load_model(DATA / "models" / "breath_slot.keras")
    rows = []
    for nb in args.no_breath:
        rows += compare(model, DATA / args.set, nb, args.who)
    (DATA / f"compare_{args.set}.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
