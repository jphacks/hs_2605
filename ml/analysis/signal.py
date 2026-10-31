"""Exploratory windowed radar diagnostics; frequencies are not breathing labels."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math

import numpy as np
from scipy.signal import periodogram

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
    spectral_status: str = "insufficient_samples"
    sampling_interval_ms: float | None = None
    interval_tolerance_ms: float | None = None
    max_interval_deviation_ms: float | None = None
    max_grid_deviation_ms: float | None = None
    spectral_resolution_hz: float | None = None
    dominant_frequency_hz: float | None = None
    spectral_concentration: float | None = None


@dataclass(frozen=True)
class SignalReport:
    bin_index: int
    distance_cm: float
    window_sec: float
    hop_sec: float
    quality: QualityReport
    spectral_policy: dict
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


def _with_spectrum(metrics: WindowMetrics, times: list[int], sequences: list[int],
                   z: np.ndarray, has_issue: bool) -> WindowMetrics:
    """Reject gaps/jitter before evaluating a Hann periodogram on measured cadence."""
    if len(z) < 2:
        return metrics
    relative = np.array([t - times[0] for t in times], dtype=float)
    interval = float(relative[-1] / (len(z) - 1))
    tolerance = max(1.0, .02 * interval)
    interval_error = float(np.max(np.abs(np.diff(relative) - interval)))
    grid_error = float(np.max(np.abs(relative - np.arange(len(z)) * interval)))
    metrics = replace(metrics, sampling_interval_ms=interval, interval_tolerance_ms=tolerance,
                      max_interval_deviation_ms=interval_error, max_grid_deviation_ms=grid_error)
    status = None
    if len(z) < 8:
        status = "insufficient_samples"
    elif not metrics.complete_window:
        status = "partial_window"
    elif any(b != a + 1 for a, b in zip(sequences, sequences[1:])):
        status = "sequence_gap"
    elif has_issue:
        status = "parser_issues"
    elif interval_error > tolerance or grid_error > tolerance:
        status = "irregular_timing"
    elif (times[0] - metrics.window_start_t_ms > 1.5 * interval
          or metrics.window_end_t_ms - times[-1] > 1.5 * interval):
        status = "incomplete_coverage"
    if status is not None:
        return replace(metrics, spectral_status=status)
    wave = project_iq(z)
    amplitude = float(np.max(np.abs(wave)))
    if amplitude == 0:
        return replace(metrics, spectral_status="constant_signal")
    # Normalize only for numeric stability; ratios and peak frequency are unchanged.
    fs = 1000 / interval
    frequencies, power = periodogram(wave / amplitude, fs=fs, window="hann",
                                     detrend=False, scaling="spectrum")
    power[0] = 0  # exclude DC from both peak and concentration denominator
    total = float(power.sum())
    if not math.isfinite(total) or total <= 0:
        return replace(metrics, spectral_status="constant_signal")
    peak = int(np.argmax(power))
    concentration = float(power[max(1, peak - 1):peak + 2].sum() / total)
    return replace(metrics, spectral_status="available", spectral_resolution_hz=fs / len(z),
                   dominant_frequency_hz=float(frequencies[peak]),
                   spectral_concentration=min(1.0, max(0.0, concentration)))


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
        metrics = WindowMetrics(
            first + start, first + end,
            capture.frames[left].data.t_ms if len(z) else None,
            capture.frames[right - 1].data.t_ms if len(z) else None,
            len(z), end <= offsets[-1], rms, projected_rms)
        selected = capture.frames[left:right]
        has_issue = bool(selected) and any(selected[0].line_number < issue.line_number
                                          < selected[-1].line_number for issue in capture.issues)
        windows.append(_with_spectrum(metrics, [f.data.t_ms for f in selected],
                                      [f.data.seq for f in selected], z, has_issue))
    return SignalReport(bin_index, distance, window_ms / 1000, hop_ms / 1000,
                        quality, {"minimum_samples": 8, "relative_timing_tolerance": .02,
                                  "absolute_timing_tolerance_ms": 1.0,
                                  "periodogram_window": "hann", "exclude_dc": True,
                                  "concentration_peak_radius_bins": 1}, tuple(windows))


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    from .protocol import read_capture

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    parser.add_argument("--bin", type=int, default=0, dest="bin_index")
    parser.add_argument("--window-sec", type=float, default=12)
    parser.add_argument("--hop-sec", type=float, default=3)
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    args = parser.parse_args(argv)
    try:
        report = analyze_signal(read_capture(args.path), args.bin_index,
                                window_sec=args.window_sec, hop_sec=args.hop_sec)
    except (OSError, ValueError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    if args.json:
        print(json.dumps(report.to_dict(), sort_keys=True, indent=2, allow_nan=False))
    else:
        print(f"Exploratory signal diagnostics: bin {report.bin_index}, {report.distance_cm:g} cm")
        print("Board uptime windows; frequency peaks are not breathing or apnea classifications.")
        for window in report.windows:
            print(f"[{window.window_start_t_ms}, {window.window_end_t_ms}) ms: "
                  f"n={window.observed_samples}, RMS={window.rms_variation}, "
                  f"peak_hz={window.dominant_frequency_hz}, "
                  f"concentration={window.spectral_concentration}, {window.spectral_status}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
