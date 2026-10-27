"""Breath timings of real babies, from the AIR-400 infant dataset.

AIR-400 (Northeastern University, WACV 2026) is a set of overnight baby-monitor videos of real
infants, where people marked every breath by hand. We don't use the videos themselves: our radar
can't learn from pictures. We use the *timing* of the breaths (rate, rhythm, natural pauses) to
drive the simulated baby chest in ml/synth.py.

    https://coe.northeastern.edu/Research/AClab/AIR-400/   (dataset, open download)
    https://github.com/michaelwwan/air-400                  (code, MIT)

The dataset's own readme states no license, so: keep the downloaded files out of git (ml/data/ is
git-ignored), cite the paper, and ask the authors before using it in anything public.

What one label file holds (checked by hand on S01/1.hdf5):
    filename     the video it belongs to ("1.mp4")
    respiration  one value per video frame: 0 most of the time, with a small Gaussian bump
                 (max ~0.2) wherever an annotator marked a breath. The paper says the marks are
                 "exhalation onsets", i.e. the moment the chest is fullest.
So a breath = a local peak of `respiration`. To turn frame numbers into seconds we need the
video's frame rate, which the label file doesn't say. We read the video's length from its MP4
header ("mvhd" box) with two small HTTP range requests, without downloading the whole video.

Run from the repo root:
    ../env/bin/python -m ml.air400            # download (once) and extract
Output:
    ml/data/air400/<subject>/<n>.hdf5       the label files, as downloaded
    ml/data/air400/durations.json           video length per clip (seconds)
    ml/data/air400_breaths.json             breath times per clip (read by ml/synth.py)
"""
from __future__ import annotations

import json
import re
import struct
import sys
import urllib.request
from pathlib import Path

import h5py
import numpy as np

BASE = "https://coe.northeastern.edu/Research/AClab/AIR-400/"
SUBJECTS = [f"S{i:02d}" for i in range(1, 11)]  # S01..S10: the 10 newly recorded babies

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RAW = DATA / "air400"
OUT = DATA / "air400_breaths.json"

# A marked breath is a Gaussian bump with a max of ~0.2; anything above this counts as a peak.
PEAK_MIN = 0.1


def _get(url: str, byte_range: str | None = None, timeout: float = 30.0) -> bytes:
    """GET a URL (optionally only part of it: byte_range like "0-65535" or "-262144")."""
    req = urllib.request.Request(url, headers={"User-Agent": "ibuki-ml/1.0"})
    if byte_range:
        req.add_header("Range", f"bytes={byte_range}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _listing(url: str, ext: str) -> list[str]:
    """File names with this extension in an Apache directory listing ("Index of ...")."""
    html = _get(url).decode("utf-8", "replace")
    names = re.findall(r'href="([^"?/]+\.' + ext + r')"', html)
    # sort 1, 2, ..., 10 as numbers, not as text ("10" would come before "2")
    return sorted(set(names), key=lambda n: (len(n), n))


def _mvhd_seconds(blob: bytes) -> float | None:
    """Video length from the MP4 'mvhd' box, if this chunk of the file contains it.

    mvhd layout after the 4-byte name: version (1 byte) + flags (3), then
      version 0: creation (4), modification (4), timescale (4), duration (4)
      version 1: creation (8), modification (8), timescale (4), duration (8)
    duration / timescale = seconds.
    """
    i = blob.find(b"mvhd")
    if i < 0 or i + 40 > len(blob):
        return None
    version = blob[i + 4]
    if version == 0:
        timescale, duration = struct.unpack(">II", blob[i + 16:i + 24])
    else:
        timescale, duration = struct.unpack(">IQ", blob[i + 24:i + 36])
    return duration / timescale if timescale else None


def video_seconds(url: str) -> float | None:
    """Length of an MP4 without downloading it: the header ('moov') is at the start of the
    file or at its end, so try the first 128 KB, then the last 512 KB."""
    for byte_range in ("0-131071", "-524288"):
        try:
            secs = _mvhd_seconds(_get(url, byte_range))
        except OSError:
            secs = None
        if secs:
            return secs
    return None


def download() -> dict[str, float]:
    """Fetch every label file (skipping ones we already have) and every clip's video length."""
    RAW.mkdir(parents=True, exist_ok=True)
    dur_file = RAW / "durations.json"
    durations: dict[str, float] = json.loads(dur_file.read_text()) if dur_file.exists() else {}
    for subj in SUBJECTS:
        (RAW / subj).mkdir(exist_ok=True)
        labels = _listing(f"{BASE}{subj}/out/", "hdf5")
        for name in labels:
            dest = RAW / subj / name
            if not dest.exists():
                dest.write_bytes(_get(f"{BASE}{subj}/out/{name}"))
            key = f"{subj}/{dest.stem}"
            if key not in durations:
                # the label "out/7.hdf5" belongs to the video "7.mp4" in the subject's folder
                secs = video_seconds(f"{BASE}{subj}/{dest.stem}.mp4")
                if secs:
                    durations[key] = round(secs, 3)
        print(f"{subj}: {len(labels)} label files", file=sys.stderr)
        dur_file.write_text(json.dumps(durations, indent=1))  # save as we go: the server is slow
    return durations


def breath_frames(resp: np.ndarray) -> list[int]:
    """Frame index of every marked breath: local maxima of the bump signal above PEAK_MIN.
    (`>=` on the left and `>` on the right, so a flat-topped bump gives exactly one peak.)"""
    r = np.asarray(resp, float)
    idx = np.flatnonzero((r[1:-1] > PEAK_MIN) & (r[1:-1] >= r[:-2]) & (r[1:-1] > r[2:])) + 1
    return idx.tolist()


def extract(durations: dict[str, float]) -> list[dict]:
    clips = []
    for subj in SUBJECTS:
        for path in sorted((RAW / subj).glob("*.hdf5"), key=lambda p: (len(p.stem), p.stem)):
            key = f"{subj}/{path.stem}"
            with h5py.File(path, "r") as f:
                resp = f["respiration"][()]
            secs = durations.get(key)
            if not secs:
                print(f"skip {key}: video length unknown", file=sys.stderr)
                continue
            fps = len(resp) / secs  # label samples are one per video frame
            frames = breath_frames(resp)
            times = [round(i / fps, 3) for i in frames]
            gaps = np.diff(times)
            clips.append({
                "subject": subj,
                "clip": path.stem,
                "duration_s": secs,
                "fps": round(fps, 3),
                "breaths_s": times,  # seconds from the start of the clip
                "bpm": round(len(times) / secs * 60.0, 1),
                "longest_gap_s": round(float(gaps.max()), 2) if len(gaps) else None,
            })
    return clips


def summary(clips: list[dict]) -> None:
    """Print what we got: how much breathing, at what rates, and how long the natural pauses are."""
    bpm = np.array([c["bpm"] for c in clips])
    gaps = np.concatenate([np.diff(c["breaths_s"]) for c in clips if len(c["breaths_s"]) > 1])
    minutes = sum(c["duration_s"] for c in clips) / 60.0
    fps = sorted({c["fps"] for c in clips})
    print(f"clips: {len(clips)} from {len({c['subject'] for c in clips})} babies, {minutes:.0f} min, "
          f"{sum(len(c['breaths_s']) for c in clips)} breaths, frame rates {fps[0]}-{fps[-1]} fps")
    print(f"breaths per minute per clip: median {np.median(bpm):.0f}, "
          f"10-90% {np.percentile(bpm, 10):.0f}-{np.percentile(bpm, 90):.0f}, range {bpm.min():.0f}-{bpm.max():.0f}")
    print(f"time between breaths: median {np.median(gaps):.2f} s, 99% under {np.percentile(gaps, 99):.1f} s, "
          f"longest {gaps.max():.1f} s; pauses over 5 s: {int((gaps > 5).sum())}, over 10 s: {int((gaps > 10).sum())}")


if __name__ == "__main__":
    durations = download()
    clips = extract(durations)
    OUT.write_text(json.dumps(clips, indent=1))
    summary(clips)
    print(f"wrote {OUT.relative_to(HERE.parent)}")
