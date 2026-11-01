from copy import deepcopy
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from ml.analysis.ingest import ingest_recording
from ml.analysis.report import load_dataset
from ml.analysis.spatial_explorer import _PLAYBACK_JS, build_html, load_case, render_explorer
from ml.analysis.spatial_sim import Simulation, evaluate, generate


def write_discontinuous_case(directory):
    """Small deterministic synthetic fixture: gap, then sequence/uptime restart.

    Also callable by the documented offline browser check; never edits an input.
    """
    directory = Path(directory)
    if directory.exists():
        raise FileExistsError("Fixture already exists; choose a fresh directory")
    csv, truth = generate(Simulation(duration_sec=18, angle_deg=-30, end_angle_deg=30))
    lines = csv.decode().splitlines()
    kept = [i for i in range(len(truth["angle_deg"])) if i != 120]
    result = [lines[0]]
    times = []
    for i in kept:
        fields = lines[i+1].split(",")
        if i >= 240:
            fields[1], fields[2] = str(i-240), str(100000+(i-240)*50)
        times.append(int(fields[2]))
        result.append(",".join(fields))
    data = ("\n".join(result)+"\n").encode()
    truth["angle_deg"] = [truth["angle_deg"][i] for i in kept]
    truth["board_uptime_ms"] = times
    truth["input_sha256"] = hashlib.sha256(data).hexdigest()
    directory.mkdir(parents=True)
    (directory / "recording.csv").write_bytes(data)
    (directory / "truth.json").write_text(json.dumps(truth, sort_keys=True), encoding="utf-8")
    ingest_recording(directory / "recording.csv", directory / "canonical", source_type="synthetic",
                     scenario="playback boundary fixture", scenario_provenance="synthetic gap at frame 120; restart at frame 240")
    evaluated = evaluate(load_dataset(directory / "canonical"), truth)
    (directory / "evaluation.json").write_text(json.dumps(evaluated, sort_keys=True), encoding="utf-8")
    return directory


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.payload = ""
        self.in_data = False

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))
        if tag == "script":
            self.in_data = dict(attrs).get("id") == "explorer-data"

    def handle_endtag(self, tag):
        if tag == "script":
            self.in_data = False

    def handle_data(self, data):
        if self.in_data:
            self.payload += data


class SpatialExplorerTests(unittest.TestCase):
    def setUp(self):
        runs = Path(__file__).resolve().parents[3] / "analysis_runs"
        runs.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="explorer-test-", dir=runs)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.counter = 0

    def case(self, config=Simulation(duration_sec=8), rewrite=None, saved=False):
        self.counter += 1
        folder = self.root / f"case_{self.counter}"
        folder.mkdir()
        csv, truth = generate(config)
        if rewrite:
            csv, truth = rewrite(csv, truth)
            truth["input_sha256"] = hashlib.sha256(csv).hexdigest()
        (folder / "recording.csv").write_bytes(csv)
        self.write(folder / "truth.json", truth)
        ingest_recording(folder / "recording.csv", folder / "canonical", source_type="synthetic",
                         scenario="unit fixture", scenario_provenance="explicit simulation only")
        if saved:
            result = evaluate(load_dataset(folder / "canonical"), truth, window_frames=40, hop_frames=20)
            self.write(folder / "evaluation.json", result)
        return folder

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_saved_parameters_verified_and_default_case_supported(self):
        case = self.case(saved=True)
        saved = json.loads((case / "evaluation.json").read_bytes())
        result = load_case(case)
        self.assertEqual(result["windows"], saved["windows"])
        self.assertEqual(result["parameters"]["window_frames"], 40)
        self.assertEqual(result["explorer"]["evaluation_source"], "verified_saved_parameters")
        self.assertEqual(result["explorer"]["saved_evaluation_sha256"],
                         hashlib.sha256((case / "evaluation.json").read_bytes()).hexdigest())
        default = load_case(self.case())
        self.assertEqual(default["parameters"]["window_frames"], 80)
        self.assertIsNone(default["explorer"]["saved_evaluation_sha256"])

    def test_source_and_truth_provenance_required(self):
        case = self.case()
        path = case / "canonical/manifest.json"
        original = json.loads(path.read_bytes())
        for source in ("board_capture", "repository_sample", "unknown"):
            manifest = deepcopy(original)
            manifest["source_type"] = source
            manifest["source_type_provenance"] = "unspecified" if source == "unknown" else "operator_declared"
            self.write(path, manifest)
            with self.assertRaisesRegex(ValueError, "synthetic"):
                load_case(case)
        self.write(path, original)
        truth_path = case / "truth.json"
        truth = json.loads(truth_path.read_bytes())
        for key, value in (("synthetic", False), ("hardware_validated", True), ("input_sha256", "0"*64),
                           ("antenna_y_wavelengths", [0, 1, 0]), ("version", 2)):
            bad = deepcopy(truth)
            bad[key] = value
            self.write(truth_path, bad)
            with self.assertRaises(ValueError):
                load_case(case)

    def test_artifact_and_saved_evaluation_tampering_rejected(self):
        case = self.case(saved=True)
        path = case / "evaluation.json"
        saved = json.loads(path.read_bytes())
        for modify in (lambda r: r["windows"][0]["position_cm"].update(x=99),
                       lambda r: r["parameters"].update(min_movement="invalid"),
                       lambda r: r["parameters"].pop("window_frames")):
            bad = deepcopy(saved)
            modify(bad)
            self.write(path, bad)
            with self.assertRaises(ValueError):
                load_case(case)
        self.write(path, saved)
        (case / "canonical/events.jsonl").write_bytes(b"{}\n")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            load_case(case)

    def test_missing_incompatible_geometry_and_empty_capture(self):
        for header in (b"", b"C,20,50,24,10\n", b"C,15,37.5,1,10\n"):
            case = self.case(rewrite=lambda csv, truth: (header+csv.split(b"\n", 1)[1], truth))
            with self.assertRaisesRegex(ValueError, "geometry"):
                load_case(case)
        def empty(csv, truth):
            truth["angle_deg"], truth["board_uptime_ms"] = [], []
            return b"C,15,37.5,24,10\n", truth
        with self.assertRaisesRegex(ValueError, "No E frames"):
            load_case(self.case(rewrite=empty))

    def test_unavailable_no_invented_estimate(self):
        for config in (Simulation(duration_sec=4, motion_amplitude=0),
                       Simulation(duration_sec=4, motion_amplitude=2, noise_std=40)):
            result = load_case(self.case(config))
            self.assertEqual(result["summary"]["available_windows"], 0)
            for window in result["windows"]:
                self.assertIsNone(window["position_cm"])
                self.assertIsNone(window["estimated_angle_deg"])
                self.assertIsNotNone(window["unavailable_reason"])
            self.assertIn("推定できません", build_html(result))

    def test_rear_ambiguity_retains_wrong_position_and_error(self):
        result = load_case(self.case(Simulation(duration_sec=4, angle_deg=150)))
        w = result["windows"][0]
        self.assertTrue(w["estimation_available"])
        self.assertEqual(w["estimated_angle_deg"], 30)
        self.assertEqual(w["absolute_angular_error_deg"], 120)
        self.assertNotEqual(w["truth_position_cm"], w["position_cm"])
        parser = Scripts()
        parser.feed(build_html(result))
        self.assertEqual(json.loads(parser.payload)["windows"], result["windows"])

    def test_gap_windows_stay_separate(self):
        def gap(csv, truth):
            lines = csv.splitlines(keepends=True)
            lines.pop(101)  # C row + frame 100; preserve original sequence numbers.
            truth["angle_deg"].pop(100)
            truth["board_uptime_ms"].pop(100)
            return b"".join(lines), truth
        result = load_case(self.case(Simulation(duration_sec=12), rewrite=gap))
        self.assertGreater(len({w["trace_index"] for w in result["windows"]}), 1)
        for w in result["windows"]:
            self.assertTrue(w["frame_row_end_exclusive"] <= 100 or w["frame_row_start"] >= 100)
        html = build_html(result)
        self.assertNotIn("polyline", html)
        self.assertNotIn("setInterval", html)

    def test_escaping_and_self_contained_html(self):
        result = load_case(self.case())
        attack = '</script><img src="https://invalid.example" onerror="alert(1)">&\u2028\u2029'
        result["simulation"]["limitations"] = attack
        html = build_html(result)
        parser = Scripts()
        parser.feed(html)
        self.assertEqual(json.loads(parser.payload)["simulation"]["limitations"], attack)
        self.assertEqual(len([t for t, _ in parser.tags if t == "script"]), 2)
        self.assertFalse(any(t in ("img", "iframe", "link") for t, _ in parser.tags))
        self.assertFalse(any("src" in a for _, a in parser.tags))
        self.assertIn("\\u003c/script\\u003e", parser.payload)
        self.assertNotIn("fetch(", html)
        self.assertNotIn("innerHTML", html)
        self.assertIn("connect-src 'none'", html)

    def test_reset_and_short_segments_are_not_joined(self):
        def reset(csv, truth):
            lines = csv.decode().splitlines()
            for index in range(20, len(truth["board_uptime_ms"])):
                fields = lines[index+1].split(",")
                fields[1] = str(index-20)
                fields[2] = str(int(fields[2])-1000)
                lines[index+1] = ",".join(fields)
                truth["board_uptime_ms"][index] -= 1000
            return ("\n".join(lines)+"\n").encode(), truth
        result = load_case(self.case(Simulation(duration_sec=4), rewrite=reset))
        self.assertEqual(len(result["windows"]), 2)
        self.assertEqual(result["summary"]["available_windows"], 0)
        self.assertEqual({w["parent_block_index"] for w in result["windows"]}, {1, 2})
        self.assertEqual([w["observed_samples"] for w in result["windows"]], [20, 60])

    def test_determinism_output_and_source_preservation(self):
        case = self.case(saved=True)
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in case.rglob("*") if p.is_file()}
        a = render_explorer(case, self.root / "one.html")
        b = render_explorer(case, self.root / "two.html")
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before})
        with self.assertRaises(FileExistsError):
            render_explorer(case, a)
        self.assertEqual(a.read_bytes(), b.read_bytes())

    def test_paths_and_malformed_json(self):
        with self.assertRaisesRegex(ValueError, "case directory"):
            load_case(self.root / "missing")
        with self.assertRaisesRegex(ValueError, "truth.json"):
            load_case(self.root)
        case = self.case()
        for payload in (b'{"synthetic":true,"synthetic":false}', b'{"value":NaN}', b"not json"):
            (case / "truth.json").write_bytes(payload)
            with self.assertRaises(ValueError):
                load_case(case)
        case = self.case()
        for output in (self.root / "bad.json", case / "canonical/output.html", self.root / "missing/out.html",
                       Path(tempfile.gettempdir()) / "not-ignored-explorer.html"):
            with self.assertRaises(ValueError):
                render_explorer(case, output)

    def test_cli_success_missing_data_and_no_overwrite(self):
        case = self.case()
        output = self.root / "cli.html"
        command = [sys.executable, "-m", "ml.analysis.spatial_explorer", str(case), "--output", str(output)]
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Saved offline synthetic Explorer", result.stdout)
        for bad in (command, command[:3] + [str(self.root / "missing")] + command[4:-1] + [str(self.root / "new.html")]):
            result = subprocess.run(bad, capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertIn("error:", result.stderr)

    def test_linked_canonical_directory_stays_read_only(self):
        case = self.case()
        alias = self.root / "alias"
        alias.mkdir()
        try:
            (alias / "canonical").symlink_to(case / "canonical", target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"Directory symlinks unavailable on this system: {exc}")
        (alias / "truth.json").write_bytes((case / "truth.json").read_bytes())
        output = case / "canonical" / "forbidden.html"
        with self.assertRaisesRegex(ValueError, "outside the canonical"):
            render_explorer(alias, output)
        self.assertFalse(output.exists())

    def test_playback_fixture_preserves_gap_and_restart_identities(self):
        result = load_case(write_discontinuous_case(self.root / "boundaries"))
        windows = result["windows"]
        self.assertEqual(len(windows), 8)
        self.assertEqual([(w["parent_block_index"], w["trace_index"]) for w in windows],
                         [(1, 1)]*3 + [(1, 2)]*2 + [(2, 1)]*3)
        self.assertLess(windows[5]["reference_board_uptime_ms"], windows[4]["reference_board_uptime_ms"])
        for w in windows:
            times = result["simulation"]["board_uptime_ms"][w["frame_row_start"]:w["frame_row_end_exclusive"]]
            self.assertEqual(len(times), w["observed_samples"])
            self.assertTrue(all(b>a for a,b in zip(times,times[1:])))


@unittest.skipUnless(shutil.which("node"), "Optional Node.js is needed to execute the embedded playback controller")
class SpatialPlaybackTests(unittest.TestCase):
    """Execute the exact shipped JS using a deterministic, single-queue clock."""

    def run_js(self, script):
        fixture = """
const assert=require('node:assert/strict');
const windows=Array.from({length:5},(_,i)=>({parent_block_index:1,trace_index:i<3?1:2,
 reference_board_uptime_ms:100000+i*1000,source_line_start:2+i*20,source_line_end:81+i*20}));
let serial=0;const pending=new Map(),cancelled=new Map(),updates=[];
const options={setTimeout:(fn,ms)=>{assert.equal(ms,1000);pending.set(++serial,fn);return serial;},
 clearTimeout:id=>{cancelled.set(id,pending.get(id));pending.delete(id);}};
const player=createPlayback(windows,s=>updates.push(s),options);
function tick(){assert.equal(pending.size,1);const [id,fn]=pending.entries().next().value;pending.delete(id);fn();}
"""
        run = subprocess.run([shutil.which("node"), "-e", _PLAYBACK_JS+fixture+script],
                             capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_play_pause_and_no_initial_autoplay(self):
        self.run_js("""
assert.deepEqual(player.snapshot(),{selected:0,playing:false,stopReason:'initial'});
assert.equal(pending.size,0);player.play();player.play();assert.equal(pending.size,1);
tick();assert.equal(player.snapshot().selected,1);assert.equal(player.snapshot().playing,true);
player.pause();assert.equal(player.snapshot().playing,false);assert.equal(pending.size,0);
assert.deepEqual(updates.at(-1),player.snapshot());
""")

    def test_previous_next_manual_selection_pauses(self):
        self.run_js("""
player.previous();assert.equal(player.snapshot().selected,0);
player.play();player.next();assert.equal(player.snapshot().selected,1);
assert.equal(player.snapshot().playing,false);assert.equal(pending.size,0);
player.previous();assert.equal(player.snapshot().selected,0);
player.select(4);player.next();assert.equal(player.snapshot().selected,4);
""")

    def test_timeline_and_marker_share_one_selected_state(self):
        self.run_js("""
player.play();player.select(3);assert.equal(pending.size,0);
assert.deepEqual(updates.at(-1),{selected:3,playing:false,stopReason:'manual'});
for(const invalid of [-1,5,NaN,1.5,'1'])player.select(invalid);
assert.equal(player.snapshot().selected,3);player.select(1);
assert.deepEqual(updates.at(-1),player.snapshot());
""")

    def test_end_stops_without_loop_or_extra_timer(self):
        self.run_js("""
player.select(3);player.play();tick();
assert.deepEqual(player.snapshot(),{selected:4,playing:false,stopReason:'end'});
assert.equal(pending.size,0);player.play();assert.equal(pending.size,0);
assert.equal(player.snapshot().selected,4);
""")

    def test_boundary_stops_and_requires_explicit_navigation(self):
        self.run_js("""
player.play();tick();tick();
assert.deepEqual(player.snapshot(),{selected:2,playing:false,stopReason:'boundary'});
assert.equal(pending.size,0);player.play();assert.equal(pending.size,0);
player.next();assert.equal(player.snapshot().selected,3);
player.play();tick();assert.equal(player.snapshot().selected,4);
""")

    def test_unreliable_metadata_disables_auto_progression(self):
        self.run_js("""
for(const change of [{trace_index:2},{parent_block_index:2},{trace_index:null},
 {reference_board_uptime_ms:100000},{reference_board_uptime_ms:NaN},{source_line_start:2}]){
 const pair=[windows[0],{...windows[1],...change}];
 const p=createPlayback(pair,()=>{},options);p.play();
 assert.equal(p.snapshot().playing,false);assert.equal(p.snapshot().stopReason,'boundary');
 assert.equal(pending.size,0);p.next();assert.equal(p.snapshot().selected,1);
}
""")

    def test_rapid_play_pause_cancels_stale_callbacks(self):
        self.run_js("""
for(let i=0;i<50;i++){player.play();player.play();assert.equal(pending.size,1);player.pause();assert.equal(pending.size,0);}
player.play();for(const fn of cancelled.values())if(fn)fn();
assert.equal(player.snapshot().selected,0);assert.equal(pending.size,1);
tick();assert.equal(player.snapshot().selected,1);player.pause();assert.equal(pending.size,0);
""")

    def test_hidden_and_reduced_motion_block_and_clear_playback(self):
        self.run_js("""
let reason=null;const p=createPlayback(windows,()=>{},{...options,blockedReason:()=>reason});
p.play();reason='hidden';tick();assert.equal(p.snapshot().selected,0);
assert.equal(p.snapshot().stopReason,'hidden');assert.equal(pending.size,0);
reason='reduced';p.play();assert.equal(p.snapshot().playing,false);
assert.equal(p.snapshot().stopReason,'reduced');p.next();assert.equal(p.snapshot().selected,1);
reason=null;p.play();assert.equal(pending.size,1);p.pause('hidden');assert.equal(pending.size,0);
""")


if __name__ == "__main__":
    unittest.main()
