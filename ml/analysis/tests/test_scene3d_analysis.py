"""Case validation belongs to the existing Explorer; this tests its 3D adapter."""
from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ml.analysis.ingest import ingest_recording
from ml.analysis.report import load_dataset
from ml.analysis.spatial_explorer import _PLAYBACK_JS, load_case
from ml.analysis.spatial_scene3d import build_html, render_scene
from ml.analysis.spatial_sim import Simulation, evaluate, generate
from ml.analysis.tests.test_spatial_explorer import write_discontinuous_case


ROOT = Path(__file__).resolve().parents[3]


class Embedded(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.payload = ""
        self.scripts = []
        self.rows = 0
        self.in_json = False
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == "script":
            self.scripts.append(attrs)
            self.in_json = attrs.get("id") == "scene-data"
        if tag == "tr":
            self.rows += 1

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_json = False

    def handle_data(self, data):
        if self.in_json:
            self.payload += data


class SceneAnalysisTest(unittest.TestCase):
    def setUp(self):
        (ROOT / "analysis_runs").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "analysis_runs")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.counter = 0

    def case(self, config=Simulation(duration_sec=8), *, saved=True):
        self.counter += 1
        directory = self.root / f"case_{self.counter}"
        directory.mkdir()
        csv, truth = generate(config)
        (directory / "recording.csv").write_bytes(csv)
        self.write(directory / "truth.json", truth)
        ingest_recording(directory / "recording.csv", directory / "canonical", source_type="synthetic")
        if saved:
            result = evaluate(load_dataset(directory / "canonical"), truth, window_frames=40, hop_frames=20)
            self.write(directory / "evaluation.json", result)
        return directory

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def fingerprint(directory):
        return {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
                for p in directory.rglob("*") if p.is_file()}

    def cli(self, *args):
        return subprocess.run([sys.executable, "-X", "utf8", "-m", "ml.analysis.spatial_scene3d", *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8")

    def test_exact_case_payload_and_shared_playback(self):
        case = self.case()
        expected = load_case(case)
        with patch("ml.analysis.spatial_explorer.load_case", wraps=load_case) as loader:
            path = render_scene(self.root / "scene.html", case=case)
        loader.assert_called_once_with(case)
        html = path.read_text(encoding="utf-8")
        parser = Embedded(html)
        self.assertEqual(json.loads(parser.payload), expected)
        self.assertEqual(parser.rows, len(expected["windows"])+1)
        self.assertEqual(html.count(_PLAYBACK_JS), 1)
        self.assertEqual(html.count("function createPlayback("), 1)
        self.assertEqual(parser.scripts, [{"id": "scene-data", "type": "application/json"}, {}])
        self.assertIn("ベッドとは別", html)
        self.assertIn("高さ・身体の位置は表しません", html)
        self.assertFalse(any("src" in a for _, a in parser.tags))

    def test_deterministic_export_preserves_every_source_byte_and_mtime(self):
        case = self.case()
        before = self.fingerprint(case)
        a = render_scene(self.root / "a.html", case=case)
        b = render_scene(self.root / "b.html", case=case)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(self.fingerprint(case), before)
        with self.assertRaises(FileExistsError):
            render_scene(a, case=case)
        self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_canonical_output_protected(self):
        case = self.case()
        before = self.fingerprint(case)
        target = case / "canonical" / "scene.html"
        with self.assertRaisesRegex(ValueError, "outside the canonical"):
            render_scene(target, case=case)
        self.assertEqual(self.fingerprint(case), before)

    def test_saved_parameters_and_default_parameters_retained(self):
        for saved, expected in ((True, 40), (False, 80)):
            with self.subTest(saved=saved):
                result = load_case(self.case(saved=saved))
                embedded = json.loads(Embedded(build_html(result)).payload)
                self.assertEqual(embedded["parameters"]["window_frames"], expected)
                self.assertEqual(embedded["explorer"], result["explorer"])

    def test_modified_evaluation_rejected_before_any_output(self):
        case = self.case()
        path = case / "evaluation.json"
        result = json.loads(path.read_bytes())
        result["windows"][0]["position_cm"]["x"] += 7
        self.write(path, result)
        target = self.root / "new" / "scene.html"
        with self.assertRaisesRegex(ValueError, "differs"):
            render_scene(target, case=case)
        self.assertFalse(target.parent.exists())

    def test_modified_canonical_artifact_rejected(self):
        case = self.case()
        (case / "canonical" / "events.jsonl").write_bytes(b"{}\n")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            render_scene(self.root / "bad.html", case=case)

    def test_non_synthetic_and_inconsistent_truth_rejected(self):
        case = self.case()
        path = case / "canonical" / "manifest.json"
        original = json.loads(path.read_bytes())
        for source in ("board_capture", "repository_sample", "unknown"):
            bad = deepcopy(original)
            bad.update(source_type=source, source_type_provenance="unspecified" if source == "unknown" else "operator_declared")
            self.write(path, bad)
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "synthetic"):
                render_scene(self.root / "bad.html", case=case)
        self.write(path, original)
        truth_path = case / "truth.json"
        truth = json.loads(truth_path.read_bytes())
        truth["input_sha256"] = "0"*64
        self.write(truth_path, truth)
        with self.assertRaises(ValueError):
            render_scene(self.root / "bad.html", case=case)

    def test_all_scenario_values_and_window_order_unchanged(self):
        configs = (Simulation(duration_sec=8), Simulation(duration_sec=8, angle_deg=-30, end_angle_deg=30),
                   Simulation(duration_sec=8, motion_amplitude=2, noise_std=40), Simulation(duration_sec=8, angle_deg=150))
        for config in configs:
            with self.subTest(config=config):
                result = load_case(self.case(config))
                embedded = json.loads(Embedded(build_html(result)).payload)
                self.assertEqual(embedded["windows"], result["windows"])
                self.assertEqual(embedded["verified_artifact_hashes"], result["verified_artifact_hashes"])
                if config.noise_std:
                    self.assertTrue(all(w["position_cm"] is None for w in embedded["windows"]))
                if config.angle_deg == 150:
                    self.assertTrue(all(w["absolute_angular_error_deg"] == 120 for w in embedded["windows"]))

    def test_gaps_and_restarts_not_joined_or_reordered(self):
        case = write_discontinuous_case(self.root / "discontinuous")
        result = load_case(case)
        actual = json.loads(Embedded(build_html(result)).payload)
        self.assertEqual(actual["windows"], result["windows"])
        self.assertGreater(len({w["parent_block_index"] for w in actual["windows"]}), 1)
        self.assertGreater(len({w["trace_index"] for w in actual["windows"]}), 1)

    def test_inert_json_cannot_escape_script_or_replace_template_tokens(self):
        result = load_case(self.case())
        attack = '</script><img src="https://invalid.example">&\u2028\u2029__SCENE_BUNDLE____PLAYBACK_JS____CASE_DATA__'
        result["simulation"]["limitations"] = attack
        parser = Embedded(build_html(result))
        self.assertEqual(json.loads(parser.payload), result)
        self.assertNotIn("<", parser.payload)
        self.assertEqual(len(parser.scripts), 2)
        self.assertFalse(any(t == "img" for t, _ in parser.tags))

    def test_missing_directory_and_malformed_case_errors(self):
        for case in (self.root / "missing", self.root):
            with self.subTest(case=case), self.assertRaises(ValueError):
                render_scene(self.root / "bad.html", case=case)
        case = self.case()
        (case / "truth.json").write_bytes(b'{"value":NaN}')
        with self.assertRaises(ValueError):
            render_scene(self.root / "bad.html", case=case)

    def test_empty_capture_rejected_without_output(self):
        case = self.root / "empty"
        case.mkdir()
        (case / "recording.csv").write_bytes(b"C,15,37.5,24,10\n")
        self.write(case / "truth.json", {})
        ingest_recording(case / "recording.csv", case / "canonical", source_type="synthetic")
        target = self.root / "bad.html"
        with self.assertRaisesRegex(ValueError, "No E frames"):
            render_scene(target, case=case)
        self.assertFalse(target.exists())

    def test_missing_optional_dependencies_actionable(self):
        with patch("ml.analysis.spatial_explorer.load_case", side_effect=ImportError("missing package")):
            with self.assertRaisesRegex(ValueError, "offline dependencies"):
                render_scene(self.root / "bad.html", case=self.root)

    def test_cli_case_success_missing_case_and_no_overwrite(self):
        case = self.case()
        output = self.root / "cli.html"
        result = self.cli("--case", case, "--output", output)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(Embedded(output.read_text(encoding="utf-8")).payload), load_case(case))
        before = output.read_bytes()
        for source, target in ((case, output), (self.root / "missing", self.root / "new" / "bad.html")):
            result = self.cli("--case", source, "--output", target)
            self.assertEqual(result.returncode, 2)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("error:", result.stderr)
        self.assertEqual(output.read_bytes(), before)

    def test_no_case_still_uses_only_standard_library(self):
        result = subprocess.run([sys.executable, "-X", "utf8", "-c",
                                 "import sys; from ml.analysis.spatial_scene3d import build_html; "
                                 "assert '<script id=\"scene-data\"' not in build_html(); assert 'numpy' not in sys.modules"],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
