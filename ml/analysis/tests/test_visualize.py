from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.image import imread

from ml.analysis.protocol import parse_capture, read_capture
from ml.analysis.quality import analyze_quality
from ml.analysis.visualize import build_figure, prepare_visualization, render_report


def synthetic():
    return parse_capture(["D,1,900000,10,20,30,40", "C,20,50,2,10",
                          "D,2,900060,30,40,50,60", "C,20,50,2,10",
                          "D,4,900300,50,60,70,80"])


class VisualizationTests(unittest.TestCase):
    def test_numeric_preparation(self):
        capture = synthetic()
        data = prepare_visualization(capture, 1)
        np.testing.assert_array_equal(data.distances_cm, [20, 25])
        np.testing.assert_array_equal(data.z, [[1+2j, 3+4j], [3+4j, 5+6j], [5+6j, 7+8j]])
        np.testing.assert_allclose(data.variation, [[np.sqrt(8)]*2, [0]*2, [np.sqrt(8)]*2])
        np.testing.assert_array_equal(data.elapsed_s, [0, .06, .3])
        self.assertEqual(data.start_t_ms, 900000)
        self.assertEqual(data.quality.to_dict(), analyze_quality(capture).to_dict())
        self.assertEqual(data.quality.estimated_missing_frames, 1)
        self.assertEqual([(s.start, s.stop) for s in data.trace_segments], [(0, 2), (2, 3)])

    def test_figure_coordinates_labels_and_summary(self):
        data = prepare_visualization(synthetic(), 1)
        figure = build_figure(data)
        try:
            self.assertIsInstance(figure.canvas, FigureCanvasAgg)
            heat, trace, summary = figure.axes[:3]
            columns = heat.collections[0]
            segments = np.asarray(columns.get_segments()).reshape(3, 2, 2, 2)
            np.testing.assert_array_equal(segments[:, 0, 0, 0], data.elapsed_s)
            np.testing.assert_array_equal(segments[0, :, :, 1], [[17.5, 22.5], [22.5, 27.5]])
            np.testing.assert_allclose(columns.get_array(), data.variation.ravel())
            self.assertIn("Bin 1 at 25 cm", trace.get_title())
            np.testing.assert_array_equal(trace.lines[0].get_xdata(), [0, .06])
            np.testing.assert_array_equal(trace.lines[0].get_ydata(), [3, 5])
            self.assertEqual({line.get_label() for line in trace.lines if not line.get_label().startswith("_")},
                             {"I (real)", "Q (imaginary)"})
            self.assertIn("board t_ms = 900000", heat.get_xlabel())
            self.assertIn("entire recording", summary.get_title(loc="left"))
            self.assertIn("Estimated missing: 1", summary.texts[0].get_text())
            self.assertIn("Long intervals: unavailable", summary.texts[0].get_text())
        finally:
            figure.clear()

    def test_large_uptime_subtracts_before_float_conversion(self):
        start = 2**60
        capture = parse_capture(["C,1,10,1,2", f"D,1,{start},2,4", f"D,2,{start+1},4,6"])
        np.testing.assert_array_equal(prepare_visualization(capture).elapsed_s, [0, .001])

    def test_invalid_bins(self):
        for index in (-1, 2, 1.2, True):
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "Bin index"):
                prepare_visualization(synthetic(), index)

    def test_rejected_captures_create_no_output(self):
        cases = [([], "No parsed D"), (["E,1,0,payload"], "E-only"),
                 (["D,1,0,1,2"], "usable C"),
                 (["C,1,10,2,10", "D,1,0,1,2"], "matching I/Q"),
                 (["C,1,10,1,10", "D,1,100,1,2", "D,2,0,1,2"], "Timestamps"),
                 (["C,1,10,1,10", "D,1,100,1,2", "D,2,100,1,2"], "Timestamps"),
                 (["C,1,10,1,10", "D,2,0,1,2", "D,1,60,1,2"], "sequence"),
                 (["C,1,10,1,10", "D,1,0,1,2", "D,1,60,1,2"], "sequence")]
        for config in ("C,2,10,1,10", "C,1,20,1,10", "C,1,10,1,20", "C,1,10,2,10"):
            cases.append((["C,1,10,1,10", "D,1,0,1,2", config], "Configuration changes"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "invalid.png"
            for rows, error in cases:
                with self.subTest(rows=rows), self.assertRaisesRegex(ValueError, error):
                    render_report(parse_capture(rows), output)
                self.assertFalse(output.exists())
            with self.assertRaisesRegex(ValueError, "Bin index"):
                render_report(synthetic(), output, 5)
            self.assertFalse(output.exists())

    def test_malformed_rows_are_counted_and_trace_is_broken(self):
        capture = parse_capture(["C,1,10,1,10", "D,1,0,1,2", "D,bad,30,1,2", "D,2,60,3,4"])
        data = prepare_visualization(capture)
        self.assertEqual(data.quality.parser_issue_count, 1)
        self.assertEqual(len(data.elapsed_s), 2)
        self.assertEqual(len(data.trace_segments), 2)
        figure = build_figure(data)
        self.assertIn("Parser issues: 1", figure.axes[2].texts[0].get_text())
        figure.clear()

    def test_long_interval_breaks_trace(self):
        rows = ["C,1,10,1,10"] + [f"D,{k+1},{t},1,2" for k, t in enumerate([0,60,120,180,500])]
        data = prepare_visualization(parse_capture(rows))
        self.assertEqual(data.quality.long_interval_events, 1)
        self.assertEqual(len(data.trace_segments), 2)

    def test_png_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.png"
            render_report(synthetic(), output)
            original = output.read_bytes()
            self.assertTrue(original.startswith(b"\x89PNG\r\n\x1a\n"))
            pixels = imread(output)
            self.assertEqual(pixels.shape[:2], (1500, 1950))
            self.assertGreater(float(pixels.std()), .05)
            with self.assertRaisesRegex(FileExistsError, "--force"):
                render_report(synthetic(), output)
            self.assertEqual(output.read_bytes(), original)
            render_report(synthetic(), output, 1, force=True)
            self.assertNotEqual(output.read_bytes(), original)
            replacement = output.read_bytes()
            with self.assertRaises(ValueError):
                render_report(parse_capture([]), output, force=True)
            self.assertEqual(output.read_bytes(), replacement)
            with self.assertRaisesRegex(ValueError, ".png"):
                render_report(synthetic(), Path(directory) / "report.svg")
            self.assertFalse((Path(directory) / "report.svg").exists())

    def test_single_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            render_report(parse_capture(["C,15,37.5,1,10", "D,1,1234,10,20"]),
                          Path(directory) / "one.png")

    def test_public_recordings(self):
        root = Path(__file__).resolve().parents[3]
        with tempfile.TemporaryDirectory() as directory:
            for name, count in (("breathing", 999), ("hold", 1000), ("leave", 996)):
                with self.subTest(name=name):
                    capture = read_capture(root / "recordings/samples" / f"{name}.sample.csv")
                    data = prepare_visualization(capture, 10)
                    self.assertEqual(data.z.shape, (count, 24))
                    self.assertEqual(data.distances_cm[10], 52.5)
                    self.assertEqual(data.quality.total_d_frames, count)
                    output = render_report(capture, Path(directory) / f"{name}.png", 10)
                    self.assertGreater(output.stat().st_size, 10000)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path, output = Path(directory) / "capture.csv", Path(directory) / "report.png"
            path.write_text("C,1,10,1,10\nD,1,0,1,2\nD,2,60,3,4\n")
            command = [sys.executable, "-m", "ml.analysis.visualize", str(path), "--output", str(output)]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(str(output), result.stdout)
            for args in ([], ["--bin", "-1"], ["--bin", "bad"]):
                failed = subprocess.run(command + args, capture_output=True, text=True)
                self.assertEqual(failed.returncode, 2)
                self.assertIn("error:", failed.stderr)
                self.assertEqual(failed.stdout, "")
            forced = subprocess.run(command + ["--force"], capture_output=True, text=True)
            self.assertEqual(forced.returncode, 0, forced.stderr)
            output.unlink()
            missing = subprocess.run(command[:3] + [str(path)+".missing", "--output", str(output)],
                                     capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
