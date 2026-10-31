from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

import numpy as np

from ml.analysis.protocol import parse_capture, read_capture
from ml.analysis.quality import analyze_quality
from ml.analysis.signal import analyze_signal, project_iq
from ml.features import WINDOW, window_features


def sinusoid(frequency=.5, step_ms=50, duration_ms=12000, start=900000):
    rows = ["C,15,37.5,1,100"]
    for k, t in enumerate(range(0, duration_ms + 1, step_ms)):
        a = 10000 * np.sin(2 * np.pi * frequency * t / 1000)
        i = round(50000 + a * np.cos(.7))
        q = round(30000 + a * np.sin(.7))
        rows.append(f"D,{k},{start+t},{i},{q}")
    return rows


class SignalTests(unittest.TestCase):
    def test_known_frequencies_and_scaled_rms(self):
        for frequency in (.25, .5, 1):
            with self.subTest(frequency=frequency):
                report = analyze_signal(parse_capture(sinusoid(frequency)))
                window = report.windows[0]
                self.assertEqual(window.spectral_status, "available")
                self.assertEqual(window.observed_samples, 240)
                self.assertEqual(window.window_start_t_ms, 900000)
                self.assertEqual(window.window_end_t_ms, 912000)
                self.assertEqual(window.observed_end_t_ms, 911950)
                self.assertEqual(window.sampling_interval_ms, 50)
                self.assertAlmostEqual(window.dominant_frequency_hz, frequency, places=8)
                self.assertAlmostEqual(window.spectral_resolution_hz, 1/12)
                self.assertGreater(window.spectral_concentration, .999)
                self.assertAlmostEqual(window.rms_variation, 100/np.sqrt(2), delta=.001)
                self.assertAlmostEqual(window.projected_rms, window.rms_variation, delta=.001)

    def test_measured_cadence_not_assumed_model_rate(self):
        window = analyze_signal(parse_capture(sinusoid(step_ms=100))).windows[0]
        self.assertEqual(window.sampling_interval_ms, 100)
        self.assertAlmostEqual(window.dominant_frequency_hz, .5)

    def test_projection_matches_existing_features(self):
        t = np.arange(WINDOW) * .06
        z = 7+3j + 10 * np.exp(.7j) * np.sin(2*np.pi*.5*t)
        bins = np.zeros((WINDOW, 24), complex)
        bins[:, 9] = z
        ref = np.abs(z-z.mean()).mean()
        features = window_features(bins)
        np.testing.assert_allclose(features[9, :, 6] * ref,
                                   project_iq(z).reshape(-1, 2).mean(axis=1), atol=2e-6)

    def test_constant(self):
        rows = ["C,15,37.5,1,10"] + [f"D,{k},{k*50},11,33" for k in range(241)]
        window = analyze_signal(parse_capture(rows)).windows[0]
        self.assertEqual(window.rms_variation, 0)
        self.assertEqual(window.projected_rms, 0)
        self.assertEqual(window.spectral_status, "constant_signal")
        self.assertIsNone(window.dominant_frequency_hz)
        self.assertIsNone(window.spectral_concentration)

    def test_short_single_empty_and_partial_windows(self):
        one = analyze_signal(parse_capture(["C,1,10,1,1", "D,1,1234,1,2"]))
        self.assertEqual(one.windows[0].spectral_status, "insufficient_samples")
        self.assertIsNone(one.windows[0].rms_variation)
        short = analyze_signal(parse_capture(sinusoid(duration_ms=1000)))
        self.assertEqual(short.windows[0].spectral_status, "partial_window")
        self.assertIsNotNone(short.windows[0].rms_variation)
        empty = analyze_signal(parse_capture(["C,1,10,1,1", "D,1,0,1,2", "D,2,20000,3,4"]))
        self.assertTrue(any(w.observed_samples == 0 for w in empty.windows))
        for window in empty.windows:
            self.assertIsNone(window.dominant_frequency_hz)

    def test_sequence_gap_does_not_get_filled(self):
        rows = sinusoid()
        del rows[31]
        window = analyze_signal(parse_capture(rows)).windows[0]
        self.assertEqual(window.observed_samples, 239)
        self.assertEqual(window.spectral_status, "sequence_gap")
        self.assertIsNone(window.dominant_frequency_hz)
        self.assertIsNotNone(window.rms_variation)

    def test_irregular_and_small_quantization_jitter(self):
        rows = sinusoid()
        p = rows[31].split(",")
        p[2] = str(int(p[2]) + 10)
        rows[31] = ",".join(p)
        window = analyze_signal(parse_capture(rows)).windows[0]
        self.assertEqual(window.spectral_status, "irregular_timing")
        self.assertIsNone(window.spectral_concentration)
        rows = sinusoid()
        for k in range(2, len(rows)-1):
            p = rows[k].split(",")
            p[2] = str(int(p[2]) + (k % 2))
            rows[k] = ",".join(p)
        window = analyze_signal(parse_capture(rows)).windows[0]
        self.assertEqual(window.spectral_status, "available")

    def test_parser_issues_block_spectrum_but_remain_in_quality(self):
        rows = sinusoid()
        rows.insert(20, "D,bad,0,1,2")
        capture = parse_capture(rows)
        report = analyze_signal(capture)
        self.assertEqual(report.windows[0].spectral_status, "parser_issues")
        self.assertEqual(report.quality.to_dict(), analyze_quality(capture).to_dict())

    def test_validation(self):
        cases = [[], ["E,1,0,payload"], ["D,1,0,1,2"],
                 ["C,1,10,2,1", "D,1,0,1,2"],
                 ["C,1,10,1,1", "D,1,0,1,2", "C,1,10,1,2"],
                 ["C,1,10,1,1", "D,1,100,1,2", "D,2,0,1,2"],
                 ["C,1,10,1,1", "D,1,100,1,2", "D,2,100,1,2"],
                 ["C,1,10,1,1", "D,2,0,1,2", "D,1,100,1,2"]]
        for rows in cases:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                analyze_signal(parse_capture(rows))
        capture = parse_capture(sinusoid())
        for index in (-1, 1, .5, True):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "Bin index"):
                analyze_signal(capture, index)
        for value in (0, -1, float("nan"), float("inf"), .0001, .000000001):
            for parameter in ("window_sec", "hop_sec"):
                with self.subTest(value=value, parameter=parameter), self.assertRaises(ValueError):
                    analyze_signal(capture, **{parameter: value})

    def test_window_boundaries_and_large_uptime(self):
        start = 2**60
        report = analyze_signal(parse_capture(sinusoid(start=start)), window_sec=6, hop_sec=2)
        self.assertEqual(report.windows[0].window_start_t_ms, start)
        self.assertEqual(report.windows[1].window_start_t_ms, start+2000)
        self.assertEqual(report.windows[0].observed_samples, 120)
        self.assertAlmostEqual(report.windows[0].dominant_frequency_hz, .5)

    def test_public_samples(self):
        root = Path(__file__).resolve().parents[3]
        for name, count in (("breathing", 999), ("hold", 1000), ("leave", 996)):
            with self.subTest(name=name):
                report = analyze_signal(read_capture(root / "recordings/samples" / f"{name}.sample.csv"), 9)
                self.assertEqual(report.quality.total_d_frames, count)
                self.assertEqual(report.distance_cm, 48.75)
                self.assertTrue(any(w.spectral_status == "available" for w in report.windows))
                for w in report.windows:
                    if w.spectral_status == "available":
                        self.assertGreater(w.dominant_frequency_hz, 0)
                        self.assertTrue(0 <= w.spectral_concentration <= 1)
                    else:
                        self.assertIsNone(w.dominant_frequency_hz)
                json.dumps(report.to_dict(), allow_nan=False)

    def test_cli_json_and_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.csv"
            path.write_text("\n".join(sinusoid()))
            command = [sys.executable, "-m", "ml.analysis.signal", str(path)]
            results = [subprocess.run(command + ["--json"], capture_output=True, text=True) for _ in range(2)]
            self.assertTrue(all(r.returncode == 0 and not r.stderr for r in results))
            self.assertEqual(results[0].stdout, results[1].stdout)
            self.assertEqual(json.loads(results[0].stdout)["bin_index"], 0)
            text = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(text.returncode, 0)
            self.assertIn("not breathing", text.stdout)
            for args in (["--bin", "-1"], ["--window-sec", "nan"], ["--hop-sec", "0"]):
                result = subprocess.run(command + args, capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertIn("error:", result.stderr)
            missing = subprocess.run(command[:-1] + [str(path)+".missing", "--json"],
                                     capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
