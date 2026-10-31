"""Exploratory windowed radar diagnostics; frequencies are not breathing labels."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np

from .protocol import Capture
from .quality import QualityReport, analyze_quality


@dataclass(frozen=True)
class WindowMetrics:
    window_start_t_ms: int
    window_end_t_ms: int
    observed_start_t_ms: int | None
    observed_end_t_ms: int | None
    observed_samples: int
    complete_window: bool
    rms_variation: float | None
    projected_rms: float | None


@dataclass(frozen=True)
class SignalReport:
    bin_index: int
    distance_cm: float
    window_sec: float
    hop_sec: float
    quality: QualityReport
    windows: tuple[WindowMetrics, ...]

    def to_dict(self) -> dict:
        """Return detached JSON-compatible metrics including whole-file quality."""
        result = asdict(self)
        result["windows"] = list(result["windows"])
        return result


def project_iq(z: np.ndarray) -> np.ndarray:
    """ml.features' single-bin linear projection, without normalization/decimation.

    Subtract the temporal mean, then rotate by half the angle of sum(centered**2)
    and retain the real component. The sign of that axis is not physical motion.
    """
    if not len(z):
        return np.array([], dtype=float)
    if np.all(z == z[0]):
        return np.zeros(len(z))
    centered = z - z.mean()
    axis = np.angle((centered ** 2).sum()) / 2
    return (centered * np.exp(-1j * axis)).real


def _milliseconds(value: float, name: str) -> int:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
    milliseconds = value * 1000
    if not math.isfinite(milliseconds) or abs(milliseconds - round(milliseconds)) > 1e-6:
        raise ValueError(f"{name} must resolve to whole milliseconds")
    if round(milliseconds) < 1:
        raise ValueError(f"{name} must be at least 0.001 seconds")
    return round(milliseconds)


def analyze_signal(capture: Capture, bin_index: int = 0, *,
                   window_sec: float = 12, hop_sec: float = 3) -> SignalReport:
    """Use half-open board-uptime windows, anchored at the first observed D.

    Emit every hop start through the last D, including explicit empty/partial
    windows. Reject ambiguous configuration or time order; never repair samples.
    """
    window_ms = _milliseconds(window_sec, "window_sec")
    hop_ms = _milliseconds(hop_sec, "hop_sec")
    quality = analyze_quality(capture)
    if not capture.frames:
        raise ValueError("No parsed D frames; empty/E-only captures are unsupported")
    if quality.frames_without_usable_configuration:
        raise ValueError("All D frames require usable C configuration and matching I/Q bins")
    config = capture.frames[0].config
    assert config is not None
    if any(f.config != config for f in capture.frames) or any(c != config for _, c in capture.configs):
        raise ValueError("Configuration changes are unsupported")
    if isinstance(bin_index, bool) or not isinstance(bin_index, int) or not 0 <= bin_index < config.n:
        raise ValueError(f"Bin index must be an integer from 0 to {config.n - 1}")
    if (quality.repeated_timestamp_events or quality.backward_timestamp_events
            or quality.duplicate_sequence_events or quality.backward_sequence_events):
        raise ValueError("Time and seq must strictly increase; resets/duplicates are unsupported")
    first = capture.frames[0].data.t_ms
    offsets = [f.data.t_ms - first for f in capture.frames]
    try:
        values = np.array([f.z()[bin_index] for f in capture.frames])
        distance = float(config.ranges_cm()[bin_index])
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Cannot scale selected bin: {exc}") from exc
    if not np.isfinite(values).all() or not math.isfinite(distance):
        raise ValueError("Scaled signal and distance must be finite")
    windows = []
    from bisect import bisect_left
    for start in range(0, offsets[-1] + 1, hop_ms):
        end = start + window_ms
        left, right = bisect_left(offsets, start), bisect_left(offsets, end)
        z = values[left:right]
        rms = projected_rms = None
        if len(z) >= 2:
            centered = z - z.mean() if not np.all(z == z[0]) else np.zeros_like(z)
            with np.errstate(over="ignore", invalid="ignore"):
                rms = float(np.sqrt(np.mean(np.abs(centered) ** 2)))
                projected_rms = float(np.sqrt(np.mean(project_iq(z) ** 2)))
            if not math.isfinite(rms) or not math.isfinite(projected_rms):
                raise ValueError("Signal variation overflowed; values must be numerically representable")
        windows.append(WindowMetrics(
            first + start, first + end,
            capture.frames[left].data.t_ms if len(z) else None,
            capture.frames[right - 1].data.t_ms if len(z) else None,
            len(z), end <= offsets[-1], rms, projected_rms))
    return SignalReport(bin_index, distance, window_ms / 1000, hop_ms / 1000,
                        quality, tuple(windows))
