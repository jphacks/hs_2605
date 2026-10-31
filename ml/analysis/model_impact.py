"""Offline feature/prediction drift of one fixed CNN; not labeled accuracy."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ml.features import CHANNELS, NUM_BINS, SCALE, STEP_CM, STEPS, WINDOW, slot_cm, window_features

from .denoise import FilterParameters, filter_iq, prepare_capture
from .protocol import Capture, Config
from .quality import QualityReport


@dataclass(frozen=True)
class FeatureComparison:
    bin_index: int
    conditions: dict[str, np.ndarray]
    feature_sets: dict[str, np.ndarray]
    windows: tuple[dict, ...]
    cadence: dict
    quality: QualityReport
    candidate_windows: int
    hop_samples: int
    max_windows: int


def select_window_ends(sample_count: int, hop_samples: int = 8,
                       max_windows: int = 12) -> tuple[list[int], int]:
    """Select evenly spaced candidate ranks, including first/last if K > 1.

    Ends are exclusive, as in ml.features.windows. Rank j is floor(j*(M-1)/(K-1));
    K=1 selects the first window. No timestamps or samples are interpolated.
    """
    for name, value in (("hop_samples", hop_samples), ("max_windows", max_windows)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    candidates = range(WINDOW, sample_count + 1, hop_samples)
    count = len(candidates)
    if not count:
        raise ValueError(f"At least {WINDOW} observed D frames are required for a CNN window")
    selected = min(count, max_windows)
    ranks = [0] if selected == 1 else [j * (count - 1) // (selected - 1) for j in range(selected)]
    return [candidates[rank] for rank in ranks], count


def prepare_features(capture: Capture, bin_index: int = 9, *, hop_samples: int = 8,
                     max_windows: int = 12,
                     parameters: FilterParameters = FilterParameters()) -> FeatureComparison:
    """Validate trained geometry, filter all bins continuously, share sample windows.

    Scale=10 is required because the existing training/live D readers divide by
    ml.features.SCALE, rather than interpreting arbitrary recording scales.
    """
    ends, count = select_window_ends(len(capture.frames), hop_samples, max_windows)
    data, _, cadence = prepare_capture(capture, bin_index)
    expected = Config(float(slot_cm(0)), STEP_CM * 10, NUM_BINS, float(SCALE))
    if data.config != expected:
        raise ValueError(f"Trained breath_slot model requires C,{expected.first_cm:g},"
                         f"{expected.step_mm:g},{expected.n},{expected.scale:g}")
    parameters.validate(cadence["sample_hz"])
    conditions = {name: np.empty_like(data.z) for name in ("baseline", "lowpass", "bandpass")}
    for k in range(NUM_BINS):
        filtered = filter_iq(data.z[:, k], cadence["sample_hz"], parameters)
        for name in conditions:
            conditions[name][:, k] = filtered[name]
    windows = []
    for end in ends:
        start = end - WINDOW
        first, last = capture.frames[start], capture.frames[end-1]
        windows.append({"start_index": start, "end_index_exclusive": end,
                        "start_source_line": first.line_number, "end_source_line": last.line_number,
                        "start_t_ms": first.data.t_ms, "evaluation_t_ms": last.data.t_ms})
    feature_sets = {}
    for name, values in conditions.items():
        features = np.stack([window_features(values[end-WINDOW:end]) for end in ends])
        if features.shape != (len(ends), NUM_BINS, STEPS, CHANNELS) or not np.isfinite(features).all():
            raise ValueError("Model features have incompatible shape or nonfinite values")
        feature_sets[name] = features
    return FeatureComparison(bin_index, conditions, feature_sets, tuple(windows), cadence,
                             data.quality, count, hop_samples, max_windows)
