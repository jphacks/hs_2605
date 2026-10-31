"""Offline causal I/Q filter experiments, never a live filter recommendation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
from scipy.signal import butter, sosfilt, sosfilt_zi

from .protocol import Capture
from .visualize import prepare_visualization


@dataclass(frozen=True)
class FilterParameters:
    order: int = 2
    lowpass_hz: float = 1.5
    band_low_hz: float = .1
    band_high_hz: float = 1.5

    def validate(self, sample_hz: float) -> None:
        if not math.isfinite(sample_hz) or sample_hz <= 0:
            raise ValueError("Sample frequency must be positive and finite")
        if isinstance(self.order, bool) or not isinstance(self.order, int) or not 1 <= self.order <= 12:
            raise ValueError("Filter order must be an integer from 1 to 12")
        if not all(math.isfinite(v) and 0 < v < sample_hz / 2 for v in
                   (self.lowpass_hz, self.band_low_hz, self.band_high_hz)):
            raise ValueError("All cutoffs must be finite, positive, and below measured Nyquist")
        if self.band_low_hz >= self.band_high_hz:
            raise ValueError("Band-pass lower cutoff must be below its upper cutoff")


@dataclass(frozen=True)
class SyntheticCase:
    t_ms: np.ndarray
    clean: np.ndarray
    observed: np.ndarray
    seed: int
    requested_sample_hz: float
    motion_hz: float = .3
    cessation_sec: float = 40
    warmup_sec: float = 10


def validate_cadence(t_ms) -> dict:
    """Use PR #4's 1 ms/2% interval AND cumulative grid tolerance; no resampling."""
    times = [int(t) for t in t_ms]
    if len(times) < 8:
        raise ValueError("At least eight observed samples are required")
    offsets = np.array([t - times[0] for t in times], dtype=float)
    differences = np.diff(offsets)
    if not np.isfinite(offsets).all() or np.any(differences <= 0):
        raise ValueError("Board timestamps must be finite and strictly increasing")
    interval = float(offsets[-1] / (len(times) - 1))
    tolerance = max(1.0, .02 * interval)
    interval_error = float(np.max(np.abs(differences - interval)))
    grid_error = float(np.max(np.abs(offsets - np.arange(len(times)) * interval)))
    if interval_error > tolerance or grid_error > tolerance:
        raise ValueError("Irregular timestamps: interval/grid deviations exceed the 1 ms/2% policy")
    return {"sample_hz": 1000 / interval, "interval_ms": interval,
            "tolerance_ms": tolerance, "max_interval_deviation_ms": interval_error,
            "max_grid_deviation_ms": grid_error}


def design_filters(sample_hz: float, parameters: FilterParameters) -> dict[str, np.ndarray | None]:
    parameters.validate(sample_hz)
    return {"baseline": None,
            "lowpass": butter(parameters.order, parameters.lowpass_hz, fs=sample_hz,
                              btype="lowpass", output="sos"),
            "bandpass": butter(parameters.order, (parameters.band_low_hz, parameters.band_high_hz),
                               fs=sample_hz, btype="bandpass", output="sos")}


def filter_iq(z: np.ndarray, sample_hz: float,
              parameters: FilterParameters = FilterParameters()) -> dict[str, np.ndarray]:
    """Filter scaled complex I/Q once, causally, with first-sample steady-state zi.

    Identical real SOS coefficients operate on I and Q independently. No
    forward/backward filtering, window resets, magnitude/projection filtering,
    or delay compensation is performed. The baseline is an unchanged copy.
    """
    values = np.asarray(z, dtype=np.complex128)
    if values.ndim != 1 or not len(values) or not np.isfinite(values).all():
        raise ValueError("I/Q must be a nonempty finite one-dimensional array")
    result = {}
    for name, sos in design_filters(sample_hz, parameters).items():
        result[name] = (values.copy() if sos is None else
                        sosfilt(sos, values, zi=sosfilt_zi(sos) * values[0])[0])
        if not np.isfinite(result[name]).all():
            raise ValueError("Filter output is not numerically finite")
    return result


def make_synthetic(sample_hz: float = 20, seed: int = 7) -> SyntheticCase:
    """Seeded 60 s case: 0.3 Hz motion stops at 40 s; drift/noise continue.

    Clean = unit-amplitude motion along a fixed complex direction. Nuisance =
    0.5-amplitude 0.03 Hz drift along another direction plus independent I/Q
    Gaussian noise with sigma 0.6 each. Ground truth excludes that nuisance.
    """
    if not math.isfinite(sample_hz) or not 0 < sample_hz <= 1000:
        raise ValueError("Synthetic sample_hz must be finite in (0, 1000]")
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("Synthetic seed must be a nonnegative integer")
    times = np.rint(np.arange(round(60 * sample_hz)) * 1000 / sample_hz).astype(np.int64)
    validate_cadence(times)
    seconds = times / 1000
    clean = np.exp(.6j) * np.sin(2 * np.pi * .3 * seconds) * (seconds < 40)
    drift = .5 * np.exp(1.2j) * np.sin(2 * np.pi * .03 * seconds)
    rng = np.random.default_rng(seed)
    noise = .6 * (rng.standard_normal(len(times)) + 1j * rng.standard_normal(len(times)))
    return SyntheticCase(times, clean, clean + drift + noise, seed, sample_hz)


def prepare_capture(capture: Capture, bin_index: int = 9):
    """Reject discontinuities before filtering; retain existing config semantics."""
    data = prepare_visualization(capture, bin_index)
    if data.quality.parser_issue_count or data.quality.sequence_gap_events:
        raise ValueError("Parser issues or sequence gaps are unsupported; never interpolate or bridge them")
    times = [frame.data.t_ms for frame in capture.frames]
    cadence = validate_cadence(times)
    return data, times, cadence
