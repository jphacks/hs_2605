"""What the AI model sees: per distance slot, 12 s of "how did this slot's echo move?".

Shared by training (ml/train.py) and by the decision layer (ml/decide.py). Every step here is
simple arithmetic on purpose, so it can be written in C on the board later with the same result:
no filters with state, no FFTs, just means, angles, logs and differences.

The question the model answers, for one slot at one moment:
    "Is something BREATHING at this distance right now?"   (yes / no)
It does NOT decide alarms. A wall, an empty slot and a chest that stopped breathing all answer
"no"; telling them apart needs memory (did this slot breathe a moment ago? is its echo still
there?), which stays in the rules of ml/decide.py where it can be read and checked.

Input for one slot k at line i: the last WINDOW lines (~12 s) of slots k-2..k+2 (the slot and
two neighbours each side, because one body's echo spreads over about 3 slots), 2 numbers each:
    disp   chest movement in mm from the echo's phase: unwrap(angle(z - mean)) * lambda / 4pi,
           minus its mean. A breathing chest makes a slow wave of 0.2-4 mm; empty slots and walls
           give noise.
    level  log of how far the echo is from its window mean, |z - mean|, compared with the
           strongest of the 5 slots: says which neighbour the movement comes from, and how
           strong it is, without depending on the distance (echoes weaken with distance).
Shape per example: (STEPS, 10), after averaging pairs of lines.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

NUM_BINS = 24
SCALE = 10
STEP_CM = 3.75
LAMBDA_MM = 299792458.0 / 60.5e9 * 1000.0  # 4.955 mm, centre of the 58.5-62.5 GHz sweep
WINDOW = 200        # lines: 200 / 16.6 per second = 12 s, enough for 2 slow breaths
DECIMATE = 2        # average pairs of lines: 8.3 values/s is plenty for breaths of <= 1.5 Hz,
                    # and halves the work for the model on the board
STEPS = WINDOW // DECIMATE
NEIGHBOURS = 2      # slots on each side
CHANNELS = 2 * (2 * NEIGHBOURS + 1)


def read_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """t_ms and complex slots (value units) of every full D line in a board-format CSV."""
    t, z = [], []
    for ln in Path(path).read_text().splitlines():
        p = ln.split(",")
        if p[0] == "D" and len(p) == 3 + 2 * NUM_BINS:
            v = np.array(p[3:], float) / SCALE
            t.append(int(p[2]))
            z.append(v[0::2] + 1j * v[1::2])
    return np.array(t), np.array(z)


def slot_cm(k) -> np.ndarray:
    return 15.0 + np.asarray(k) * STEP_CM


def window_features(zw: np.ndarray) -> np.ndarray:
    """Features of all 24 slots for one window of lines: zw is (WINDOW, 24) complex.

    Returns (24, STEPS, CHANNELS): one model input per slot.
    """
    centred = zw - zw.mean(axis=0)                       # remove the still part (walls, leakage)
    disp = np.unwrap(np.angle(centred), axis=0) * LAMBDA_MM / (4.0 * np.pi)
    disp -= disp.mean(axis=0)                            # movement around its own middle
    disp = np.clip(disp, -8.0, 8.0)                      # noise slots can unwrap far: cap them
    mag = np.abs(centred)                                # how much each slot's echo moves
    # pad 2 empty slots on each side, so every slot has 2 neighbours each side
    pad_d = np.pad(disp, ((0, 0), (NEIGHBOURS, NEIGHBOURS)))
    pad_m = np.pad(mag, ((0, 0), (NEIGHBOURS, NEIGHBOURS)))
    out = np.empty((NUM_BINS, zw.shape[0], CHANNELS), np.float32)
    for k in range(NUM_BINS):
        d = pad_d[:, k:k + 2 * NEIGHBOURS + 1]
        m = pad_m[:, k:k + 2 * NEIGHBOURS + 1]
        ref = max(float(m.mean(axis=0).max()), 1e-3)     # strongest of the 5 slots (on average)
        level = np.log10(m / ref + 1e-3)                  # 0 = as strong as the strongest, -3 = nothing
        out[k, :, 0::2] = d
        out[k, :, 1::2] = level
    # average each pair of lines (time axis 1): (24, 200, 10) -> (24, 100, 10)
    return out.reshape(NUM_BINS, STEPS, DECIMATE, CHANNELS).mean(axis=2)


def windows(t_ms: np.ndarray, z: np.ndarray, hop: int = 8):
    """Yield (end index, features) every `hop` lines (~0.5 s), once a full window is there."""
    for end in range(WINDOW, len(z) + 1, hop):
        yield end - 1, window_features(z[end - WINDOW:end])
