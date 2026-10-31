from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from matplotlib.image import imread

from ml.analysis.denoise import FilterParameters
from ml.analysis.labeled_impact import (classification_metrics, discover_sessions, evaluate_labeled,
                                        label_window, paired_comparison, prepare_labeled_session,
                                        render_report, summarize_examples, validate_truth)
from ml.analysis.protocol import read_capture
from ml.synth import write_csv
from ml.train import examples as training_examples


def fixture(root: Path, *, split="val", name="session", parent=True, hold=False):
    """Small mathematical test input, not a generated AIR-400 research dataset."""
    path = root / split / ("cosleep" if parent else "alone") / f"{name}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    t_ms = [900000 + k*60 for k in range(216)]
    seconds = np.arange(216)*.06
    z = (20+10j) + np.sin(2*np.pi*.3*seconds)[:, None] * np.exp(.6j) * (np.arange(24)[None, :]+1)*4
    write_csv(path, list(range(216)), t_ms, z)
    since = [.2] * 216
    since[199], since[200], since[207], since[208], since[215] = 4.999, 8, 5, .2, 8
    truth = {"t_ms": t_ms, "since_breath_s": since, "baby_cm": 37.5,
             "parent_cm": 60.0 if parent else None,
             "base": "recordings/fixture/rec_hold_unit.csv" if hold else "recordings/fixture/rec_breathing_unit.csv",
             "kind": "cosleep" if parent else "alone", "subject": "fixture-only",
             "origin": "unit-test fixture; not AIR-400 or a real recording"}
    path.with_suffix(".json").write_text(json.dumps(truth), encoding="utf-8")
    return path, truth


class LabeledImpactTests(unittest.TestCase):
    def test_label_boundaries_and_unlabeled(self):
        with tempfile.TemporaryDirectory() as directory:
            path, truth = fixture(Path(directory))
            truth["since_breath_s"][:6] = [4.999, 5.0, 7.999, 8.0, None, float("nan")]
            parsed = validate_truth(read_capture(path), truth)
            for k, expected in enumerate(("baby_breathing", None, None, "baby_stopped", None, None)):
                labels, exclusions = label_window(parsed, k, np.random.default_rng(5))
                baby = [e for e in labels if e["bin_index"] == parsed.baby_slot]
                self.assertEqual([e["group"] for e in baby], [] if expected is None else [expected])
                self.assertEqual(len(labels) + sum(exclusions.values()), 24)
                self.assertEqual(sum(e["group"] == "far" for e in labels), 2)

    def test_exclusive_window_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = fixture(Path(directory), parent=False)
            session = prepare_labeled_session(path, "alone/session.csv", np.random.default_rng(5))
            self.assertEqual([w["label_frame_index"] for w in session.windows], [199, 207, 215])
            self.assertEqual([w["evaluation_t_ms"] for w in session.windows], [911940, 912420, 912900])
            babies = [e for e in session.examples if e["group"].startswith("baby_")]
            self.assertEqual([(e["label_frame_index"], e["label"]) for e in babies], [(199, 1), (215, 0)])
            self.assertEqual(session.exclusions["baby_transition_5_to_8_s"], 1)

    def test_training_label_and_baseline_feature_equivalence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = fixture(root)
            session = prepare_labeled_session(path, "cosleep/session.csv", np.random.default_rng(5))
            x, y, what = training_examples(root / "val", hop=8, rng=np.random.default_rng(5))
            selected = np.stack([session.prepared.feature_sets["baseline"][e["window_index"], e["bin_index"]]
                                 for e in session.examples])
            np.testing.assert_array_equal(selected, x)
            np.testing.assert_array_equal([e["label"] for e in session.examples], y)
            np.testing.assert_array_equal([0 if e["group"].startswith("baby_") else 1 if e["group"] == "parent" else 2
                                           for e in session.examples], what)

    def test_parent_hold_far_exclusions_and_overlap(self):
        with tempfile.TemporaryDirectory() as directory:
            path, truth = fixture(Path(directory), hold=True)
            parsed = validate_truth(read_capture(path), truth)
            labels, excluded = label_window(parsed, 199, np.random.default_rng(5))
            self.assertFalse(any(e["bin_index"] == 12 for e in labels))
            self.assertEqual(excluded["parent_hold_unknown"], 1)
            self.assertEqual(excluded["far_not_sampled"], 10)  # 12 eligible, sample 2
            for e in labels:
                if e["group"] == "far":
                    self.assertGreaterEqual(abs(e["bin_index"]-6), 3)
                    self.assertGreaterEqual(abs(e["bin_index"]-12), 4)
            labels, excluded = label_window(replace(parsed, parent_slot=6), 199, np.random.default_rng(5))
            self.assertEqual(excluded["overlapping_centers"], 1)
            self.assertTrue(all(e["group"] == "far" for e in labels))
            self.assertEqual(len(labels)+sum(excluded.values()), 24)

    def test_confusion_arithmetic_and_threshold(self):
        result = classification_metrics([1, 1, 0, 0], [.9, .1, .8, .2])
        self.assertEqual([result[key] for key in ("tp", "tn", "fp", "fn")], [1, 1, 1, 1])
        self.assertEqual(result["confusion_matrix"], [[1, 1], [1, 1]])
        for key in ("precision", "recall", "f1", "accuracy"):
            self.assertEqual(result[key], .5)
        boundary = classification_metrics([1, 0], [.5, .5])
        self.assertEqual((boundary["tp"], boundary["fp"]), (1, 1))
        for y, p in (([2], [.5]), ([0], [float("nan")]), ([0], [1.1]), ([0], [])):
            with self.assertRaises(ValueError):
                classification_metrics(y, p)

    def test_undefined_ratios(self):
        empty = classification_metrics([], [])
        self.assertEqual(empty["confusion_matrix"], [[0, 0], [0, 0]])
        for name in ("precision", "recall", "f1", "accuracy"):
            self.assertIsNone(empty[name])
        negative = classification_metrics([0, 0], [.1, .2])
        self.assertEqual(negative["accuracy"], 1)
        self.assertIsNone(negative["precision"])
        self.assertIsNone(negative["recall"])
        self.assertIsNone(negative["f1"])
        missed = classification_metrics([1], [.1])
        self.assertIsNone(missed["precision"])
        self.assertEqual(missed["recall"], 0)
        self.assertEqual(missed["f1"], 0)

    def test_paired_fixed_and_introduced_error_ids(self):
        examples = [{"id": name, "label": y, "group": group,
                     "probabilities": {"baseline": b, "lowpass": p, "bandpass": b}}
                    for name, y, group, b, p in
                    (("fixed", 1, "baby_breathing", .1, .9), ("introduced", 0, "far", .1, .9),
                     ("correct", 1, "parent", .9, .8), ("wrong", 0, "far", .9, .8))]
        paired = paired_comparison(examples, "lowpass")
        self.assertEqual(paired["fixed_error_ids"], ["fixed"])
        self.assertEqual(paired["introduced_error_ids"], ["introduced"])
        self.assertEqual((paired["both_correct"], paired["both_wrong"], paired["net_error_reduction"]), (1, 1, 0))
        summary = summarize_examples(examples)
        self.assertEqual(summary["conditions"]["baseline"]["overall"]["accuracy"], .5)
        self.assertEqual(summary["conditions"]["lowpass"]["overall"]["accuracy"], .5)
        self.assertIsNone(summary["conditions"]["baseline"]["baby_stopped"]["accuracy"])

    def test_paired_sampling_session_and_aggregate_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root, parent=False, name="a")
            fixture(root, parent=True, hold=True, name="b")
            report = evaluate_labeled(root, max_windows=2)
            self.assertEqual(report["dataset"]["evaluated_sessions"], 2)
            self.assertEqual(report["coverage"], {"candidate_windows": 6, "evaluated_windows": 4,
                                                  "labeled_examples_per_condition": 12})
            identifiers = []
            for session in report["sessions"]:
                self.assertEqual(session["coverage"]["filtered_bins"], 24)
                self.assertFalse(session["coverage"]["all_candidate_windows_evaluated"])
                for example in session["examples"]:
                    identifiers.append(example["id"])
                    self.assertEqual(set(example["probabilities"]), {"baseline", "lowpass", "bandpass"})
                    self.assertEqual(example["label_t_ms"], session["windows"][example["window_index"]]["evaluation_t_ms"])
                self.assertEqual(len(session["examples"])+sum(session["exclusions"].values()), 2*24)
            self.assertEqual(len(identifiers), len(set(identifiers)))
            for condition in ("baseline", "lowpass", "bandpass"):
                overall = report["aggregate"]["conditions"][condition]["overall"]
                self.assertEqual(overall["sample_count"], 12)
                for key in ("tp", "tn", "fp", "fn", "sample_count"):
                    self.assertEqual(overall[key], sum(s["conditions"][condition]["overall"][key] for s in report["sessions"]))
                self.assertIsNone(report["aggregate"]["conditions"][condition]["parent"]["accuracy"])
            prefix = evaluate_labeled(root, max_windows=1, max_sessions=1)
            self.assertEqual(prefix["dataset"]["discovered_sessions"], 2)
            self.assertFalse(prefix["dataset"]["all_sessions_evaluated"])

    def test_truth_schema_and_exact_time_alignment(self):
        with tempfile.TemporaryDirectory() as directory:
            path, truth = fixture(Path(directory))
            capture = read_capture(path)
            cases = [dict(truth, since_breath_s=[0]), dict(truth, t_ms=truth["t_ms"][:-1]),
                     dict(truth, base=""), dict(truth, baby_cm=-100), dict(truth, parent_cm=float("inf"))]
            shifted = json.loads(json.dumps(truth))
            shifted["t_ms"][199] += 1
            cases.append(shifted)
            for bad in (-1, float("inf"), "unknown", True):
                changed = json.loads(json.dumps(truth))
                changed["since_breath_s"][199] = bad
                cases.append(changed)
            for value in cases:
                with self.subTest(value=str(value)[:50]), self.assertRaises(ValueError):
                    validate_truth(capture, value)
            with self.assertRaises(ValueError):
                validate_truth(capture, {})

    def test_optional_data_and_no_split_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "never downloads AIR-400"):
                evaluate_labeled(root)
            path, _ = fixture(root, split="test")
            with self.assertRaisesRegex(ValueError, "val"):
                evaluate_labeled(root)
            path.with_suffix(".json").unlink()
            with self.assertRaisesRegex(ValueError, "Missing matching"):
                discover_sessions(root, "test")
            with self.assertRaisesRegex(ValueError, "Only val"):
                discover_sessions(root, "train")

    def test_explicit_test_split_locks_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root, split="test")
            report = evaluate_labeled(root, "test")
            self.assertIn("fixed defaults", report["dataset"]["test_policy"])
            for options in ({"hop_samples": 16}, {"max_windows": 1}, {"seed": 6}, {"max_sessions": 1},
                            {"parameters": FilterParameters(lowpass_hz=2)}, {"parameters": FilterParameters(order=3)}):
                with self.subTest(options=options), self.assertRaisesRegex(ValueError, "fixed default protocol"):
                    evaluate_labeled(root, "test", **options)

    def test_deterministic_cli_json_and_missing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            command = [sys.executable, "-m", "ml.analysis.labeled_impact", "--data-root", str(root), "--json"]
            outputs = [subprocess.run(command, capture_output=True, text=True) for _ in range(2)]
            self.assertTrue(all(p.returncode == 0 and not p.stderr for p in outputs), [p.stderr for p in outputs])
            self.assertEqual(outputs[0].stdout, outputs[1].stdout)
            report = json.loads(outputs[0].stdout)
            self.assertEqual(report["dataset"]["origin"].split(";")[0], "existing synthetic-session CSV + matching JSON truth")
            self.assertEqual(report["sessions"][0]["source"]["origin"], "unit-test fixture; not AIR-400 or a real recording")
            output = root / "failed.png"
            missing = subprocess.run(command + ["--split", "test", "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(missing.stdout, "")
            self.assertIn("Supply existing", missing.stderr)
            self.assertFalse(output.exists())
            invalid = subprocess.run(command + ["--max-windows", "0", "--output", str(output)], capture_output=True, text=True)
            self.assertEqual(invalid.returncode, 2)
            self.assertFalse(output.exists())

    def test_headless_confusion_png_and_force(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            report = evaluate_labeled(root, max_windows=1)
            path = root / "confusion.png"
            render_report(report, path)
            original = path.read_bytes()
            self.assertTrue(original.startswith(b"\x89PNG"))
            self.assertGreater(float(imread(path).std()), .05)
            with self.assertRaises(FileExistsError):
                render_report(report, path)
            self.assertEqual(path.read_bytes(), original)
            render_report(report, path, force=True)
            command = [sys.executable, "-m", "ml.analysis.labeled_impact", "--data-root", str(root),
                       "--json", "--max-windows", "1", "--output", str(path)]
            protected = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(protected.returncode, 2)
            forced = subprocess.run(command + ["--force"], capture_output=True, text=True)
            self.assertEqual(forced.returncode, 0, forced.stderr)
            json.loads(forced.stdout)
            invalid = subprocess.run(command + ["--force", "--hop", "0"], capture_output=True, text=True)
            self.assertEqual(invalid.returncode, 2)
            self.assertEqual(path.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
