from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from matplotlib.image import imread

from ml.analysis.denoise import filter_iq
from ml.analysis.model_impact import (analyze_model_impact, feature_difference, load_numpy_model,
                                      prepare_features, probability_difference, render_report,
                                      select_window_ends)
from ml.analysis.protocol import Config, parse_capture, read_capture
from ml.features import NUM_BINS, WINDOW, read_csv, window_features
from ml.runtime import MODELS, Model


ROOT = Path(__file__).resolve().parents[3]
BREATHING = ROOT / "recordings/samples/breathing.sample.csv"


class ModelImpactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture = read_capture(BREATHING)

    def test_baseline_feature_equivalence(self):
        prepared = prepare_features(self.capture, max_windows=3)
        times, z = read_csv(BREATHING)
        np.testing.assert_array_equal(prepared.conditions["baseline"], z)
        for k, window in enumerate(prepared.windows):
            start, end = window["start_index"], window["end_index_exclusive"]
            np.testing.assert_array_equal(prepared.feature_sets["baseline"][k], window_features(z[start:end]))
            self.assertEqual(window["evaluation_t_ms"], times[end-1])
            self.assertEqual(window["start_t_ms"], times[start])

    def test_matching_indices_and_continuous_state(self):
        prepared = prepare_features(self.capture, max_windows=3)
        self.assertEqual([w["end_index_exclusive"] for w in prepared.windows], [200, 592, 992])
        self.assertEqual({value.shape for value in prepared.feature_sets.values()}, {(3, 24, 100, 15)})
        for k, window in enumerate(prepared.windows):
            start, end = window["start_index"], window["end_index_exclusive"]
            self.assertEqual(end-start, WINDOW)
            for name in prepared.conditions:
                np.testing.assert_array_equal(prepared.feature_sets[name][k],
                                              window_features(prepared.conditions[name][start:end]))
        # Restarting the filter at a late window is observably different.
        start, end = prepared.windows[-1]["start_index"], prepared.windows[-1]["end_index_exclusive"]
        reset = np.empty((WINDOW, NUM_BINS), complex)
        for k in range(NUM_BINS):
            reset[:, k] = filter_iq(prepared.conditions["baseline"][start:end, k],
                                    prepared.cadence["sample_hz"])["lowpass"]
        self.assertGreater(float(np.abs(window_features(reset)-prepared.feature_sets["lowpass"][-1]).max()), .01)

    def test_all_bins_filtered_consistently(self):
        prepared = prepare_features(self.capture, max_windows=1)
        for k in range(NUM_BINS):
            direct = filter_iq(prepared.conditions["baseline"][:, k], prepared.cadence["sample_hz"])
            for name in direct:
                np.testing.assert_array_equal(prepared.conditions[name][:, k], direct[name])
        self.assertGreater(float(np.abs(prepared.conditions["lowpass"][:, 17]-
                                        prepared.conditions["baseline"][:, 17]).max()), 0)

    def test_committed_numpy_model_and_deterministic_inference(self):
        prepared = prepare_features(self.capture, max_windows=2)
        model, identity = load_numpy_model()
        self.assertIsInstance(model, Model)
        self.assertEqual(identity["runtime"], "ml.runtime.Model")
        for filename, digest in identity["artifacts_sha256"].items():
            self.assertEqual(digest, hashlib.sha256((MODELS / filename).read_bytes()).hexdigest())
        x = prepared.feature_sets["baseline"].reshape(-1, 100, 15)
        np.testing.assert_array_equal(model.run(x), model.run(x))
        report = analyze_model_impact(self.capture, max_windows=2)
        predictions = np.array([w["probabilities"]["baseline"] for w in report["windows"]])
        np.testing.assert_array_equal(predictions, model.run(x).reshape(2, NUM_BINS))
        self.assertTrue(np.all((predictions >= 0) & (predictions <= 1)))

    def test_feature_differences_and_families(self):
        baseline = np.zeros((2, 3, 15))
        current = np.zeros_like(baseline)
        current[..., 0::3], current[..., 1::3], current[..., 2::3] = 1, 2, 3
        summary = feature_difference(current, baseline)
        self.assertEqual(summary["value_count"], 90)
        self.assertEqual(summary["mean_absolute_difference"], 2)
        self.assertEqual(summary["p95_absolute_difference"], 3)
        for name, value in (("wave", 1), ("level", 2), ("amount", 3)):
            self.assertEqual(summary["families"][name]["mean_absolute_difference"], value)
            self.assertEqual(summary["families"][name]["value_count"], 30)
        self.assertEqual(feature_difference(baseline, baseline)["p95_absolute_difference"], 0)
        with self.assertRaises(ValueError):
            feature_difference(np.ones((1, 2)), np.ones((1, 2)))

    def test_probability_differences_and_threshold_flips(self):
        baseline = np.array([.5, .49, .8, .1])
        current = np.array([.49, .5, .7, .2])
        summary = probability_difference(current, baseline)
        self.assertAlmostEqual(summary["mean_absolute_difference"], .055)
        self.assertAlmostEqual(summary["p95_absolute_difference"], .1)
        self.assertEqual(summary["prediction_flips"], 2)
        self.assertEqual(summary["prediction_flip_percent"], 50)
        self.assertEqual(summary["flips_up"], 1)
        self.assertEqual(summary["flips_down"], 1)
        self.assertEqual(probability_difference(baseline, baseline)["prediction_flips"], 0)
        for a, b in (([], []), ([0], [0, 1]), ([float("nan")], [0]), ([1.1], [0])):
            with self.assertRaises(ValueError):
                probability_difference(a, b)

    def test_expected_geometry_and_scale(self):
        for config in (Config(16, 37.5, 24, 10), Config(15, 40, 24, 10), Config(15, 37.5, 24, 20)):
            changed = replace(self.capture,
                              frames=tuple(replace(frame, config=config) for frame in self.capture.frames),
                              configs=tuple((line, config) for line, _ in self.capture.configs))
            with self.subTest(config=config), self.assertRaisesRegex(ValueError, "Trained breath_slot"):
                prepare_features(changed, max_windows=1)
        config = Config(15, 37.5, 23, 10)
        changed = replace(self.capture,
                          frames=tuple(replace(f, config=config, data=replace(f.data, i=f.data.i[:-1], q=f.data.q[:-1]))
                                       for f in self.capture.frames),
                          configs=tuple((line, config) for line, _ in self.capture.configs))
        with self.assertRaisesRegex(ValueError, "Trained breath_slot"):
            prepare_features(changed, max_windows=1)

    def test_missing_malformed_and_discontinuous_data(self):
        rows = BREATHING.read_text().splitlines()
        d_index = next(k for k, row in enumerate(rows[20:], 20) if row.startswith("D,"))
        cases = [[], ["E,1,0,payload"], [row for row in rows if not row.startswith("C,")],
                 rows[:d_index]+rows[d_index+1:], rows[:d_index]+["D,bad,0,1,2"]+rows[d_index:]]
        repeated = rows.copy()
        parts = repeated[d_index].split(",")
        parts[2] = "0"
        repeated[d_index] = ",".join(parts)
        cases.append(repeated)
        for lines in cases:
            with self.subTest(rows=len(lines)), self.assertRaises(ValueError):
                prepare_features(parse_capture(lines), max_windows=1)
        with self.assertRaisesRegex(ValueError, "Bin index"):
            prepare_features(self.capture, 24)

    def test_model_artifact_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "Missing breath_slot"):
                analyze_model_impact(self.capture, max_windows=1, model_dir=directory)
            Path(directory, "breath_slot.json").write_text("not-json")
            np.savez(Path(directory, "breath_slot.npz"), dummy=np.array([1]))
            with self.assertRaisesRegex(ValueError, "Cannot load breath_slot"):
                load_numpy_model(directory)

    def test_bounded_deterministic_window_selection(self):
        self.assertEqual(select_window_ends(999, 8, 3), ([200, 592, 992], 100))
        self.assertEqual(select_window_ends(999, 8, 1), ([200], 100))
        self.assertEqual(select_window_ends(200, 8, 12), ([200], 1))
        self.assertEqual(select_window_ends(999, 400, 12), ([200, 600], 2))
        ends, count = select_window_ends(999, 8, 1000)
        self.assertEqual(len(ends), count)
        self.assertEqual(ends, list(range(200, 1000, 8)))
        for hop, maximum in ((0, 1), (8, 0), (-1, 2), (True, 1), (8, .5)):
            with self.assertRaises(ValueError):
                select_window_ends(999, hop, maximum)
        with self.assertRaisesRegex(ValueError, "At least 200"):
            select_window_ends(199)

    def test_public_samples_and_coverage(self):
        identity = None
        for name, count, candidates in (("breathing", 999, 100), ("hold", 1000, 101), ("leave", 996, 100)):
            with self.subTest(name=name):
                capture = read_capture(ROOT / "recordings/samples" / f"{name}.sample.csv")
                report = analyze_model_impact(capture, max_windows=2)
                self.assertEqual(report["recording"]["observed_d_frames"], count)
                self.assertEqual(report["evaluation"]["candidate_windows"], candidates)
                self.assertEqual(report["evaluation"]["evaluated_windows"], 2)
                self.assertEqual(report["evaluation"]["predictions_per_condition"], 48)
                self.assertFalse(report["evaluation"]["all_candidate_windows_evaluated"])
                self.assertEqual(report["evaluation"]["evaluated_bin_indices"], list(range(24)))
                self.assertEqual(report["selected_bin"]["distance_cm"], 48.75)
                self.assertEqual(report["feature_drift"]["baseline"]["all_bins"]["mean_absolute_difference"], 0)
                for window in report["windows"]:
                    self.assertEqual(window["evaluation_t_ms"], capture.frames[window["end_index_exclusive"]-1].data.t_ms)
                    self.assertEqual({len(p) for p in window["probabilities"].values()}, {24})
                for condition in report["prediction_drift"].values():
                    self.assertEqual(condition["all_bins"]["value_count"], 48)
                    self.assertEqual(condition["selected_bin"]["value_count"], 2)
                if identity is not None:
                    self.assertEqual(report["model"], identity)
                identity = report["model"]
                json.dumps(report, allow_nan=False)

    def test_cli_json_and_missing_artifacts(self):
        command = [sys.executable, "-m", "ml.analysis.model_impact", str(BREATHING), "--max-windows", "2", "--json"]
        outputs = [subprocess.run(command, capture_output=True, text=True) for _ in range(2)]
        self.assertTrue(all(p.returncode == 0 and not p.stderr for p in outputs))
        self.assertEqual(outputs[0].stdout, outputs[1].stdout)
        self.assertEqual(json.loads(outputs[0].stdout)["evaluation"]["evaluated_windows"], 2)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "failed.png"
            for args in (["--hop", "0"], ["--bin", "24"], ["--model-dir", directory]):
                result = subprocess.run(command + args + ["--output", str(output)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertIn("error:", result.stderr)
                self.assertEqual(result.stdout, "")
                self.assertFalse(output.exists())
            missing = subprocess.run(command[:3] + [str(Path(directory)/"missing.csv"), "--json"],
                                     capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)

    def test_optional_report_and_overwrite_protection(self):
        report = analyze_model_impact(self.capture, max_windows=2)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "probabilities.png"
            render_report(report, path)
            original = path.read_bytes()
            self.assertTrue(original.startswith(b"\x89PNG"))
            self.assertGreater(float(imread(path).std()), .05)
            with self.assertRaises(FileExistsError):
                render_report(report, path)
            self.assertEqual(original, path.read_bytes())
            render_report(report, path, force=True)
            with self.assertRaises(ValueError):
                render_report(report, Path(directory) / "bad.svg")


if __name__ == "__main__":
    unittest.main()
