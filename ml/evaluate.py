"""How well does the Brain do on simulated baby data?

Replays every converted session (ml/synth.py) through the real Brain (pc/ibuki/brain.py), line
by line exactly as the server would, and compares what it said with the truth file.

Scores, per kind of session ("alone" = baby on its own, "cosleep" = a real adult beside it):
  stops caught   scripted breathing stops that got a no_breath alert before the stop ended
  delay          seconds from the baby's last breath to that alert (the threshold is the floor)
  false alarms   no_breath alerts while the baby had breathed within the threshold: the Brain
                 was wrong. (An alert after a long *natural* pause is counted apart: it is what
                 the threshold asks for, but a parent would still find it annoying.)
  left_bed       "left the bed" alerts although the baby never left: wrong
  seen           share of the time the Brain thought someone was in bed (the baby always is)
  bpm error      |Brain's breathing rate - true rate| when the Brain shows one
  on baby        cosleep only: share of the time the Brain's chest slot is the baby's, not the
                 parent's (the Brain follows the strongest echo, so expect the parent)

Run from the repo root:
    ../env/bin/python -m ml.evaluate                 # thresholds 10 s (demo) and 20 s (babies)
    ../env/bin/python -m ml.evaluate --no-breath 20
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "pc"))
from ibuki.brain import Brain  # noqa: E402  (the same Brain the server runs)

SYNTH = REPO / "ml" / "data" / "synth"
STEP_CM = 3.75


def replay(csv: Path, no_breath_s: float) -> dict:
    """Feed one session to a fresh Brain; keep its state per D line and every event with its time."""
    brain = Brain(no_breath_s=no_breath_s)
    states, chests, bpms, events = [], [], [], []
    for line in csv.read_text().splitlines():
        out = brain.feed(line)
        if not line.startswith("D,"):
            continue
        t = int(line.split(",")[2])
        for msg in out:
            if msg.get("type") in ("alert", "info"):
                events.append({"t_ms": t, **msg})
        states.append(brain.state)
        chests.append(None if brain.chest is None else 15.0 + brain.chest * STEP_CM)
        bpms.append(brain.bpm)
    return {"states": states, "chests": chests, "bpms": bpms, "events": events}


def score(truth: dict, run: dict, no_breath_s: float) -> dict:
    t_ms = np.array(truth["t_ms"])
    since = np.array(truth["since_breath_s"], float)
    res = {"stop": truth["stop"] is not None, "caught": False, "delay": None,
           "false_alarms": 0, "pause_alarms": 0, "left_bed": 0}
    t0 = t_ms[0]
    for ev in run["events"]:
        i = int(np.searchsorted(t_ms, ev["t_ms"]))
        i = min(i, len(t_ms) - 1)
        t_s = (ev["t_ms"] - t0) / 1000.0
        if ev.get("reason") == "left_bed":
            res["left_bed"] += 1
        if ev.get("reason") != "no_breath":
            continue
        in_stop = truth["stop"] and truth["stop"][0] <= t_s <= truth["stop"][1] + 2.0
        if in_stop and not res["caught"]:
            res["caught"], res["delay"] = True, round(float(since[i]), 1)
        elif not in_stop:
            # breathed recently: the Brain was wrong. Not breathed for the threshold: a real pause
            if np.isnan(since[i]) or since[i] < no_breath_s - 1.0:
                res["false_alarms"] += 1
            else:
                res["pause_alarms"] += 1
    states = np.array(run["states"])
    res["seen"] = float(np.mean(states != "EMPTY"))
    shown = [(b, truth["true_bpm"]) for b in run["bpms"] if b is not None]
    res["bpm_err"] = float(np.mean([abs(b - tb) for b, tb in shown])) if shown else None
    if truth["kind"] == "cosleep":
        chests = [c for c in run["chests"] if c is not None]
        res["on_baby"] = (float(np.mean([abs(c - truth["baby_cm"]) <= STEP_CM for c in chests]))
                          if chests else 0.0)
    res["minutes"] = (t_ms[-1] - t0) / 60000.0
    return res


def report(kind: str, no_breath_s: float, results: list[dict]) -> dict:
    stops = [r for r in results if r["stop"]]
    caught = [r for r in stops if r["caught"]]
    minutes = sum(r["minutes"] for r in results)
    errs = [r["bpm_err"] for r in results if r["bpm_err"] is not None]
    out = {
        "kind": kind, "no_breath_s": no_breath_s, "sessions": len(results),
        "minutes": round(minutes, 1),
        "stops_caught": f"{len(caught)}/{len(stops)}",
        "median_delay_s": round(float(np.median([r["delay"] for r in caught])), 1) if caught else None,
        "false_alarms": sum(r["false_alarms"] for r in results),
        "false_alarms_per_hour": round(sum(r["false_alarms"] for r in results) / (minutes / 60.0), 1),
        "pause_alarms": sum(r["pause_alarms"] for r in results),
        "left_bed_wrong": sum(r["left_bed"] for r in results),
        "seen_pct": round(100 * float(np.mean([r["seen"] for r in results])), 1),
        "bpm_err": round(float(np.mean(errs)), 1) if errs else None,
    }
    if kind == "cosleep":
        out["on_baby_pct"] = round(100 * float(np.mean([r["on_baby"] for r in results])), 1)
    return out


def main():
    ap = argparse.ArgumentParser(description="Score the Brain on the simulated baby sessions.")
    ap.add_argument("--no-breath", type=float, nargs="*", default=[10.0, 20.0])
    args = ap.parse_args()
    summary = []
    for kind in ("alone", "cosleep"):
        files = sorted((SYNTH / kind).glob("*.csv"))
        for nb in args.no_breath:
            results = [score(json.loads(f.with_suffix(".json").read_text()), replay(f, nb), nb) for f in files]
            summary.append(report(kind, nb, results))
            print(json.dumps(summary[-1]))
    (REPO / "ml" / "data" / "eval_baseline.json").write_text(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
