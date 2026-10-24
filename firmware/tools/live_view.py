"""Live radar viewer: shows where things move, how much, and the breathing wave.

Usage (repo root, venv active):
  python firmware/tools/live_view.py                 # board on /dev/cu.usbmodem*
  python firmware/tools/live_view.py --file lines.txt  # replay saved lines at real speed
  python firmware/tools/live_view.py --file lines.txt --snapshot view.png  # test, no window

Window:
  top     - how much each range bin changes over the last 3 s (where the movement is)
  middle  - total motion over the last 20 s, with MOVING / STILL
  bottom  - displacement (mm) of the most active bin over the last 20 s (breathing wave)
"""
import argparse
import collections
import glob
import sys
import threading
import time

import numpy as np

WINDOW_S = 20.0
PROFILE_S = 3.0
LAMBDA_MM = 5.0  # 60 GHz wavelength, ~4.96 mm


class Stream:
    """Collects D lines (time, complex bins) from a serial port or a replayed file."""

    def __init__(self):
        self.cfg = (15.0, 37.5, 24, 10.0)  # replaced by the board's C line
        self.buf = collections.deque(maxlen=2000)
        self.lock = threading.Lock()
        self.last_seq = None
        self.gaps = 0
        self.status = ""

    def feed(self, line):
        p = line.strip().split(",")
        try:
            if p[0] == "C" and len(p) == 5:
                self.cfg = (float(p[1]), float(p[2]), int(p[3]), float(p[4]))
            elif p[0] == "S" and len(p) >= 3:
                self.status = ",".join(p[2:])
            elif p[0] == "D" and len(p) >= 5:
                seq, t_ms = int(p[1]), int(p[2])
                v = np.array(p[3:], dtype=float)
                z = (v[0::2] + 1j * v[1::2]) / self.cfg[3]
                if self.last_seq is not None and seq != self.last_seq + 1:
                    self.gaps += 1
                self.last_seq = seq
                with self.lock:
                    self.buf.append((t_ms / 1000.0, z))
        except (ValueError, IndexError):
            pass

    def snapshot(self):
        with self.lock:
            return list(self.buf)


def read_serial(stream, port, save_path=None):
    import serial

    # The first bytes after opening the port can be garbage, so never let encoding stop the reader.
    out = open(save_path, "a", encoding="ascii", errors="replace") if save_path else None
    with serial.Serial(port, 115200, timeout=0.2) as ser:
        rest = b""
        while True:
            rest += ser.read(4096)
            *lines, rest = rest.split(b"\n")
            for ln in lines:
                text = ln.decode("ascii", "replace")
                stream.feed(text)
                if out:
                    out.write(text.rstrip("\r") + "\n")
            if out and lines:
                out.flush()


def replay_file(stream, path, realtime=True):
    t0_wall, t0_board = time.time(), None
    for ln in open(path, encoding="ascii", errors="replace"):
        if realtime and ln.startswith("D,"):
            t_board = int(ln.split(",")[2]) / 1000.0
            t0_board = t_board if t0_board is None else t0_board
            delay = (t_board - t0_board) - (time.time() - t0_wall)
            if delay > 0:
                time.sleep(delay)
        stream.feed(ln)


def analyse(stream, min_cm):
    data = stream.snapshot()
    if len(data) < 10:
        return None
    t = np.array([d[0] for d in data])
    n = min(len(d[1]) for d in data)
    z = np.array([d[1][:n] for d in data])
    keep = t >= t[-1] - WINDOW_S
    t, z = t[keep], z[keep]
    first_cm, step_mm = stream.cfg[0], stream.cfg[1]
    rng = first_cm + np.arange(n) * step_mm / 10.0
    use = rng >= min_cm

    recent = t >= t[-1] - PROFILE_S
    zr = z[recent]
    profile = np.abs(zr - zr.mean(axis=0)).mean(axis=0)

    motion = np.r_[0.0, np.abs(np.diff(z[:, use], axis=0)).sum(axis=1)]
    quiet = np.percentile(motion[1:], 10) if len(motion) > 20 else motion.max()
    moving = motion[-5:].mean() > 4.0 * max(quiet, 1e-9)

    b = int(np.argmax(np.where(use, profile, -1)))
    zc = z[:, b] - z[:, b].mean()  # remove the static echo in this bin
    phase = np.unwrap(np.angle(zc))
    disp = phase * LAMBDA_MM / (4 * np.pi)
    disp -= np.polyval(np.polyfit(t - t[0], disp, 1), t - t[0])  # remove slow drift
    return dict(t=t - t[-1], rng=rng, profile=profile, use=use, motion=motion,
                moving=moving, bin_cm=rng[b], disp=disp)


def build_figure():
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(3, 1, figsize=(9, 8))
    fig.canvas.manager.set_window_title("IBUKI radar live view")
    return fig, ax


def draw(fig, ax, a, stream):
    for x in ax:
        x.clear()
        x.grid(alpha=0.3)
    if a is None:
        ax[0].set_title("waiting for D lines ... (is the board plugged in?)")
        return
    colors = np.where(a["use"], "tab:blue", "lightgray")
    ax[0].bar(a["rng"], a["profile"], width=3.0, color=colors)
    ax[0].axvline(a["bin_cm"], color="tab:red", lw=1)
    ax[0].set_xlabel("range (cm)  [gray = too close, ignored]")
    ax[0].set_ylabel("change, last 3 s")
    ax[0].set_title(f"Most active: {a['bin_cm']:.0f} cm   (seq gaps: {stream.gaps})")

    ax[1].plot(a["t"], a["motion"], color="tab:orange")
    ax[1].set_ylabel("motion")
    ax[1].set_xlim(-WINDOW_S, 0)
    label, col = ("MOVING", "tab:red") if a["moving"] else ("STILL", "tab:green")
    ax[1].set_title(label, color=col, fontsize=16, fontweight="bold")

    ax[2].plot(a["t"], a["disp"], color="tab:purple")
    ax[2].set_xlim(-WINDOW_S, 0)
    ax[2].set_xlabel("time (s)")
    ax[2].set_ylabel("mm")
    ax[2].set_title(f"Displacement at {a['bin_cm']:.0f} cm (breathing shows as a slow wave)")
    fig.tight_layout()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--file")
    ap.add_argument("--min-cm", type=float, default=25.0, help="ignore bins closer than this")
    ap.add_argument("--snapshot", help="replay --file fast and save one image, no window")
    ap.add_argument("--mirror", help="also save the window to this PNG about once a second")
    ap.add_argument("--save", help="append the raw board lines to this file")
    a = ap.parse_args()

    stream = Stream()
    if a.snapshot:
        if not a.file:
            sys.exit("--snapshot needs --file")
        import matplotlib

        matplotlib.use("Agg")
        replay_file(stream, a.file, realtime=False)
        fig, ax = build_figure()
        draw(fig, ax, analyse(stream, a.min_cm), stream)
        fig.savefig(a.snapshot, dpi=100)
        print(f"saved {a.snapshot}")
        return

    if a.file:
        src = threading.Thread(target=replay_file, args=(stream, a.file), daemon=True)
    else:
        port = a.port or (sorted(glob.glob("/dev/cu.usbmodem*")) or [None])[0]
        if not port:
            sys.exit("no /dev/cu.usbmodem* port: check the cable is in the KitProg3 port")
        print(f"reading {port}  (close this window or press Ctrl+C to stop)")
        src = threading.Thread(target=read_serial, args=(stream, port, a.save), daemon=True)
    src.start()

    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    fig, ax = build_figure()
    last_mirror = [0.0]

    def update(_):
        draw(fig, ax, analyse(stream, a.min_cm), stream)
        if a.mirror and time.time() - last_mirror[0] >= 1.0:
            last_mirror[0] = time.time()
            fig.savefig(a.mirror + ".tmp.png", dpi=80)
            import os

            os.replace(a.mirror + ".tmp.png", a.mirror)  # readers never see a half-written file

    anim = FuncAnimation(fig, update, interval=150, cache_frame_data=False)
    plt.show()
    del anim


if __name__ == "__main__":
    main()
