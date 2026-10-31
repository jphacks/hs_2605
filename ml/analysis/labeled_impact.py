"""Paired, labeled per-slot CNN evaluation; not apnea/state/alarm validation."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ml.features import NUM_BINS
from ml.train import (BREATHING_WITHIN_S, FAR_PER_WINDOW, FAR_SLOTS, PARENT_BODY_SLOTS,
                      STOPPED_FOR_S, slot_of)

from .denoise import FilterParameters
from .model_impact import FeatureComparison, prepare_features
from .protocol import Capture, read_capture

DATA = Path(__file__).resolve().parents[1] / "data"
GROUPS = ("baby_breathing", "baby_stopped", "parent", "far")


@dataclass(frozen=True)
class SessionTruth:
    baby_slot: int
    parent_slot: int | None
    base: str
    since_breath_s: np.ndarray
    metadata: dict


@dataclass(frozen=True)
class LabeledSession:
    source: dict
    prepared: FeatureComparison
    examples: tuple[dict, ...]
    windows: tuple[dict, ...]
    exclusions: dict[str, int]


def validate_truth(capture: Capture, truth: dict) -> SessionTruth:
    """One truth entry per parsed D; exact timestamps prevent positional drift."""
    if not isinstance(truth, dict):
        raise ValueError("Session truth must be a JSON object")
    required = {"t_ms", "since_breath_s", "baby_cm", "parent_cm", "base"}
    if missing := required - truth.keys():
        raise ValueError("Missing truth fields: " + ", ".join(sorted(missing)))
    times, ages = truth["t_ms"], truth["since_breath_s"]
    if not isinstance(times, list) or not isinstance(ages, list) or len(times) != len(capture.frames) or len(ages) != len(times):
        raise ValueError("Truth t_ms/since_breath_s lengths must match every observed D frame")
    if any(isinstance(t, bool) or not isinstance(t, int) or t != f.data.t_ms
           for t, f in zip(times, capture.frames)):
        raise ValueError("Truth t_ms must exactly match D-frame timestamps in order")
    since = []
    for value in ages:
        if value is None:
            since.append(float("nan"))
        elif isinstance(value, bool) or not isinstance(value, (int, float)) or math.isinf(value) or value < 0:
            raise ValueError("since_breath_s must be nonnegative numbers or null/NaN for unlabeled samples")
        else:
            since.append(float(value))
    def distance_slot(value, field):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{field} must be a finite distance")
        slot = slot_of(value)
        if not 0 <= slot < NUM_BINS:
            raise ValueError(f"{field} maps outside the trained radar bins")
        return slot
    baby = distance_slot(truth["baby_cm"], "baby_cm")
    parent = None if truth["parent_cm"] is None else distance_slot(truth["parent_cm"], "parent_cm")
    if not isinstance(truth["base"], str) or not truth["base"]:
        raise ValueError("Truth base must identify the source recording; do not infer it from the CSV filename")
    metadata = {key: truth.get(key) for key in ("kind", "subject", "origin")}
    if any(value is not None and not isinstance(value, str) for value in metadata.values()):
        raise ValueError("Truth kind/subject/origin metadata must be strings when present")
    return SessionTruth(baby, parent, truth["base"], np.array(since), metadata)


def label_window(truth: SessionTruth, frame_index: int, rng) -> tuple[list[dict], dict]:
    """Reuse train.py semantics; classify only centers and sampled eligible far bins.

    Same-slot baby/parent centers are conservatively excluded as ambiguous,
    rather than duplicating or assigning conflicting labels to one slot.
    """
    if not 0 <= frame_index < len(truth.since_breath_s):
        raise ValueError("Label frame index is out of range")
    kb, kp = truth.baby_slot, truth.parent_slot
    excluded = {k: "near_or_unlabeled" for k in range(NUM_BINS)}
    labels = []
    age = truth.since_breath_s[frame_index]
    if kb == kp:
        excluded[kb] = "overlapping_centers"
    else:
        if np.isnan(age):
            excluded[kb] = "baby_unlabeled"
        elif age < BREATHING_WITHIN_S:
            labels.append({"bin_index": kb, "label": 1, "group": "baby_breathing"})
            del excluded[kb]
        elif age >= STOPPED_FOR_S:
            labels.append({"bin_index": kb, "label": 0, "group": "baby_stopped"})
            del excluded[kb]
        else:
            excluded[kb] = "baby_transition_5_to_8_s"
        if kp is not None:
            if "rec_hold" in truth.base:
                excluded[kp] = "parent_hold_unknown"
            else:
                labels.append({"bin_index": kp, "label": 1, "group": "parent"})
                del excluded[kp]
    far = [k for k in range(NUM_BINS) if abs(k-kb) >= FAR_SLOTS
           and (kp is None or abs(k-kp) >= PARENT_BODY_SLOTS)]
    for k in far:
        excluded[k] = "far_not_sampled"
    for k in rng.choice(far, size=min(FAR_PER_WINDOW, len(far)), replace=False):
        labels.append({"bin_index": int(k), "label": 0, "group": "far"})
        del excluded[int(k)]
    return labels, dict(sorted(Counter(excluded.values()).items()))


def discover_sessions(data_root: str | Path = DATA, split: str = "val") -> list[Path]:
    """Match train.py's split/*/*.csv layout; never download or synthesize data."""
    if split not in ("val", "test"):
        raise ValueError("Only val or explicitly selected test splits are supported")
    folder = Path(data_root) / split
    paths = sorted(folder.glob("*/*.csv")) if folder.is_dir() else []
    if not paths:
        raise ValueError(f"No labeled sessions found in {folder}. Supply existing {split}/<kind>/*.csv "
                         "and matching .json truth files via --data-root. See ml/README.md data setup; "
                         "this command never downloads AIR-400 or generates a dataset.")
    missing = [str(path.with_suffix(".json")) for path in paths if not path.with_suffix(".json").is_file()]
    if missing:
        raise ValueError("Missing matching session truth files: " + ", ".join(missing))
    return paths


def prepare_labeled_session(csv: Path, session_id: str, rng, *, hop_samples: int = 8,
                            max_windows: int = 12,
                            parameters: FilterParameters = FilterParameters()) -> LabeledSession:
    capture = read_capture(csv)
    truth_path = csv.with_suffix(".json")
    truth = validate_truth(capture, json.loads(truth_path.read_text(encoding="utf-8-sig")))
    prepared = prepare_features(capture, 0, hop_samples=hop_samples, max_windows=max_windows, parameters=parameters)
    windows, examples, exclusions = [], [], Counter()
    for index, window in enumerate(prepared.windows):
        # prepare_features ends are exclusive; train.features.windows yields end-1.
        frame_index = window["end_index_exclusive"] - 1
        labels, omitted = label_window(truth, frame_index, rng)
        exclusions.update(omitted)
        windows.append({**window, "label_frame_index": frame_index,
                        "labeled_examples": len(labels), "exclusions": omitted})
        for label in labels:
            examples.append({**label, "id": f"{session_id}:{frame_index}:{label['bin_index']}",
                             "window_index": index, "label_frame_index": frame_index,
                             "label_t_ms": capture.frames[frame_index].data.t_ms})
    source = {"session_id": session_id, "base": truth.base, **truth.metadata,
              "baby_slot": truth.baby_slot, "parent_slot": truth.parent_slot,
              "csv_sha256": hashlib.sha256(csv.read_bytes()).hexdigest(),
              "truth_sha256": hashlib.sha256(truth_path.read_bytes()).hexdigest()}
    return LabeledSession(source, prepared, tuple(examples), tuple(windows), dict(sorted(exclusions.items())))
