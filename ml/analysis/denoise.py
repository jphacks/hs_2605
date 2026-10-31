"""Offline causal I/Q filter experiments, never a live filter recommendation."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
from scipy.signal import butter, periodogram, sosfilt, sosfilt_zi, sosfreqz

from .protocol import Capture
from .signal import analyze_signal, project_iq
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
    raw = list(t_ms)
    if any(isinstance(t, bool) or not isinstance(t, (int, np.integer)) or t < 0 for t in raw):
        raise ValueError("Board timestamps must be nonnegative integer milliseconds")
    times = [int(t) for t in raw]
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


@dataclass(frozen=True)
class DenoisingComparison:
    elapsed_s: np.ndarray
    signals: dict[str, np.ndarray]
    clean: np.ndarray | None
    report: dict

    def to_dict(self) -> dict:
        from copy import deepcopy
        return deepcopy(self.report)


def _rms(values: np.ndarray) -> float:
    with np.errstate(over="ignore", invalid="ignore"):
        result = float(np.sqrt(np.mean(np.abs(values) ** 2)))
    if not math.isfinite(result):
        raise ValueError("Metric overflow: signal values must be numerically representable")
    return result


def power_spectrum(z: np.ndarray, sample_hz: float):
    """One-sided summed I/Q PSD, centered, Hann; never a projected-motion PSD."""
    centered = z - z.mean() if not np.all(z == z[0]) else np.zeros_like(z)
    frequencies, i_power = periodogram(centered.real, fs=sample_hz, window="hann", detrend=False)
    _, q_power = periodogram(centered.imag, fs=sample_hz, window="hann", detrend=False)
    power = i_power + q_power
    power[0] = 0
    if not np.isfinite(power).all():
        raise ValueError("Spectrum overflow")
    return frequencies, power


def _truth_metrics(output, response, case, seconds, sos, fs):
    active = (seconds >= case.warmup_sec) & (seconds < case.cessation_sec)
    after = seconds >= case.cessation_sec
    first_five = after & (seconds < case.cessation_sec + 5)
    reference = case.clean[active]
    error = _rms(output[active] - reference)
    reference_rms = _rms(reference)
    snr = 20 * math.log10(reference_rms / error) if reference_rms > 0 and error > 0 else None
    phase = 2 * np.pi * case.motion_hz * seconds[active]
    basis = np.column_stack((np.sin(phase), np.cos(phase), np.ones(len(phase))))
    original = np.linalg.lstsq(basis, reference, rcond=None)[0]
    filtered = np.linalg.lstsq(basis, response[active], rcond=None)[0]
    transfer = (filtered[0] + 1j * filtered[1]) / (original[0] + 1j * original[1])
    gain = float(abs(transfer))
    attenuation = -20 * math.log10(gain) if gain > 0 else None
    phase_delay = -float(np.angle(transfer)) / (2 * np.pi * case.motion_hz)
    group_delay = 0.0
    if sos is not None:
        delta = case.motion_hz * .001
        _, h = sosfreqz(sos, worN=[case.motion_hz - delta, case.motion_hz + delta], fs=fs)
        group_delay = -float(np.diff(np.unwrap(np.angle(h)))[0]) / (4 * np.pi * delta)
    post = np.abs(response[after])
    above = np.flatnonzero(post > .01)  # clean unit-amplitude motion, absolute 1% threshold
    settling = (0.0 if not len(above) else
                float(seconds[after][above[-1] + 1] - case.cessation_sec)
                if above[-1] + 1 < len(post) else None)
    return {"snr_db": snr, "reconstruction_rmse": error,
            "signal_attenuation_db": attenuation, "phase_delay_sec": phase_delay,
            "group_delay_sec": group_delay,
            "post_cessation_residual_rms": _rms(response[first_five]),
            "post_cessation_peak_abs": float(np.max(np.abs(response[first_five]))),
            "post_cessation_observed_rms": _rms(output[first_five]),
            "settling_time_sec": settling,
            "startup_rmse": _rms(output[seconds < case.warmup_sec] - case.clean[seconds < case.warmup_sec])}


def _compare(times, observed, cadence, parameters, source, case=None):
    fs = cadence["sample_hz"]
    outputs = filter_iq(observed, fs, parameters)
    seconds = np.array([(int(t) - int(times[0])) / 1000 for t in times])
    responses = filter_iq(case.clean, fs, parameters) if case is not None else None
    designs = design_filters(fs, parameters)
    metrics = {}
    truth_keys = ("snr_db", "snr_improvement_db", "reconstruction_rmse", "signal_attenuation_db",
                  "phase_delay_sec", "group_delay_sec", "post_cessation_residual_rms",
                  "post_cessation_peak_abs", "post_cessation_observed_rms", "settling_time_sec", "startup_rmse")
    for name, signal in outputs.items():
        f, power = power_spectrum(signal, fs)
        total = float(power.sum())
        metrics[name] = {
            "variation_rms": _rms(signal - signal.mean()) if not np.all(signal == signal[0]) else 0.0,
            "projected_rms": _rms(project_iq(signal)),
            "change_rms_vs_baseline": _rms(signal - observed),
            "mean_i": float(signal.real.mean()), "mean_q": float(signal.imag.mean()),
            "power_above_lowpass_fraction": float(power[f > parameters.lowpass_hz].sum() / total) if total > 0 else None,
            **dict.fromkeys(truth_keys)}
        if case is not None:
            metrics[name].update(_truth_metrics(signal, responses[name], case, seconds, designs[name], fs))
    if case is not None:
        baseline_snr = metrics["baseline"]["snr_db"]
        for row in metrics.values():
            row["snr_improvement_db"] = (row["snr_db"] - baseline_snr
                                          if row["snr_db"] is not None and baseline_snr is not None else None)
    report = {"source": source, "observed_samples": len(seconds),
              "start_t_ms": int(times[0]), "end_t_ms": int(times[-1]),
              "cadence": cadence, "parameters": asdict(parameters),
              "initial_state": "first-sample steady-state, once per complete recording",
              "signal_domain": "scaled complex I/Q; identical causal SOS on real and imaginary components",
              "metrics": metrics,
              "evaluation": ({"active_start_sec": case.warmup_sec, "active_end_sec": case.cessation_sec,
                              "residual_interval_sec": [case.cessation_sec, case.cessation_sec + 5],
                              "settling_threshold_abs": .01, "error_delay_compensation": False}
                             if case is not None else None)}
    return DenoisingComparison(seconds, outputs, case.clean if case is not None else None, report)


def benchmark_synthetic(sample_hz: float = 20, seed: int = 7,
                        parameters: FilterParameters = FilterParameters()) -> DenoisingComparison:
    case = make_synthetic(sample_hz, seed)
    source = {"kind": "synthetic", "seed": seed, "requested_sample_hz": sample_hz,
              "motion_hz": case.motion_hz, "cessation_sec": case.cessation_sec,
              "duration_sec": 60, "drift_hz": .03, "drift_amplitude": .5,
              "noise_sigma_per_component": .6, "motion_amplitude": 1.0}
    return _compare(case.t_ms, case.observed, validate_cadence(case.t_ms), parameters, source, case)


def benchmark_capture(capture: Capture, bin_index: int = 9,
                      parameters: FilterParameters = FilterParameters()) -> DenoisingComparison:
    data, times, cadence = prepare_capture(capture, bin_index)
    source = {"kind": "recording", "bin_index": bin_index,
              "distance_cm": float(data.distances_cm[bin_index]),
              "quality": data.quality.to_dict(),
              "baseline_temporal": analyze_signal(capture, bin_index).to_dict()}
    return _compare(times, data.z[:, bin_index], cadence, parameters, source)


def build_figure(comparison: DenoisingComparison):
    """Return an Agg figure: I/Q side by side, cessation zoom if known, PSD, metrics."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    report = comparison.report
    synthetic = comparison.clean is not None
    figure = Figure(figsize=(14, 12 if synthetic else 10), layout="constrained")
    FigureCanvasAgg(figure)
    grid = figure.add_gridspec(4 if synthetic else 3, 3, height_ratios=[2.5, 1.8, 2, 2.6] if synthetic else [2.5, 2, 2.6])
    fs = report["cadence"]["sample_hz"]
    colors = {"baseline": "0.35", "lowpass": "tab:blue", "bandpass": "tab:orange"}
    top = []
    all_values = np.concatenate(list(comparison.signals.values()))
    lower = float(min(all_values.real.min(), all_values.imag.min()))
    upper = float(max(all_values.real.max(), all_values.imag.max()))
    margin = max((upper-lower)*.05, .01)
    for k, (name, signal) in enumerate(comparison.signals.items()):
        axis = figure.add_subplot(grid[0, k])
        top.append(axis)
        axis.plot(comparison.elapsed_s, signal.real, label="I", lw=.8)
        axis.plot(comparison.elapsed_s, signal.imag, label="Q", lw=.8)
        if synthetic:
            axis.plot(comparison.elapsed_s, comparison.clean.real, "k--", lw=.8, label="Clean I")
        axis.set(title=name, xlabel="Elapsed seconds (board-relative)", ylabel="Scaled I/Q units",
                 ylim=(lower-margin, upper+margin))
        axis.legend(fontsize=8)
        axis.grid(alpha=.2)
        if synthetic:
            zoom = figure.add_subplot(grid[1, k])
            stop = report["source"]["cessation_sec"]
            mask = (comparison.elapsed_s >= stop-3) & (comparison.elapsed_s <= stop+8)
            zoom.plot(comparison.elapsed_s[mask], signal.real[mask], lw=.8, label="Observed I")
            zoom.plot(comparison.elapsed_s[mask], comparison.clean.real[mask], "k--", lw=.8, label="Clean I")
            zoom.axvline(stop, color="tab:red", linestyle=":", label="Known cessation")
            zoom.set(title="Cessation detail (I component)", xlabel="Elapsed seconds", ylabel="I units")
            zoom.legend(fontsize=7)
    spectrum = figure.add_subplot(grid[2 if synthetic else 1, :])
    for name, signal in comparison.signals.items():
        f, power = power_spectrum(signal, fs)
        valid = (f > 0) & (power > 0)
        spectrum.semilogy(f[valid], power[valid], color=colors[name], label=name)
    spectrum.set(xlabel="Frequency (Hz)", ylabel="Summed I/Q PSD (units²/Hz)",
                 title="Centered I/Q Hann spectra - no motion classification")
    spectrum.legend()
    spectrum.grid(alpha=.2)
    summary = figure.add_subplot(grid[-1, :])
    summary.axis("off")
    keys = (["snr_db", "snr_improvement_db", "reconstruction_rmse", "signal_attenuation_db",
             "phase_delay_sec", "group_delay_sec", "post_cessation_residual_rms", "settling_time_sec"]
            if synthetic else ["variation_rms", "projected_rms", "change_rms_vs_baseline",
                               "mean_i", "mean_q", "power_above_lowpass_fraction"])
    table_rows = [[key.replace("_", " ")] + ["n/a" if report["metrics"][mode][key] is None
                  else f"{report['metrics'][mode][key]:.5g}" for mode in comparison.signals] for key in keys]
    table = summary.table(cellText=table_rows, colLabels=["Metric", *comparison.signals],
                          cellLoc="left", colWidths=[.4, .2, .2, .2], bbox=[0, .2, 1, .78])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    note = ("Synthetic truth: active 10-40 s; no delay compensation. Residual RMS: clean-only filter response, 40-45 s."
            if synthetic else "Descriptive real-data metrics only; SNR, distortion, delay and cessation truth are unknown.")
    summary.text(0, .14, note, transform=summary.transAxes, fontsize=9)
    summary.text(0, .02, "Causal state is continuous; band-pass removes DC. No filter winner, medical claim, or runtime change.",
                 transform=summary.transAxes, fontsize=9)
    p = report["parameters"]
    source_label = (f"seed {report['source']['seed']}" if synthetic else
                    f"bin {report['source']['bin_index']} at {report['source']['distance_cm']:g} cm")
    figure.suptitle(f"Offline denoising experiment ({source_label}) - {report['observed_samples']} samples, {fs:.5g} Hz\n"
                   f"Butterworth order {p['order']}: LP {p['lowpass_hz']:g} Hz; BP {p['band_low_hz']:g}-{p['band_high_hz']:g} Hz",
                   fontsize=13)
    return figure


def render_report(comparison: DenoisingComparison, output, *, force: bool = False):
    from io import BytesIO
    from pathlib import Path

    path = Path(output)
    if path.suffix.lower() != ".png":
        raise ValueError("Report output must have a .png extension")
    if path.exists() and not force:
        raise FileExistsError(f"Output already exists: {path}; use --force")
    figure = build_figure(comparison)
    try:
        with BytesIO() as buffer:
            figure.savefig(buffer, format="png", dpi=130, metadata={"Software": "IBUKI offline analysis"})
            with path.open("wb" if force else "xb") as stream:
                stream.write(buffer.getvalue())
    finally:
        figure.clear()
    return path


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    from .protocol import read_capture

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?")
    parser.add_argument("--synthetic", action="store_true")
    parser.add_argument("--bin", type=int, dest="bin_index")
    parser.add_argument("--sample-hz", type=float)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--order", type=int, default=2)
    parser.add_argument("--lowpass-hz", type=float, default=1.5)
    parser.add_argument("--band-low-hz", type=float, default=.1)
    parser.add_argument("--band-high-hz", type=float, default=1.5)
    parser.add_argument("--output", help="optional PNG output; no files written otherwise")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if bool(args.path) == args.synthetic:
        parser.error("Supply exactly one recording path or --synthetic")
    if args.synthetic and args.bin_index is not None:
        parser.error("--bin applies only to recordings")
    if not args.synthetic and (args.sample_hz is not None or args.seed is not None):
        parser.error("--sample-hz and --seed apply only to synthetic experiments")
    try:
        parameters = FilterParameters(args.order, args.lowpass_hz, args.band_low_hz, args.band_high_hz)
        result = (benchmark_synthetic(20 if args.sample_hz is None else args.sample_hz,
                                      7 if args.seed is None else args.seed, parameters) if args.synthetic else
                  benchmark_capture(read_capture(args.path), 9 if args.bin_index is None else args.bin_index, parameters))
        path = render_report(result, args.output, force=args.force) if args.output else None
        encoded = json.dumps(result.to_dict(), indent=2, sort_keys=True, allow_nan=False)
    except (ValueError, OSError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(encoded if args.json else "Offline exploratory comparison (no automatic winner):\n" +
          "\n".join(f"{name}: {metrics}" for name, metrics in result.report["metrics"].items()))
    if path is not None:
        print(f"Saved denoising comparison: {path}", file=sys.stderr if args.json else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
