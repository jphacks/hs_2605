"""Live radar viewer: shows where things move, how much, and the breathing wave.

Usage (repo root, venv active):
  python firmware/tools/live_view.py                 # board on /dev/cu.usbmodem*
  python firmware/tools/live_view.py --file lines.txt  # replay saved lines at real speed
  python firmware/tools/live_view.py --file lines.txt --snapshot view.png  # test, no window
  python firmware/tools/live_view.py --server        # while pc/ibuki/server.py owns the port

Window:
  top     - how much each range bin changes over the last 3 s (where the movement is)
  middle  - total motion over the last 20 s, with MOVING / STILL
  bottom  - displacement (mm) of the most active bin over the last 20 s (breathing wave)
"""
import argparse
import collections
import glob
import os
import sys
import threading
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "pc"))
from ibuki.shake import ShakeDetector  # noqa: E402  (shared with the server's placeholder Brain)

WINDOW_S = 20.0
PROFILE_S = 3.0
LAMBDA_MM = 5.0  # 60 GHz wavelength, ~4.96 mm
PRESENT_LEVEL = 15.0  # bin activity meaning "something is there"; tune with the empty-room recording
BREATH_BAND_HZ = (0.1, 1.2)  # adults ~0.2-0.33 Hz, babies ~0.5-1.0 Hz
NOISE_BAND_HZ = (1.5, 4.0)


def signal_quality(t, disp):
    """(SNR dB of the breathing band vs. a faster noise band, peak in breaths/min) or (None, None)."""
    if len(t) < 50 or t[-1] - t[0] < 10.0:
        return None, None
    fs = (len(t) - 1) / (t[-1] - t[0])
    x = (disp - disp.mean()) * np.hanning(len(disp))
    power = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1.0 / fs)
    band = (f >= BREATH_BAND_HZ[0]) & (f <= BREATH_BAND_HZ[1])
    noise = (f >= NOISE_BAND_HZ[0]) & (f <= NOISE_BAND_HZ[1])
    if not band.any() or not noise.any():
        return None, None
    snr_db = 10 * np.log10(power[band].mean() / max(power[noise].mean(), 1e-12))
    peak_per_min = f[band][np.argmax(power[band])] * 60.0
    return float(snr_db), float(peak_per_min)


class Stream:
    """Collects D lines (time, complex bins) from a serial port or a replayed file."""

    def __init__(self):
        self.cfg = (15.0, 37.5, 24, 10.0)  # replaced by the board's C line
        self.buf = collections.deque(maxlen=2000)
        self.lock = threading.Lock()
        self.last_seq = None
        self.gaps = 0
        self.status = ""
        self.min_cm = 28.0
        self.shake = ShakeDetector()

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
                rng = self.cfg[0] + np.arange(len(z)) * self.cfg[1] / 10.0
                shaking = self.shake.feed(z, rng >= self.min_cm)
                with self.lock:
                    self.buf.append((t_ms / 1000.0, z, shaking))
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


def read_server(stream, url, save_path=None):
    """Raw board lines from the IBUKI server (/ws/raw), so the server can own the port."""
    from websockets.sync.client import connect

    out = open(save_path, "a", encoding="ascii", errors="replace") if save_path else None
    while True:
        try:
            with connect(url, open_timeout=3) as ws:
                stream.status = "connected to server"
                for text in ws:
                    stream.feed(text)
                    if out:
                        out.write(text + "\n")
                        out.flush()
        except Exception:
            stream.status = "server not reachable, retrying"
            time.sleep(1.0)


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
    shake = np.array([d[2] for d in data])
    keep = t >= t[-1] - WINDOW_S
    t, z, shake = t[keep], z[keep], shake[keep]
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
    snr_db, peak = signal_quality(t, disp)
    return dict(t=t - t[-1], rng=rng, profile=profile, use=use, motion=motion,
                moving=moving, bin_cm=rng[b], disp=disp, present=profile[b] >= PRESENT_LEVEL,
                activity=float(profile[b]), snr_db=snr_db, peak_per_min=peak,
                gate=(float(rng[use][0]) if use.any() else float(rng[0]), float(rng[-1])),
                shake=shake, shaking=bool(shake[t >= t[-1] - 1.0].any()))


def quality_text(a):
    if a["shake"].any():
        return "signal quality: - (board moved in the last 20 s)"
    if not a["present"]:
        return "signal quality: - (nothing in range)"
    if a["snr_db"] is None:
        return "signal quality: collecting 10 s ..."
    q = a["snr_db"]
    word = "good" if q >= 10 else "ok" if q >= 5 else "weak"
    return f"signal quality {q:.0f} dB ({word})   breathing-band peak ~{a['peak_per_min']:.0f}/min"


def banner(a):
    if a["shaking"]:
        return "BOARD SHAKING - breathing unknown (keep the board still)", "tab:orange"
    if a["present"]:
        return f"IN RANGE - radar sees something at {a['bin_cm']:.0f} cm", "tab:green"
    lo, hi = a["gate"]
    return f"OUT OF RANGE - nothing moving between {lo:.0f} and {hi:.0f} cm", "gray"


class Camera:
    """TESTING AID ONLY: shows what is in front of the board next to the radar plots.

    IBUKI itself is camera-free. Frames are only drawn on screen: never saved, never sent,
    and hidden from the --mirror PNG. source: camera index ("0") or "fake" for tests.
    """

    def __init__(self, source="0", width=480):
        self.source = str(source)
        self.width = width
        self.frame = None
        self.error = None
        self._lock = threading.Lock()
        threading.Thread(target=self._run, daemon=True).start()

    def latest(self):
        with self._lock:
            return self.frame

    def _run(self):
        if self.source == "fake":  # moving block, for tests without a camera
            k = 0
            while True:
                img = np.full((270, self.width, 3), 60, np.uint8)
                x = int(self.width / 2 + 150 * np.sin(k / 10))
                img[100:170, max(0, x - 35):x + 35] = (230, 180, 120)
                with self._lock:
                    self.frame = img
                k += 1
                time.sleep(0.1)
        try:
            import cv2
        except ImportError:
            self.error = "OpenCV missing: ../env/bin/python -m pip install opencv-python"
            return
        cap = cv2.VideoCapture(int(self.source))
        if not cap.isOpened():
            self.error = ("camera not available: allow camera access for your terminal app in\n"
                          "System Settings > Privacy & Security > Camera, then restart the viewer")
            return
        while True:
            ok, bgr = cap.read()
            if not ok:
                self.error = "camera stopped sending frames"
                time.sleep(0.5)
                continue
            h, w = bgr.shape[:2]
            small = cv2.resize(bgr, (self.width, int(h * self.width / w)))
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)[:, ::-1]  # mirrored, like a selfie
            with self._lock:
                self.frame = np.ascontiguousarray(rgb)
                self.error = None


def build_figure(camera=False):
    import matplotlib.pyplot as plt

    if camera:
        fig = plt.figure(figsize=(15, 8))
        gs = fig.add_gridspec(3, 2, width_ratios=[1.0, 1.15])
        cam_ax = fig.add_subplot(gs[:, 0])
        ax = [fig.add_subplot(gs[i, 1]) for i in range(3)]
    else:
        fig, ax = plt.subplots(3, 1, figsize=(9, 8))
        cam_ax = None
    fig.canvas.manager.set_window_title("IBUKI radar live view")
    return fig, ax, cam_ax


def draw_camera(cam_ax, camera, a):
    cam_ax.clear()
    cam_ax.set_xticks([])
    cam_ax.set_yticks([])
    frame = camera.latest()
    if frame is not None:
        cam_ax.imshow(frame)
    else:
        cam_ax.text(0.5, 0.5, camera.error or "starting camera ...", ha="center", va="center",
                    transform=cam_ax.transAxes)
    if a is None:
        text, col, sub = "waiting for radar data ...", "gray", ""
    else:
        text, col = banner(a)
        sub = "MOVING" if a["moving"] else "STILL"
    cam_ax.text(0.02, 0.98, f"{text}\n{sub}", transform=cam_ax.transAxes, va="top", ha="left",
                fontsize=12, fontweight="bold", color="white",
                bbox=dict(facecolor=col, alpha=0.85, edgecolor="none"))
    for spine in cam_ax.spines.values():  # colored frame = what the radar thinks right now
        spine.set_edgecolor(col)
        spine.set_linewidth(6)
    cam_ax.set_title("TEST CAMERA (not part of IBUKI, nothing is saved)", fontsize=10, color="gray")


def draw(fig, ax, a, stream, cam_ax=None, camera=None):
    if cam_ax is not None and camera is not None:
        draw_camera(cam_ax, camera, a)
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
    for x in (ax[1], ax[2]):  # shade the moments when the board itself moved
        x.fill_between(a["t"], 0, 1, where=a["shake"], transform=x.get_xaxis_transform(),
                       color="tab:orange", alpha=0.2, lw=0)
    ax[1].set_ylabel("motion")
    ax[1].set_xlim(-WINDOW_S, 0)
    label, col = ("MOVING", "tab:red") if a["moving"] else ("STILL", "tab:green")
    ax[1].set_title(label, color=col, fontsize=16, fontweight="bold")

    ax[2].plot(a["t"], a["disp"], color="tab:purple")
    ax[2].set_xlim(-WINDOW_S, 0)
    ax[2].set_xlabel("time (s)")
    ax[2].set_ylabel("mm")
    ax[2].set_title(f"Displacement at {a['bin_cm']:.0f} cm - {quality_text(a)}")
    text, col = banner(a)
    fig.suptitle(text, color=col, fontsize=15, fontweight="bold")
    fig.tight_layout()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--file")
    ap.add_argument("--min-cm", type=float, default=28.0, help="ignore bins closer than this (antenna leakage)")
    ap.add_argument("--snapshot", help="replay --file fast and save one image, no window")
    ap.add_argument("--mirror", help="also save the window to this PNG about once a second")
    ap.add_argument("--save", help="append the raw board lines to this file")
    ap.add_argument("--server", nargs="?", const="ws://localhost:8000/ws/raw",
                    help="read through the running IBUKI server instead of the port")
    ap.add_argument("--camera", nargs="?", const="0", default=None,
                    help="TEST ONLY: show the laptop camera next to the plots (index, or 'fake')")
    a = ap.parse_args()

    stream = Stream()
    stream.min_cm = a.min_cm
    camera = Camera(a.camera) if a.camera is not None else None
    if a.snapshot:
        if not a.file:
            sys.exit("--snapshot needs --file")
        import matplotlib

        matplotlib.use("Agg")
        replay_file(stream, a.file, realtime=False)
        if camera:
            time.sleep(0.5)  # let the first frame arrive
        fig, ax, cam_ax = build_figure(camera is not None)
        draw(fig, ax, analyse(stream, a.min_cm), stream, cam_ax, camera)
        fig.savefig(a.snapshot, dpi=100)
        print(f"saved {a.snapshot}")
        return

    if a.file:
        src = threading.Thread(target=replay_file, args=(stream, a.file), daemon=True)
    elif a.server:
        print(f"reading through the server {a.server}  (Ctrl+C to stop)")
        src = threading.Thread(target=read_server, args=(stream, a.server, a.save), daemon=True)
    else:
        port = a.port or (sorted(glob.glob("/dev/cu.usbmodem*")) or [None])[0]
        if not port:
            sys.exit("no /dev/cu.usbmodem* port: check the cable is in the KitProg3 port")
        print(f"reading {port}  (close this window or press Ctrl+C to stop)")
        src = threading.Thread(target=read_serial, args=(stream, port, a.save), daemon=True)
    src.start()

    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    fig, ax, cam_ax = build_figure(camera is not None)
    last_mirror = [0.0]

    def update(_):
        draw(fig, ax, analyse(stream, a.min_cm), stream, cam_ax, camera)
        if a.mirror and time.time() - last_mirror[0] >= 1.0:
            last_mirror[0] = time.time()
            if cam_ax is not None:
                cam_ax.set_visible(False)  # the camera picture is never written to disk
            fig.savefig(a.mirror + ".tmp.png", dpi=80)
            if cam_ax is not None:
                cam_ax.set_visible(True)
            os.replace(a.mirror + ".tmp.png", a.mirror)  # readers never see a half-written file

    anim = FuncAnimation(fig, update, interval=150, cache_frame_data=False)
    plt.show()
    del anim


if __name__ == "__main__":
    main()
