"""Step 2 of the Kaizen plan: converted baby data, a simulated baby chest in real radar recordings.

We can't record a real baby that stops breathing, and we have no baby recordings at all. So we
take recordings of our own room made by the real board (empty rooms, or an adult lying there),
and add the echo a breathing baby's chest would make, in exactly the format the board sends.
Everything real stays real: the room's reflections, the board's leakage and noise, the adult's
breathing. Only the baby is simulated, and its breathing rhythm comes from real babies (AIR-400,
see ml/air400.py), including their natural pauses.

How the board sees a chest (firmware/proj_cm55/ibuki.c, preset P1):
- Each chirp sweeps 58.5-62.5 GHz (4 GHz), giving 64 samples. The firmware removes the chirp's
  mean, applies a Hann window, takes a 64-point real FFT and keeps bins 4..27: 24 distance
  "slots", 3.75 cm apart, from 15 cm. It averages 64 chirps (2 frames) per D line, ~16.6 lines/s,
  and sends each slot as an integer I/Q pair = value * 10.
- Something at distance r adds a tone at r / 3.75 cm FFT bins. Its echo lands mostly in the
  nearest slot and leaks into the neighbours (Hann window), all with the same phase motion.
- When the chest moves by d mm, the round trip changes by 2d, so the echo's phase turns by
  4*pi*d / lambda (lambda = 4.955 mm at the 60.5 GHz centre). That phase is what the Brain reads.
So the simulated baby = a tone at the baby's distance, run through the same window and FFT as the
firmware, rotated by the chest movement, added to every D line of a real recording.

Assumptions (stated so they can be checked later with the physical dummy or real babies):
- Echo strength: an adult chest in our recordings is |z| ~ 430 * (37.5 cm / r)^2 (fitted on the
  24 recordings: 30 cm ~ 700, 37.5 cm ~ 430, 64 cm ~ 150). A baby's is BABY_FACTOR times that
  (0.2-0.5 by default): a much smaller body reflects less.
- Breath depth: 0.5-2.0 mm average per session (the newborn simulator study, Gleichauf et al.
  2024, spans 0.14-4 mm), varying +-20% from breath to breath.
- Shape: each breath goes from full (the annotated moment) down to empty and back, a raised
  cosine from one annotated breath to the next. During a scripted stop the chest stays empty.
- Heartbeat: a 0.03 mm, 2.1 Hz wiggle that keeps going during a stop, as a real heart would.
  It sits above the Brain's 0.1-1.5 Hz breathing band, so it mostly tests that the band works.

Run from the repo root (needs ml/data/air400_breaths.json from ml/air400.py):
    ../env/bin/python -m ml.synth            # writes ml/data/synth/{alone,cosleep}/
Each session is a pair: <name>.csv (board lines, replayable by the server and the Brain) and
<name>.json (the truth: where the baby is, when it breathes, when it stops).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "ml" / "data"

# --- the board (must match firmware/proj_cm55/ibuki.h and radar_settings.h) ------------------
N_FFT = 64              # samples per chirp = FFT size
FIRST_BIN = 4           # first FFT bin sent: 4 * 3.75 cm = 15 cm
NUM_BINS = 24           # slots per D line
STEP_CM = 3.75          # c / (2 * 4 GHz sweep)
SCALE = 10              # D line integer = value * SCALE
LAMBDA_MM = 299792458.0 / 60.5e9 * 1000.0  # 4.955 mm at the sweep's centre

# --- the simulated baby (assumptions above) ------------------------------------------------
ADULT_ECHO_AT_37CM = 430.0
BABY_FACTOR = (0.2, 0.5)
DEPTH_MM = (0.5, 2.0)
DEPTH_JITTER = 0.2
HEART_HZ, HEART_MM = 2.1, 0.03
STOP_S = (20.0, 35.0)   # a scripted breathing stop lasts this long
STOP_AFTER_S = 25.0     # ...and starts after this much breathing, so the Brain learns "normal"


# ----------------------------------------------------------------------------- the radar maths
def tone_bins(r_cm: float) -> tuple[np.ndarray, np.ndarray]:
    """How a reflector at r_cm shows up in the 24 slots, as two complex vectors (a, b).

    The firmware sees a real tone cos(2*pi*f*n/N + phi) per chirp. After its mean removal,
    Hann window and FFT, slot k holds a_k * e^{j*phi} + b_k * e^{-j*phi}: the tone's positive-
    frequency part (a) plus a small mirror image (b). We get a and b by running the firmware's
    exact steps on phi = 0 and phi = pi/2, so window leakage and the mirror are included.
    a and b are scaled so the strongest slot has |a| = 1: callers multiply by the echo strength.
    """
    f = r_cm / STEP_CM  # beat frequency in FFT bins
    n = np.arange(N_FFT)
    hann = 0.5 * (1.0 - np.cos(2.0 * np.pi * n / (N_FFT - 1)))  # same window as the firmware

    def spectrum(phi: float) -> np.ndarray:
        x = np.cos(2.0 * np.pi * f * n / N_FFT + phi)
        x = (x - x.mean()) * hann  # mean removal, then window, as in ibuki_on_frame()
        return np.fft.rfft(x)[FIRST_BIN:FIRST_BIN + NUM_BINS]

    s0, s90 = spectrum(0.0), spectrum(np.pi / 2)
    a = (s0 - 1j * s90) / 2.0  # from s(phi) = a e^{j phi} + b e^{-j phi}
    b = (s0 + 1j * s90) / 2.0
    peak = np.abs(a).max()
    return a / peak, b / peak


def adult_echo(r_cm: float) -> float:
    """Echo strength of an adult chest at r_cm, fitted on our recordings (radar: amplitude ~ 1/r^2)."""
    return ADULT_ECHO_AT_37CM * (37.5 / r_cm) ** 2


# ----------------------------------------------------------------------------- base recordings
def read_lines(path: Path) -> list[str]:
    """Board lines from a recording: our CSVs, or the server's .jsonl logs (channel "raw")."""
    if path.suffix == ".jsonl":
        out = []
        for row in path.read_text().splitlines():
            d = json.loads(row)
            if d.get("ch") == "raw" and isinstance(d.get("msg"), str):
                out.append(d["msg"].strip())
        return out
    return [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]


def parse(lines: list[str]) -> tuple[list[int], list[int], np.ndarray]:
    """seq, t_ms and the complex slots (value units, not *10) of every full D line."""
    seq, t_ms, z = [], [], []
    for ln in lines:
        p = ln.split(",")
        if p[0] == "D" and len(p) == 3 + 2 * NUM_BINS:
            v = np.array(p[3:], float) / SCALE
            seq.append(int(p[1]))
            t_ms.append(int(p[2]))
            z.append(v[0::2] + 1j * v[1::2])
    return seq, t_ms, np.array(z)


def write_csv(path: Path, seq: list[int], t_ms: list[int], z: np.ndarray) -> None:
    """Board lines again: a C line every 5 s (as the firmware sends) and the D lines."""
    out, last_c = [], None
    for s, t, row in zip(seq, t_ms, z):
        if last_c is None or t - last_c >= 5000:
            out.append(f"C,{15.0:.1f},{STEP_CM * 10:.1f},{NUM_BINS},{SCALE}")
            last_c = t
        iq = np.empty(2 * NUM_BINS)
        iq[0::2], iq[1::2] = row.real, row.imag
        out.append("D," + ",".join([str(s), str(t)] + [str(int(round(v * SCALE))) for v in iq]))
    path.write_text("\n".join(out) + "\n")


# ----------------------------------------------------------------------------- the baby
@dataclass
class Baby:
    r_cm: float
    echo: float                 # echo strength (|z| units) of the whole baby
    depth_mm: float             # average breath depth
    breaths: list[float]        # breath times (s from the session start): fullest chest
    depths: list[float]         # depth of each breath
    stop: tuple[float, float] | None = None  # scripted stop: (from, to) seconds
    phase0: float = 0.0
    meta: dict = field(default_factory=dict)


def breath_sequence(clips: list[dict], subject: str, start: int, seconds: float) -> list[float]:
    """Real breath times for `seconds`, from one baby's consecutive AIR-400 clips.

    Clips are ~1 minute each; we join them end to end. Between two clips we leave the baby's
    own median breath interval, so the joint doesn't look like a pause.
    """
    mine = [c for c in clips if c["subject"] == subject]
    gaps = np.concatenate([np.diff(c["breaths_s"]) for c in mine if len(c["breaths_s"]) > 1])
    step = float(np.median(gaps))
    times, offset, i = [], 0.0, start
    while offset < seconds + 10:
        c = mine[i % len(mine)]
        b = c["breaths_s"]
        if b:
            times += [offset + x - b[0] for x in b]  # each clip starts at its first breath
            offset = times[-1] + step
        i += 1
    return [t for t in times if t <= seconds + 10]


def chest_mm(t: np.ndarray, baby: Baby) -> tuple[np.ndarray, np.ndarray]:
    """Chest movement (mm) at each time t, and whether the baby is breathing then.

    Between two breaths i and i+1 the chest goes full -> empty -> full: a raised cosine of the
    breath's depth. In a scripted stop, breaths inside it are removed: the last breath before it
    finishes its exhale (one median interval), then the chest stays empty until the stop ends.
    """
    b = np.array(baby.breaths)
    d = np.array(baby.depths)
    keep = np.ones(len(b), bool)
    if baby.stop:
        keep = (b < baby.stop[0]) | (b >= baby.stop[1])
    b, d = b[keep], d[keep]
    interval = float(np.median(np.diff(b)))

    out = np.zeros(len(t))
    breathing = np.zeros(len(t), bool)
    i = np.searchsorted(b, t, side="right") - 1  # the breath each time belongs to
    for n, (tn, k) in enumerate(zip(t, i)):
        if k < 0:
            continue  # before the first breath: chest empty
        nxt = b[k + 1] if k + 1 < len(b) else tn + 1e9
        span = nxt - b[k]
        if span > 2.5 * interval and baby.stop and b[k] < baby.stop[0] <= nxt:
            # the breath right before a scripted stop: exhale once, then stay empty
            span = interval
        x = (tn - b[k]) / span
        if x < 1.0:
            out[n] = d[k] * (0.5 + 0.5 * np.cos(2.0 * np.pi * x))
            breathing[n] = True
    out += HEART_MM * np.sin(2.0 * np.pi * HEART_HZ * t)  # the heart never stops
    return out, breathing


def add_baby(z: np.ndarray, t_s: np.ndarray, baby: Baby) -> np.ndarray:
    """The recording's slots plus the baby's echo: echo * (a e^{j phi} + b e^{-j phi})."""
    a, bb = tone_bins(baby.r_cm)
    mm, _ = chest_mm(t_s, baby)
    phi = baby.phase0 + 4.0 * np.pi * mm / LAMBDA_MM
    e = np.exp(1j * phi)[:, None]
    return z + baby.echo * (a[None, :] * e + bb[None, :] * np.conj(e))


# ----------------------------------------------------------------------------- sessions
def chest_cm_of(z: np.ndarray) -> float:
    """Where the adult in a base recording is: the slot (from 28 cm) whose echo changes most."""
    change = np.abs(z - z.mean(axis=0)).mean(axis=0)
    cm = 15.0 + np.arange(NUM_BINS) * STEP_CM
    return float(cm[np.argmax(np.where(cm >= 28.0, change, -1.0))])


def make_session(rng, base: Path, clips: list[dict], kind: str, with_stop: bool) -> tuple[list, list, np.ndarray, dict]:
    seq, t_ms, z = parse(read_lines(base))
    t_s = (np.array(t_ms) - t_ms[0]) / 1000.0
    seconds = float(t_s[-1])

    subject = rng.choice(sorted({c["subject"] for c in clips}))
    start = int(rng.integers(0, 50))
    breaths = breath_sequence(clips, subject, start, seconds)
    depth = float(rng.uniform(*DEPTH_MM))
    depths = list(depth * np.exp(rng.normal(0.0, DEPTH_JITTER, len(breaths))))

    if kind == "alone":
        r_cm = float(rng.uniform(25.0, 70.0))  # a baby somewhere in front of the board
        parent_cm = None
    else:
        # co-sleeping: the baby lies 10-20 cm nearer the board than the parent's chest,
        # the mounting the plan recommends (the separation is easier when the baby is nearer)
        parent_cm = chest_cm_of(z)
        r_cm = max(25.0, parent_cm - float(rng.uniform(10.0, 20.0)))
    echo = float(rng.uniform(*BABY_FACTOR)) * adult_echo(r_cm)

    stop = None
    if with_stop and seconds > STOP_AFTER_S + STOP_S[0]:
        length = float(rng.uniform(*STOP_S))
        begin = float(rng.uniform(STOP_AFTER_S, max(STOP_AFTER_S, seconds - length)))
        stop = (round(begin, 2), round(min(seconds, begin + length), 2))

    baby = Baby(r_cm=r_cm, echo=echo, depth_mm=depth, breaths=breaths, depths=depths,
                stop=stop, phase0=float(rng.uniform(0, 2 * np.pi)))
    z_out = add_baby(z, t_s, baby)
    mm, breathing = chest_mm(t_s, baby)

    # the truth, one entry per D line, plus what the session is
    last_breath = np.full(len(t_s), np.nan)
    b = np.array([x for x in breaths if not (stop and stop[0] <= x < stop[1])])
    k = np.searchsorted(b, t_s, side="right") - 1
    last_breath[k >= 0] = b[k[k >= 0]]
    truth = {
        "base": str(base.relative_to(REPO)),
        "kind": kind,
        "subject": subject,
        "baby_cm": round(r_cm, 2),
        "parent_cm": parent_cm,
        "echo": round(echo, 1),
        "depth_mm": round(depth, 3),
        "stop": stop,
        "true_bpm": round(len(b[(b >= 0) & (b <= seconds)]) / seconds * 60.0, 1),
        "t_ms": t_ms,
        "breathing": breathing.astype(int).tolist(),
        "since_breath_s": np.round(t_s - last_breath, 2).tolist(),
    }
    return seq, t_ms, z_out, truth


def bases(kind: str) -> list[Path]:
    rec = REPO / "recordings"
    if kind == "alone":
        return sorted(rec.glob("*/rec_empty_*.csv")) + sorted(rec.glob("live/empty_*.jsonl"))
    # co-sleeping: the adult breathing (and, in the hold recordings, stopping) beside the baby
    return sorted(rec.glob("*/rec_breathing_*.csv")) + sorted(rec.glob("*/rec_hold_*.csv"))


def main():
    ap = argparse.ArgumentParser(description="Make converted baby sessions (see the module docstring).")
    ap.add_argument("--per-base", type=int, default=8, help="sessions per base recording and kind")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    clips = json.loads((DATA / "air400_breaths.json").read_text())
    rng = np.random.default_rng(args.seed)
    for kind in ("alone", "cosleep"):
        out_dir = DATA / "synth" / kind
        out_dir.mkdir(parents=True, exist_ok=True)
        n = 0
        for base in bases(kind):
            for j in range(args.per_base):
                seq, t_ms, z, truth = make_session(rng, base, clips, kind, with_stop=(j % 2 == 0))
                name = f"{base.parent.name}_{base.stem}_{j}"
                write_csv(out_dir / f"{name}.csv", seq, t_ms, z)
                (out_dir / f"{name}.json").write_text(json.dumps(truth))
                n += 1
        print(f"{kind}: {n} sessions in {out_dir.relative_to(REPO)}")


if __name__ == "__main__":
    main()
