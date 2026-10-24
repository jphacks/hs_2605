"""Stage A check: read the board's C/D/S/B lines, report rate and seq gaps, plot the range profile.

Usage:
  python firmware/tools/check_stream.py                       # auto-detect /dev/cu.usbmodem*, 20 s
  python firmware/tools/check_stream.py --seconds 30 --out range.png
  python firmware/tools/check_stream.py --file saved_lines.txt  # replay saved lines

Prints lines/s, seq gaps, bytes/s and the board's own clock rate, then saves a plot with
(1) the mean magnitude per range bin (static echoes) and (2) the variation per bin after
removing each bin's mean (moving or breathing targets stand out here).
"""
import argparse
import glob
import sys
import time

import numpy as np


def parse(lines):
    cfg, data, status, other, buttons = None, [], [], 0, 0
    for raw in lines:
        s = raw.strip()
        parts = s.split(",")
        try:
            if parts[0] == "C" and len(parts) == 5:
                cfg = (float(parts[1]), float(parts[2]), int(parts[3]), float(parts[4]))
            elif parts[0] == "D" and len(parts) >= 3:
                vals = np.array(parts[3:], dtype=np.int64)
                if len(vals) % 2:
                    other += 1
                    continue
                z = vals[0::2] + 1j * vals[1::2]
                data.append((int(parts[1]), int(parts[2]), z, len(raw)))
            elif parts[0] == "S":
                status.append(s)
            elif parts[0] == "B":
                buttons += 1
            else:
                other += 1
        except ValueError:
            other += 1
    return cfg, data, status, other, buttons


def read_port(port, seconds):
    import serial

    lines = []
    with serial.Serial(port, 115200, timeout=0.2) as ser:
        ser.reset_input_buffer()
        t0 = time.time()
        buf = b""
        while time.time() - t0 < seconds:
            buf += ser.read(4096)
            *full, buf = buf.split(b"\n")
            lines += [ln.decode("ascii", "replace") + "\n" for ln in full]
    return lines, seconds


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--file")
    ap.add_argument("--out", default="range_profile.png")
    ap.add_argument("--save", help="also save the raw lines to this file")
    a = ap.parse_args()

    if a.file:
        lines = open(a.file, encoding="ascii", errors="replace").readlines()
        wall = None
    else:
        port = a.port or (sorted(glob.glob("/dev/cu.usbmodem*")) or [None])[0]
        if not port:
            sys.exit("no /dev/cu.usbmodem* port: check the cable is in the KitProg3 port")
        print(f"reading {port} for {a.seconds:.0f} s ...")
        lines, wall = read_port(port, a.seconds)
    if a.save:
        open(a.save, "w", encoding="ascii").writelines(lines)

    cfg, data, status, other, buttons = parse(lines)
    print(f"lines: {len(lines)}  D: {len(data)}  S: {len(status)}  B: {buttons}  other: {other}")
    for s in status[:10]:
        print("  ", s)
    if cfg is None:
        print("no C line seen (C comes at boot and every 5 s)")
    else:
        print(f"C: first_cm={cfg[0]} step_mm={cfg[1]} n={cfg[2]} scale={cfg[3]}")
    if not data:
        sys.exit("no D lines")

    seq = np.array([d[0] for d in data])
    t_ms = np.array([d[1] for d in data])
    dseq = np.diff(seq)
    gaps = int(np.sum(dseq != 1))
    lost = int(np.sum(dseq[dseq > 1] - 1))
    span_s = (t_ms[-1] - t_ms[0]) / 1000.0
    print(f"seq {seq[0]}..{seq[-1]}  gaps: {gaps}  lost lines: {lost}  resets: {int(np.sum(dseq < 1))}")
    if span_s > 0:
        print(f"D rate by board clock: {(len(data) - 1) / span_s:.2f} lines/s")
    if wall:
        print(f"D rate by PC clock:    {len(data) / wall:.2f} lines/s")
        print(f"bytes/s: {sum(len(l) for l in lines) / wall:.0f} (115200 baud max ~11520)")
    print(f"D line length: mean {np.mean([d[3] for d in data]):.0f}, max {max(d[3] for d in data)} bytes")

    n = min(len(d[2]) for d in data)
    z = np.array([d[2][:n] for d in data], dtype=complex)
    if cfg:
        z = z / cfg[3]
        rng = cfg[0] + np.arange(n) * cfg[1] / 10.0
    else:
        rng = np.arange(n, dtype=float)
    mag = np.abs(z).mean(axis=0)
    var = np.abs(z - z.mean(axis=0)).mean(axis=0)
    print(f"strongest echo: {rng[np.argmax(mag)]:.1f} cm   most variation: {rng[np.argmax(var)]:.1f} cm")

    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
    ax[0].plot(rng, mag, "o-")
    ax[0].set_ylabel("mean |z|")
    ax[0].set_title(f"Range profile ({len(data)} D lines)")
    ax[1].plot(rng, var, "o-", color="tab:red")
    ax[1].set_ylabel("mean |z - mean z|")
    ax[1].set_xlabel("range (cm)")
    for x in ax:
        x.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(a.out, dpi=110)
    print(f"plot saved: {a.out}")


if __name__ == "__main__":
    main()
