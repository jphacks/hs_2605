"""Headless offline D-capture visualization, without signal interpretation."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .protocol import Capture, Config
from .quality import QualityReport, analyze_quality


@dataclass(frozen=True)
class VisualizationData:
    elapsed_s: np.ndarray
    distances_cm: np.ndarray
    z: np.ndarray
    variation: np.ndarray
    bin_index: int
    config: Config
    start_t_ms: int
    trace_segments: tuple[slice, ...]
    quality: QualityReport


def prepare_visualization(capture: Capture, bin_index: int = 0) -> VisualizationData:
    """Prepare actual sample coordinates and |z - temporal mean(z)| per bin.

    Reject ambiguous geometry/time order. Malformed rows remain in the whole
    capture quality summary; parsed D frames are plotted without interpolation.
    """
    quality = analyze_quality(capture)
    if not capture.frames:
        raise ValueError("No parsed D frames (empty or E-only captures cannot be plotted)")
    if quality.frames_without_usable_configuration:
        raise ValueError("Every D frame needs usable C configuration and matching I/Q bin counts")
    config = capture.frames[0].config
    assert config is not None
    if any(frame.config != config for frame in capture.frames) or any(
            cfg != config for _, cfg in capture.configs):
        raise ValueError("Configuration changes are unsupported: geometry or scale must stay constant")
    if isinstance(bin_index, bool) or not isinstance(bin_index, int) or not 0 <= bin_index < config.n:
        raise ValueError(f"Bin index must be an integer from 0 to {config.n - 1}; got {bin_index!r}")
    if quality.repeated_timestamp_events or quality.backward_timestamp_events:
        raise ValueError("Timestamps must strictly increase; repeated/backward board time is unsupported")
    if quality.duplicate_sequence_events or quality.backward_sequence_events:
        raise ValueError("Duplicate/backward sequence numbers or suspected restarts are unsupported")
    start = capture.frames[0].data.t_ms
    elapsed = np.array([(frame.data.t_ms - start) / 1000 for frame in capture.frames])
    try:
        z = np.asarray([frame.z() for frame in capture.frames], dtype=np.complex128)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Cannot scale I/Q values: {exc}") from exc
    with np.errstate(over="ignore", invalid="ignore"):
        variation = np.abs(z - z.mean(axis=0))
        distances = np.asarray(config.ranges_cm())
    if not all(np.isfinite(values).all() for values in (elapsed, distances, z, variation)):
        raise ValueError("Scaled values, distances and elapsed times must be finite")
    # Line connections express only short, uninterrupted adjacency; heatmap
    # columns always remain at the actual sample times regardless of segments.
    threshold = quality.long_interval_threshold_ms
    if threshold is None and len(capture.frames) > 1:
        threshold = 3 * min(b.data.t_ms - a.data.t_ms
                            for a, b in zip(capture.frames, capture.frames[1:]))
    issue_lines = sorted({issue.line_number for issue in capture.issues})
    breaks = [0]
    for k, (a, b) in enumerate(zip(capture.frames, capture.frames[1:]), 1):
        if (b.data.seq != a.data.seq + 1
                or (threshold is not None and b.data.t_ms - a.data.t_ms > threshold)
                or any(a.line_number < line < b.line_number for line in issue_lines)):
            breaks.append(k)
    breaks.append(len(capture.frames))
    segments = tuple(slice(a, b) for a, b in zip(breaks, breaks[1:]))
    return VisualizationData(elapsed, distances, z, variation, bin_index, config,
                             start, segments, quality)
