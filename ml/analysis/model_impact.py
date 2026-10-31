"""Offline feature/prediction drift of one fixed CNN; not labeled accuracy."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from ml.features import CHANNELS, NUM_BINS, SCALE, STEP_CM, STEPS, WINDOW, slot_cm, window_features
from ml.runtime import MODELS, Model

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


def _paired_arrays(current, baseline):
    a, b = np.asarray(current, dtype=float), np.asarray(baseline, dtype=float)
    if a.shape != b.shape or not a.size or not np.isfinite(a).all() or not np.isfinite(b).all():
        raise ValueError("Comparison arrays must be finite, nonempty and have matching shapes")
    return a, b


def _absolute_summary(difference: np.ndarray) -> dict:
    return {"value_count": difference.size, "mean_absolute_difference": float(difference.mean()),
            "p95_absolute_difference": float(np.percentile(difference, 95))}


def feature_difference(current, baseline) -> dict:
    """Aggregate absolute drift over the actual model tensor, including neighbors."""
    a, b = _paired_arrays(current, baseline)
    if a.ndim < 2 or a.shape[-1] != CHANNELS:
        raise ValueError(f"Feature tensors must have {CHANNELS} interleaved channels")
    difference = np.abs(a - b)
    return {**_absolute_summary(difference), "families": {
        name: _absolute_summary(difference[..., offset::3])
        for offset, name in enumerate(("wave", "level", "amount"))}}


def probability_difference(current, baseline) -> dict:
    """Changes at p >= 0.5, matching ml/train.py; never correctness/accuracy."""
    a, b = _paired_arrays(current, baseline)
    if np.any((a < 0) | (a > 1) | (b < 0) | (b > 1)):
        raise ValueError("Model probabilities must lie in [0, 1]")
    yes, original_yes = a >= .5, b >= .5
    flips = int(np.count_nonzero(yes != original_yes))
    return {**_absolute_summary(np.abs(a-b)), "mean_probability": float(a.mean()),
            "positive_count": int(yes.sum()), "baseline_positive_count": int(original_yes.sum()),
            "prediction_flips": flips, "prediction_flip_percent": 100 * flips / a.size,
            "flips_up": int(np.count_nonzero(yes & ~original_yes)),
            "flips_down": int(np.count_nonzero(~yes & original_yes))}


def load_numpy_model(folder: str | Path = MODELS):
    """Load the exported 1D model explicitly, never ml.runtime.load or LiteRT."""
    import hashlib
    from zipfile import BadZipFile

    folder = Path(folder)
    artifacts = [folder / f"breath_slot.{suffix}" for suffix in ("json", "npz")]
    missing = [str(path) for path in artifacts if not path.is_file()]
    if missing:
        raise ValueError("Missing breath_slot model artifacts: " + ", ".join(missing))
    identity = {"name": "breath_slot", "runtime": "ml.runtime.Model",
                "representation": "exported full-precision NumPy weights, not int8 LiteRT",
                "artifacts_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                     for path in artifacts}}
    try:
        model = Model("breath_slot", folder=folder)
    except (OSError, ValueError, KeyError, TypeError, EOFError, BadZipFile) as exc:
        raise ValueError(f"Cannot load breath_slot NumPy model: {exc}") from exc
    return model, identity


def analyze_model_impact(capture: Capture, bin_index: int = 9, *, hop_samples: int = 8,
                         max_windows: int = 12,
                         parameters: FilterParameters = FilterParameters(),
                         model_dir: str | Path = MODELS) -> dict:
    """Evaluate one model on aligned all-bin feature tensors, with bounded windows."""
    prepared = prepare_features(capture, bin_index, hop_samples=hop_samples,
                                max_windows=max_windows, parameters=parameters)
    model, identity = load_numpy_model(model_dir)
    probabilities = {}
    count = len(prepared.windows)
    for name, features in prepared.feature_sets.items():
        values = model.run(features.reshape(-1, STEPS, CHANNELS))
        if values.shape != (count * NUM_BINS, 1):
            raise ValueError("breath_slot output must be one probability per window/distance bin")
        probabilities[name] = values.reshape(count, NUM_BINS)
    base_features, base_probabilities = prepared.feature_sets["baseline"], probabilities["baseline"]
    feature_drift, prediction_drift = {}, {}
    for name in probabilities:
        feature_drift[name] = {
            "all_bins": feature_difference(prepared.feature_sets[name], base_features),
            "selected_bin": feature_difference(prepared.feature_sets[name][:, bin_index], base_features[:, bin_index])}
        prediction_drift[name] = {
            "all_bins": probability_difference(probabilities[name], base_probabilities),
            "selected_bin": probability_difference(probabilities[name][:, bin_index], base_probabilities[:, bin_index])}
    records = [{**window, "probabilities": {name: values[k].tolist() for name, values in probabilities.items()},
                "selected_bin_probabilities": {name: float(values[k, bin_index]) for name, values in probabilities.items()}}
               for k, window in enumerate(prepared.windows)]
    return {"model": identity, "parameters": asdict(parameters),
            "selected_bin": {"index": bin_index, "distance_cm": float(slot_cm(bin_index))},
            "recording": {"observed_d_frames": len(capture.frames),
                          "first_t_ms": capture.frames[0].data.t_ms,
                          "last_t_ms": capture.frames[-1].data.t_ms,
                          "quality": prepared.quality.to_dict(), "cadence": prepared.cadence},
            "evaluation": {"window_samples": WINDOW, "feature_shape_per_bin": [STEPS, CHANNELS],
                           "hop_samples": hop_samples, "max_windows": max_windows,
                           "candidate_windows": prepared.candidate_windows, "evaluated_windows": count,
                           "evaluated_bin_indices": list(range(NUM_BINS)), "evaluated_bins": NUM_BINS,
                           "predictions_per_condition": count * NUM_BINS,
                           "all_candidate_windows_evaluated": count == prepared.candidate_windows,
                           "selection_policy": "evenly spaced candidate ranks, integer floor; K=1 selects first",
                           "decision_threshold": .5, "positive_decision": "p >= 0.5",
                           "filter_state": "continuous over all recorded samples, first-sample steady-state",
                           "labeled_ground_truth": False},
            "feature_drift": feature_drift, "prediction_drift": prediction_drift,
            "windows": records,
            "limitations": ["No per-window ground-truth labels: drift and flips are not accuracy improvements.",
                            "All 24 bins are filtered and evaluated; --bin selects display and per-bin summaries only.",
                            "Only selected candidate windows are inferred; no full-coverage claim or automatic filter winner.",
                            "NumPy full-precision export is evaluated, not the quantized live 2D state/alarm pipeline.",
                            "Includes filter startup; no interpolation, delay compensation, or state reset at windows."]}


def render_report(report: dict, output, *, force: bool = False):
    """Optional headless PNG for the selected bin; exclusive creation by default."""
    from io import BytesIO
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    path = Path(output)
    if path.suffix.lower() != ".png":
        raise ValueError("Output must have a .png extension")
    if path.exists() and not force:
        raise FileExistsError(f"Output already exists: {path}; use --force")
    figure = Figure(figsize=(13, 8), layout="constrained")
    FigureCanvasAgg(figure)
    plot, summary = figure.subplots(2, 1, gridspec_kw={"height_ratios": [3, 2]})
    origin = report["recording"]["first_t_ms"]
    seconds = [(window["evaluation_t_ms"] - origin) / 1000 for window in report["windows"]]
    for name in ("baseline", "lowpass", "bandpass"):
        plot.plot(seconds, [window["selected_bin_probabilities"][name] for window in report["windows"]],
                  marker="o", linewidth=.8, label=name)
    plot.axhline(.5, color="0.4", linestyle="--", label="p >= 0.5 threshold")
    selected = report["selected_bin"]
    plot.set(title=f"Fixed breath_slot CNN: bin {selected['index']} at {selected['distance_cm']:g} cm",
             xlabel="Evaluation seconds from first D (board uptime, not Unix time)",
             ylabel="CNN breathing probability (not labeled accuracy)", ylim=(-.03, 1.03))
    plot.legend()
    plot.grid(alpha=.2)
    summary.axis("off")
    rows = []
    for name in ("baseline", "lowpass", "bandpass"):
        selected_stats = report["prediction_drift"][name]["selected_bin"]
        all_stats = report["prediction_drift"][name]["all_bins"]
        feature_stats = report["feature_drift"][name]["all_bins"]
        rows.append([name, f"{selected_stats['mean_probability']:.4f}",
                     f"{selected_stats['mean_absolute_difference']:.4f}",
                     f"{selected_stats['prediction_flips']}", f"{all_stats['mean_absolute_difference']:.4f}",
                     f"{all_stats['prediction_flips']} ({all_stats['prediction_flip_percent']:.2f}%)",
                     f"{feature_stats['mean_absolute_difference']:.4f}"])
    table = summary.table(cellText=rows, colLabels=["Condition", "Bin mean p", "Bin p MAE", "Bin flips",
                                                   "All-bin p MAE", "All-bin flips", "Feature MAE"],
                          cellLoc="center", bbox=[0, .4, 1, .58])
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    evaluation = report["evaluation"]
    summary.text(0, .3, f"Evaluated {evaluation['evaluated_windows']}/{evaluation['candidate_windows']} candidate windows; "
                 f"{NUM_BINS} bins; {evaluation['predictions_per_condition']} predictions per condition; "
                 f"hop {evaluation['hop_samples']} samples.", transform=summary.transAxes)
    p = report["parameters"]
    summary.text(0, .19, f"Butterworth order {p['order']}: LP {p['lowpass_hz']:g} Hz; "
                 f"BP {p['band_low_hz']:g}-{p['band_high_hz']:g} Hz. Continuous state, includes startup.",
                 transform=summary.transAxes)
    summary.text(0, .08, "Points are sampled CNN evaluations; connecting lines are guides. No labels, corrected predictions, or filter winner.",
                 transform=summary.transAxes, fontsize=9)
    figure.suptitle("Offline CNN sensitivity to denoising - same NumPy model weights", fontsize=14)
    try:
        with BytesIO() as buffer:
            figure.savefig(buffer, format="png", dpi=140, metadata={"Software": "IBUKI offline analysis"})
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
    parser.add_argument("path")
    parser.add_argument("--bin", type=int, default=9, dest="bin_index")
    parser.add_argument("--hop", type=int, default=8, help="candidate hop in observed samples")
    parser.add_argument("--max-windows", type=int, default=12)
    parser.add_argument("--model-dir", type=Path, default=MODELS)
    parser.add_argument("--order", type=int, default=2)
    parser.add_argument("--lowpass-hz", type=float, default=1.5)
    parser.add_argument("--band-low-hz", type=float, default=.1)
    parser.add_argument("--band-high-hz", type=float, default=1.5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", help="optional selected-bin PNG")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        parameters = FilterParameters(args.order, args.lowpass_hz, args.band_low_hz, args.band_high_hz)
        report = analyze_model_impact(read_capture(args.path), args.bin_index, hop_samples=args.hop,
                                      max_windows=args.max_windows, parameters=parameters, model_dir=args.model_dir)
        encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
        path = render_report(report, args.output, force=args.force) if args.output else None
    except (ValueError, OSError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    if args.json:
        print(encoded)
    else:
        print("Offline prediction changes, not labeled accuracy; same NumPy breath_slot model.")
        print(report["evaluation"])
        for name, drift in report["prediction_drift"].items():
            print(name, drift)
    if path is not None:
        print(f"Saved model sensitivity report: {path}", file=sys.stderr if args.json else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
