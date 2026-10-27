"""Which direction a breathing chest is in, from the board's 3 antennas (E lines).

Record with all 3 antennas:  ../env/bin/python pc/record.py <name> --antennas 3
Then:                        ../env/bin/python -m ml.antennas recordings/<person>/rec_<name>_*.csv

The range FFT already says how far a reflector is (24 distance slots). With 3 antennas, the
same echo reaches each antenna with a slightly different phase, depending on the direction it
comes from: for antennas half a wavelength apart, a phase difference dphi means an angle of
asin(dphi / pi) from straight ahead. Only the MOVING part of each slot's echo is used (the
still echo of walls and the board itself is removed), so the angle is the breathing chest's.
Two people at the same distance, side by side, then show up as two different angles.

E line: E,seq,t_ms,<base64 of int16 little-endian: antenna, bin, Re/Im> (firmware/README.md).
"""
from __future__ import annotations

import argparse
import base64
from pathlib import Path

import numpy as np

NUM_BINS = 24
ANTENNAS = 3
FIRST_CM, STEP_CM = 15.0, 3.75


def read_e(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """t_ms (n,) and the complex slots of every E line: (n, 3 antennas, 24 slots)."""
    t, z = [], []
    for ln in Path(path).read_text().splitlines():
        p = ln.split(",")
        if p[0] != "E" or len(p) != 4:
            continue
        v = np.frombuffer(base64.b64decode(p[3]), dtype="<i2").astype(float)
        if len(v) != ANTENNAS * NUM_BINS * 2:
            continue
        v = v.reshape(ANTENNAS, NUM_BINS, 2)
        t.append(int(p[2]))
        z.append(v[..., 0] + 1j * v[..., 1])
    return np.array(t), np.array(z)


def chest_direction(z: np.ndarray, min_cm: float = 28.0) -> dict:
    """The slot that moves most (beyond the board's own leakage) and the direction of its movement."""
    moving = z - z.mean(axis=0)                     # remove the still echo, per antenna and slot
    cm = FIRST_CM + np.arange(NUM_BINS) * STEP_CM
    strength = np.abs(moving).mean(axis=(0, 1))     # all antennas together
    k = int(np.argmax(np.where(cm >= min_cm, strength, 0.0)))
    out = {"slot_cm": round(float(cm[k]), 2), "movement": round(float(strength[k]), 1)}
    for a in (1, 2):
        # the phase of antenna a relative to antenna 0, averaged over the recording, weighted by
        # how much the chest moves at each moment
        dphi = float(np.angle(np.sum(moving[:, a, k] * np.conj(moving[:, 0, k]))))
        out[f"phase_0_{a}_deg"] = round(np.degrees(dphi), 1)
        out[f"angle_0_{a}_deg"] = round(float(np.degrees(np.arcsin(np.clip(dphi / np.pi, -1, 1)))), 1)
    return out


def main():
    ap = argparse.ArgumentParser(description="Direction of the breathing chest from 3-antenna recordings.")
    ap.add_argument("files", nargs="+")
    for f in ap.parse_args().files:
        t, z = read_e(Path(f))
        if len(z) < 100:
            print(Path(f).name, "too few E lines:", len(z))
            continue
        print(Path(f).name, f"{len(z)} lines,", chest_direction(z))


if __name__ == "__main__":
    main()
