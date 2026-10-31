import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.image import imread
from scipy.signal import sosfreqz

from ml.analysis.denoise import (FilterParameters, benchmark_capture, benchmark_synthetic,
                                build_figure, design_filters, filter_iq, make_synthetic,
                                render_report, validate_cadence)
from ml.analysis.protocol import parse_capture, read_capture


def recording():
    return parse_capture(["C,20,50,1,10"] +
                         [f"D,{k},{900000+k*50},{k*10},{k*20}" for k in range(32)])


class DenoiseTests(unittest.TestCase):
    def test_seeded_ground_truth_recovery(self):
        for fs in (10, 20):
            with self.subTest(fs=fs):
                result = benchmark_synthetic(fs)
                baseline = result.report["metrics"]["baseline"]
                self.assertEqual(baseline["snr_improvement_db"], 0)
                self.assertEqual(baseline["change_rms_vs_baseline"], 0)
                for name in ("lowpass", "bandpass"):
                    row = result.report["metrics"][name]
                    self.assertGreater(row["snr_improvement_db"], 2)
                    self.assertLess(row["reconstruction_rmse"], baseline["reconstruction_rmse"])
                    self.assertLess(abs(row["signal_attenuation_db"]), .1)
                    self.assertLess(row["power_above_lowpass_fraction"], baseline["power_above_lowpass_fraction"])
                    self.assertGreater(row["post_cessation_observed_rms"], row["post_cessation_residual_rms"])

    def test_delay_gain_and_causality(self):
        case = make_synthetic()
        outputs = filter_iq(case.observed, 20)
        prefix = filter_iq(case.observed[:237], 20)
        result = benchmark_synthetic()
        designs = design_filters(20, FilterParameters())
        for name in outputs:
            np.testing.assert_array_equal(outputs[name][:237], prefix[name])
            if name == "baseline":
                continue
            _, h = sosfreqz(designs[name], worN=[.3], fs=20)
            row = result.report["metrics"][name]
            self.assertAlmostEqual(row["signal_attenuation_db"], -20*np.log10(abs(h[0])), delta=.003)
            self.assertAlmostEqual(row["phase_delay_sec"], -np.angle(h[0])/(2*np.pi*.3), delta=.003)
            self.assertGreater(row["group_delay_sec"], 0)
        self.assertGreater(result.report["metrics"]["lowpass"]["phase_delay_sec"], .1)
        # Signed narrowband phase lead does not imply access to future samples.
        self.assertLess(result.report["metrics"]["bandpass"]["phase_delay_sec"], 0)
        np.testing.assert_array_equal(outputs["baseline"], case.observed)
        for name in outputs:
            separate = filter_iq(case.observed.real, 20)[name] + 1j*filter_iq(case.observed.imag, 20)[name]
            np.testing.assert_allclose(outputs[name], separate, atol=1e-13)

    def test_cessation_residual_and_continuous_state(self):
        case = make_synthetic()
        outputs = filter_iq(case.clean, 20)
        stopped = case.t_ms >= 40000
        self.assertTrue(np.all(case.clean[stopped] == 0))
        self.assertGreater(np.max(np.abs(outputs["bandpass"][stopped])), .1)
        self.assertGreater(np.max(np.abs(outputs["lowpass"][stopped])), .1)
        restarted = filter_iq(case.clean[stopped], 20)
        self.assertTrue(np.all(restarted["bandpass"] == 0))
        result = benchmark_synthetic().report["metrics"]
        self.assertEqual(result["baseline"]["post_cessation_residual_rms"], 0)
        self.assertGreater(result["bandpass"]["post_cessation_residual_rms"], result["lowpass"]["post_cessation_residual_rms"])
        self.assertGreater(result["bandpass"]["settling_time_sec"], 5)
        self.assertLess(result["lowpass"]["settling_time_sec"], 1)

    def test_constant_iq_and_scaling(self):
        z = np.full(400, 3+4j)
        output = filter_iq(z, 20)
        np.testing.assert_allclose(output["lowpass"], z, atol=1e-12)
        np.testing.assert_allclose(output["bandpass"], 0, atol=1e-12)
        result = benchmark_capture(recording(), 0)
        np.testing.assert_array_equal(result.signals["baseline"], np.arange(32)*(1+2j))
        np.testing.assert_allclose(result.elapsed_s, np.arange(32)*.05, rtol=0, atol=1e-15)
        self.assertEqual(result.report["source"]["distance_cm"], 20)
        self.assertEqual(result.report["start_t_ms"], 900000)

    def test_configuration_and_invalid_capture(self):
        cases = [[], ["E,1,0,payload"], ["D,1,0,1,2"],
                 ["C,1,10,2,1", "D,1,0,1,2"],
                 ["C,1,10,1,1", "D,1,0,1,2", "C,1,10,1,2"]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                benchmark_capture(parse_capture(rows), 0)
        with self.assertRaisesRegex(ValueError, "Bin index"):
            benchmark_capture(recording(), 1)

    def test_gaps_parser_errors_and_irregular_timing(self):
        rows = ["C,1,10,1,1"] + [f"D,{k},{k*50},1,2" for k in range(32)]
        for changed in (rows[:10]+rows[11:], rows[:10]+["D,bad,0,1,2"]+rows[10:]):
            with self.assertRaisesRegex(ValueError, "issues or sequence gaps"):
                benchmark_capture(parse_capture(changed), 0)
        for stamp in (465, 400, 0):
            changed = rows.copy()
            changed[10] = f"D,9,{stamp},1,2"
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                benchmark_capture(parse_capture(changed), 0)
        with self.assertRaisesRegex(ValueError, "integer milliseconds"):
            validate_cadence([0., 50., 100., 150., 200., 250., 300., 350.5])
        with self.assertRaises(ValueError):
            validate_cadence([0, 1])

    def test_invalid_parameters_and_values(self):
        invalid = [FilterParameters(order=0), FilterParameters(order=13), FilterParameters(order=True),
                   FilterParameters(order=1.5), FilterParameters(lowpass_hz=0),
                   FilterParameters(lowpass_hz=10), FilterParameters(lowpass_hz=float("nan")),
                   FilterParameters(band_low_hz=2, band_high_hz=1), FilterParameters(band_high_hz=float("inf"))]
        for parameters in invalid:
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                filter_iq(np.ones(16), 20, parameters)
        for fs in (0, -1, float("inf"), float("nan")):
            with self.subTest(fs=fs), self.assertRaises(ValueError):
                filter_iq(np.ones(16), fs)
        for z in ([], [[1, 2]], [1, float("nan")]):
            with self.assertRaises(ValueError):
                filter_iq(z, 20)
        with self.assertRaises(ValueError):
            make_synthetic(seed=-1)

    def test_different_sample_rates(self):
        for fs in (10, 12.5, 20):
            result = benchmark_synthetic(fs)
            self.assertAlmostEqual(result.report["cadence"]["sample_hz"], fs, places=8)
            self.assertEqual(result.report["observed_samples"], round(60*fs))
            self.assertEqual(result.report["parameters"], {"order": 2, "lowpass_hz": 1.5,
                                                          "band_low_hz": .1, "band_high_hz": 1.5})
        high_order = filter_iq(make_synthetic().observed, 20, FilterParameters(order=3, lowpass_hz=2))
        self.assertTrue(np.isfinite(high_order["lowpass"]).all())

    def test_deterministic_outputs(self):
        a, b = benchmark_synthetic(), benchmark_synthetic()
        self.assertEqual(json.dumps(a.to_dict(), sort_keys=True, allow_nan=False),
                         json.dumps(b.to_dict(), sort_keys=True, allow_nan=False))
        for name in a.signals:
            np.testing.assert_array_equal(a.signals[name], b.signals[name])
        copy = a.to_dict()
        copy["metrics"]["baseline"]["snr_db"] = 99
        self.assertNotEqual(a.report["metrics"]["baseline"]["snr_db"], 99)

    def test_headless_png_overwrite(self):
        comparison = benchmark_synthetic()
        figure = build_figure(comparison)
        self.assertIsInstance(figure.canvas, FigureCanvasAgg)
        self.assertEqual([ax.get_title() for ax in figure.axes[:6:2]], ["baseline", "lowpass", "bandpass"])
        figure.clear()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "comparison.png"
            render_report(comparison, path)
            original = path.read_bytes()
            self.assertTrue(original.startswith(b"\x89PNG"))
            self.assertGreater(float(imread(path).std()), .05)
            with self.assertRaises(FileExistsError):
                render_report(comparison, path)
            self.assertEqual(original, path.read_bytes())
            render_report(comparison, path, force=True)
            with self.assertRaises(ValueError):
                render_report(comparison, Path(directory) / "bad.svg")

    def test_public_recordings_descriptive_only(self):
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as directory:
            for name, count in (("breathing", 999), ("hold", 1000), ("leave", 996)):
                with self.subTest(name=name):
                    result = benchmark_capture(read_capture(root / "recordings/samples" / f"{name}.sample.csv"))
                    self.assertEqual(result.report["observed_samples"], count)
                    self.assertEqual(result.report["source"]["distance_cm"], 48.75)
                    self.assertEqual(result.report["source"]["baseline_temporal"]["quality"]["total_d_frames"], count)
                    for row in result.report["metrics"].values():
                        for metric in ("snr_db", "reconstruction_rmse", "signal_attenuation_db",
                                       "phase_delay_sec", "post_cessation_residual_rms"):
                            self.assertIsNone(row[metric])
                    render_report(result, Path(directory) / f"{name}.png")
                    json.dumps(result.to_dict(), allow_nan=False)

    def test_cli_json_and_failures(self):
        command = [sys.executable, "-m", "ml.analysis.denoise", "--synthetic", "--json"]
        outputs = [subprocess.run(command, capture_output=True, text=True) for _ in range(2)]
        self.assertTrue(all(r.returncode == 0 and not r.stderr for r in outputs))
        self.assertEqual(outputs[0].stdout, outputs[1].stdout)
        self.assertEqual(json.loads(outputs[0].stdout)["source"]["seed"], 7)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.png"
            for args in (["--order", "0"], ["--bin", "0"], ["--sample-hz", "0"]):
                failed = subprocess.run(command + args + ["--output", str(path)], capture_output=True, text=True)
                self.assertEqual(failed.returncode, 2)
                self.assertEqual(failed.stdout, "")
                self.assertFalse(path.exists())
            result = subprocess.run(command + ["--output", str(path)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            json.loads(result.stdout)
            original = path.read_bytes()
            failed = subprocess.run(command + ["--output", str(path)], capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(path.read_bytes(), original)
            forced = subprocess.run(command + ["--output", str(path), "--force"], capture_output=True, text=True)
            self.assertEqual(forced.returncode, 0, forced.stderr)
            failed = subprocess.run(command + ["--order", "0", "--output", str(path), "--force"],
                                    capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
