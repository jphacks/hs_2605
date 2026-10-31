"""Paired, labeled per-slot CNN evaluation; not apnea/state/alarm validation."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path

import numpy as np

from ml.features import NUM_BINS, WINDOW
from ml.train import (BREATHING_WITHIN_S, FAR_PER_WINDOW, FAR_SLOTS, PARENT_BODY_SLOTS,
                      STOPPED_FOR_S, slot_of)

from .denoise import FilterParameters
from .model_impact import FeatureComparison, load_numpy_model, prepare_features, select_window_ends
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
        # prepare_features ends are exclusive; ml.features.windows yields end-1.
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


def classification_metrics(labels, probabilities) -> dict:
    """Binary p>=0.5 counts; matrix rows=true [0,1], columns=predicted [0,1]."""
    y, p = np.asarray(labels), np.asarray(probabilities, dtype=float)
    if y.ndim != 1 or p.shape != y.shape or not np.isin(y, [0, 1]).all() or not np.isfinite(p).all() or np.any((p < 0) | (p > 1)):
        raise ValueError("Labels/probabilities must be aligned binary/finite one-dimensional arrays")
    actual, predicted = y == 1, p >= .5
    tp = int(np.count_nonzero(actual & predicted))
    tn = int(np.count_nonzero(~actual & ~predicted))
    fp = int(np.count_nonzero(~actual & predicted))
    fn = int(np.count_nonzero(actual & ~predicted))
    def ratio(a, b):
        return a / b if b else None
    return {"sample_count": len(y), "positive_count": int(actual.sum()),
            "negative_count": int((~actual).sum()), "tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "confusion_matrix": [[tn, fp], [fn, tp]], "matrix_labels": [0, 1],
            "precision": ratio(tp, tp+fp), "recall": ratio(tp, tp+fn),
            "f1": ratio(2*tp, 2*tp+fp+fn), "accuracy": ratio(tp+tn, len(y))}


def paired_comparison(examples: list[dict], condition: str) -> dict:
    """Identify exactly which shared labeled examples change correctness."""
    fixed, introduced = [], []
    both_correct = both_wrong = 0
    for example in examples:
        label = example["label"] == 1
        baseline_correct = (example["probabilities"]["baseline"] >= .5) == label
        current_correct = (example["probabilities"][condition] >= .5) == label
        if baseline_correct and current_correct:
            both_correct += 1
        elif not baseline_correct and not current_correct:
            both_wrong += 1
        elif current_correct:
            fixed.append(example["id"])
        else:
            introduced.append(example["id"])
    return {"sample_count": len(examples), "both_correct": both_correct, "both_wrong": both_wrong,
            "baseline_errors_fixed": len(fixed), "previously_correct_became_errors": len(introduced),
            "net_error_reduction": len(fixed)-len(introduced),
            "fixed_error_ids": fixed, "introduced_error_ids": introduced}


def summarize_examples(examples: list[dict]) -> dict:
    conditions, paired = {}, {}
    subsets = {"overall": examples, **{group: [e for e in examples if e["group"] == group] for group in GROUPS}}
    for condition in ("baseline", "lowpass", "bandpass"):
        conditions[condition] = {name: classification_metrics([e["label"] for e in subset],
                                                               [e["probabilities"][condition] for e in subset])
                                 for name, subset in subsets.items()}
        if condition != "baseline":
            paired[condition] = {name: paired_comparison(subset, condition) for name, subset in subsets.items()}
    return {"conditions": conditions, "paired_comparisons": paired}


def evaluate_labeled(data_root: str | Path = DATA, split: str = "val", *,
                     hop_samples: int = 8, max_windows: int = 12, seed: int = 5,
                     max_sessions: int | None = None,
                     parameters: FilterParameters = FilterParameters()) -> dict:
    """Micro-aggregate identical labeled examples; test uses a frozen default protocol.

    Validation can explore settings. Explicit test selection locks filters, hop,
    window cap, far seed and all-session coverage to defaults fixed in this code.
    It does not establish historical split independence or prevent external tuning.
    """
    select_window_ends(WINDOW, hop_samples, max_windows)  # validate selection options before I/O
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError("Far sampling seed must be a nonnegative integer")
    if max_sessions is not None and (isinstance(max_sessions, bool) or not isinstance(max_sessions, int) or max_sessions <= 0):
        raise ValueError("max_sessions must be a positive integer when supplied")
    if split == "test" and (parameters != FilterParameters() or hop_samples != 8 or max_windows != 12
                             or seed != 5 or max_sessions is not None):
        raise ValueError("Test evaluation requires the fixed default protocol (order 2, LP 1.5, BP 0.1-1.5, "
                         "hop 8, max-windows 12, seed 5, all sessions). Explore settings on val instead.")
    paths = discover_sessions(data_root, split)
    selected = paths if max_sessions is None else paths[:max_sessions]
    model, identity = load_numpy_model()
    rng = np.random.default_rng(seed)
    sessions, all_examples, excluded = [], [], Counter()
    folder = Path(data_root) / split
    for csv in selected:
        session_id = csv.relative_to(folder).as_posix()
        try:
            session = prepare_labeled_session(csv, session_id, rng, hop_samples=hop_samples,
                                              max_windows=max_windows, parameters=parameters)
            examples = [dict(e, probabilities={}) for e in session.examples]
            for condition, features in session.prepared.feature_sets.items():
                rows = np.stack([features[e["window_index"], e["bin_index"]] for e in examples]) if examples else None
                probabilities = model.run(rows) if rows is not None else np.empty((0, 1))
                if probabilities.shape != (len(examples), 1):
                    raise ValueError("breath_slot must return one probability per labeled example")
                for example, probability in zip(examples, probabilities[:, 0]):
                    example["probabilities"][condition] = float(probability)
            summary = summarize_examples(examples)  # also validates finite probability ranges
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise ValueError(f"Invalid session {session_id}: {exc}") from exc
        prepared = session.prepared
        sessions.append({"source": session.source,
                         "coverage": {"observed_d_frames": prepared.quality.total_d_frames,
                                      "candidate_windows": prepared.candidate_windows,
                                      "evaluated_windows": len(session.windows),
                                      "all_candidate_windows_evaluated": len(session.windows) == prepared.candidate_windows,
                                      "filtered_bins": NUM_BINS, "feature_bins_per_window": NUM_BINS,
                                      "labeled_examples_per_condition": len(examples),
                                      "evaluated_labeled_bin_indices": sorted({e["bin_index"] for e in examples})},
                         "quality": prepared.quality.to_dict(), "cadence": prepared.cadence,
                         "exclusions": session.exclusions, "windows": list(session.windows),
                         "examples": examples, **summary})
        all_examples.extend(examples)
        excluded.update(session.exclusions)
    label_policy = {"source": "ml.train constants and slot_of (no training invoked)",
                    "baby_breathing_lt_s": BREATHING_WITHIN_S, "baby_stopped_ge_s": STOPPED_FOR_S,
                    "baby_far_distance_bins": FAR_SLOTS, "parent_far_distance_bins": PARENT_BODY_SLOTS,
                    "far_per_window": FAR_PER_WINDOW, "parent_hold_marker": "rec_hold",
                    "unknown_baby_age": "null/NaN excluded", "overlapping_centers": "exclude ambiguous shared slot",
                    "truth_alignment": "exact D timestamps; label at end_index_exclusive - 1"}
    protocol = {"parameters": asdict(parameters), "threshold": .5, "positive_decision": "p >= 0.5",
                "window_samples": WINDOW, "hop_samples": hop_samples, "max_windows_per_session": max_windows,
                "far_sampling_seed": seed, "far_sampling": "one Generator across sorted selected sessions/windows; shared by conditions",
                "window_selection": "model_impact evenly spaced candidate ranks; K=1 first",
                "max_sessions": max_sessions, "label_policy": label_policy,
                "model_artifacts_sha256": identity["artifacts_sha256"]}
    return {"dataset": {"root": str(Path(data_root).resolve()), "split": split,
                        "origin": "existing synthetic-session CSV + matching JSON truth; declared source metadata is unverified",
                        "discovered_sessions": len(paths), "evaluated_sessions": len(sessions),
                        "all_sessions_evaluated": len(sessions) == len(paths),
                        "session_selection": "all sorted sessions, or explicit sorted prefix for val",
                        "test_policy": "fixed defaults only; no test-set tuning in this tool" if split == "test" else "exploratory validation",
                        "historical_split_independence_verified": False},
            "model": identity, "protocol": protocol,
            "protocol_sha256": hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest(),
            "coverage": {"candidate_windows": sum(s["coverage"]["candidate_windows"] for s in sessions),
                         "evaluated_windows": sum(s["coverage"]["evaluated_windows"] for s in sessions),
                         "labeled_examples_per_condition": len(all_examples)},
            "exclusions": dict(sorted(excluded.items())), "aggregate": summarize_examples(all_examples),
            "sessions": sessions,
            "limitations": ["Per-slot synthetic-session classification, not apnea-event detection or medical-device validation.",
                            "No 2D state-model performance or false alarms per hour are measured.",
                            "Overlapping windows/bins and shared base recordings are correlated; no significance claim or automatic winner.",
                            "Declared split/source metadata is not a proof of independence from training.",
                            "Same fixed full-precision NumPy model, threshold and paired examples; not live int8/alarm behavior.",
                            "Filters process complete accepted recordings continuously; scores cover only the reported labeled subset."]}


def render_report(report: dict, output, *, force: bool = False):
    """Headless count confusion matrices; no normalized or invented empty scores."""
    from io import BytesIO
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    path = Path(output)
    if path.suffix.lower() != ".png":
        raise ValueError("Confusion-matrix output must have a .png extension")
    if path.exists() and not force:
        raise FileExistsError(f"Output already exists: {path}; use --force")
    figure = Figure(figsize=(13, 5), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(1, 3)
    conditions = report["aggregate"]["conditions"]
    maximum = max(1, max(max(max(row) for row in conditions[c]["overall"]["confusion_matrix"])
                         for c in ("baseline", "lowpass", "bandpass")))
    for ax, name in zip(axes, ("baseline", "lowpass", "bandpass")):
        metrics = conditions[name]["overall"]
        matrix = np.array(metrics["confusion_matrix"])
        heat = ax.imshow(matrix, vmin=0, vmax=maximum, cmap="Blues")
        for i in range(2):
            for j in range(2):
                ax.text(j, i, str(matrix[i, j]), ha="center", va="center",
                        color="white" if matrix[i, j] > maximum/2 else "black", fontsize=18)
        accuracy = "undefined" if metrics["accuracy"] is None else f"{metrics['accuracy']:.4f}"
        ax.set(title=f"{name}: n={metrics['sample_count']}, accuracy={accuracy}",
               xticks=[0, 1], yticks=[0, 1], xticklabels=["0: not breathing", "1: breathing"],
               yticklabels=["0: not breathing", "1: breathing"], xlabel="Predicted slot label", ylabel="Truth slot label")
    figure.colorbar(heat, ax=list(axes), label="Example count (same paired samples)", shrink=.75)
    dataset = report["dataset"]
    figure.suptitle(f"Labeled per-slot CNN evaluation: {dataset['split']}, {dataset['evaluated_sessions']} sessions\n"
                   "Synthetic-session labels; not apnea events, state-model/alarm performance, or medical validation", fontsize=12)
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
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=DATA, help="directory containing val/ and/or test/")
    parser.add_argument("--split", choices=("val", "test"), default="val")
    parser.add_argument("--hop", type=int, default=8)
    parser.add_argument("--max-windows", type=int, default=12)
    parser.add_argument("--max-sessions", type=int)
    parser.add_argument("--seed", type=int, default=5)
    parser.add_argument("--order", type=int, default=2)
    parser.add_argument("--lowpass-hz", type=float, default=1.5)
    parser.add_argument("--band-low-hz", type=float, default=.1)
    parser.add_argument("--band-high-hz", type=float, default=1.5)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", help="optional confusion-matrix PNG; parent directory must exist")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = evaluate_labeled(args.data_root, args.split, hop_samples=args.hop, max_windows=args.max_windows,
                                  seed=args.seed, max_sessions=args.max_sessions,
                                  parameters=FilterParameters(args.order, args.lowpass_hz, args.band_low_hz, args.band_high_hz))
        encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False)
        path = render_report(report, args.output, force=args.force) if args.output else None
    except (ValueError, OSError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    if args.json:
        print(encoded)
    else:
        print("Labeled per-slot classification; not apnea-event, state-model, or alarm evaluation.")
        print(report["dataset"])
        for condition, metrics in report["aggregate"]["conditions"].items():
            print(condition, metrics["overall"])
    if path is not None:
        print(f"Saved labeled confusion matrices: {path}", file=sys.stderr if args.json else sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
