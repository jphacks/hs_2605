import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from ml.analysis.ingest import build_capture_dataset, ingest_recording
from ml.analysis.protocol import parse_e_line, read_capture
from ml.analysis.quality import analyze_quality
from ml.analysis.schema import DATASET_FILES, SCHEMA_VERSION, frame_schema, require_arrow
from ml.antennas import read_e
from ml.features import read_csv

pa, pq = require_arrow()
ROOT = Path(__file__).resolve().parents[3]


def e_line(seq=1, t_ms=100):
    values = np.arange(144, dtype=np.int16)-72
    values[0], values[1] = -32768, 32767
    return f"E,{seq},{t_ms}," + base64.b64encode(values.astype("<i2").tobytes()).decode(), values


def load(directory):
    return (json.loads((directory/"manifest.json").read_text()), pq.read_table(directory/"frames.parquet").to_pylist(),
            [json.loads(line) for line in (directory/"events.jsonl").read_text().splitlines()],
            json.loads((directory/"quality.json").read_text()))


class IngestTests(unittest.TestCase):
    def test_public_samples_and_existing_numeric_quality_equivalence(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, count in (("breathing", 999), ("hold", 1000), ("leave", 996)):
                with self.subTest(name=name):
                    source = ROOT / "recordings/samples" / f"{name}.sample.csv"
                    original, modified = source.read_bytes(), source.stat().st_mtime_ns
                    out = Path(directory)/name
                    ingest_recording(source, out)
                    manifest, rows, events, quality = load(out)
                    self.assertEqual(set(p.name for p in out.iterdir()), set(DATASET_FILES))
                    self.assertEqual(manifest["schema_version"], SCHEMA_VERSION)
                    self.assertEqual(manifest["input"]["sha256"], hashlib.sha256(original).hexdigest())
                    self.assertEqual(manifest["counts"]["d_frames"], count)
                    self.assertEqual(manifest["counts"]["events"], 12)
                    self.assertEqual(manifest["counts"]["diagnostics"], 0)
                    self.assertEqual(manifest["scenario"], {"name": None, "provenance": "unspecified", "is_ground_truth": False})
                    self.assertEqual(manifest["source_type"], "unknown")
                    times, expected = read_csv(source)
                    values = np.array([np.array(row["raw_i"])[0]/row["value_divisor"] +
                                       1j*np.array(row["raw_q"])[0]/row["value_divisor"] for row in rows])
                    np.testing.assert_array_equal([row["board_uptime_ms"] for row in rows], times)
                    np.testing.assert_allclose(values, expected, rtol=1e-15, atol=1e-14)
                    self.assertEqual(quality["d_quality"], analyze_quality(read_capture(source)).to_dict())
                    self.assertEqual(quality["frame_quality"]["total_frames"], count)
                    self.assertTrue(all(event["event_type"] == "configuration" for event in events))
                    self.assertEqual(source.read_bytes(), original)
                    self.assertEqual(source.stat().st_mtime_ns, modified)

    def test_delayed_missing_and_changing_config(self):
        data = b"D,1,100,10,20,30,40\nC,20,50,2,2\nD,2,160,4,6,8,10\nC,30,25,2,10\nD,3,220,2,4,6,8\n"
        rows, _, quality, manifest = build_capture_dataset(data, source_name="capture.csv")
        self.assertEqual([r["config_source_line"] for r in rows], [2, 2, 4])
        self.assertEqual([r["value_divisor"] for r in rows], [2, 2, 10])
        self.assertEqual([r["first_cm"] for r in rows], [20, 20, 30])
        self.assertEqual(manifest["counts"]["diagnostics"], 0)
        missing = build_capture_dataset(b"D,1,100,10,20\n", source_name="capture.csv")
        row = missing[0][0]
        for key in ("first_cm", "step_mm", "config_bin_count", "config_scale", "config_source_line", "value_divisor"):
            self.assertIsNone(row[key])
        self.assertFalse(row["scaled_values_available"])
        self.assertEqual(row["raw_i"], ((10,),))
        self.assertEqual(missing[2]["frame_quality"]["frames_without_usable_configuration"], 1)

    def test_e_order_units_delayed_config_and_existing_readers(self):
        line, values = e_line()
        decoded = parse_e_line(line)
        for a in range(3):
            for k in range(24):
                self.assertEqual(decoded.i[a][k], int(values[a*48+k*2]))
                self.assertEqual(decoded.q[a][k], int(values[a*48+k*2+1]))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/"e.csv"
            source.write_text(line+"\nC,15,37.5,24,10\n")
            out = Path(directory)/"out"
            ingest_recording(source, out)
            manifest, rows, _, quality = load(out)
            row = rows[0]
            self.assertEqual((row["antenna_count"], row["bin_count"], row["value_divisor"], row["config_scale"]), (3,24,1,10))
            self.assertEqual(row["config_source_line"], 2)
            actual = np.array(row["raw_i"]) + 1j*np.array(row["raw_q"])
            times, antennas = read_e(source)
            np.testing.assert_array_equal(actual, antennas[0])
            np.testing.assert_array_equal(actual[0], read_csv(source)[1][0])
            self.assertEqual(manifest["counts"]["e_frames"], 1)
            self.assertEqual(quality["frame_quality"]["total_frames"], 1)
        without_c = build_capture_dataset((line+"\n").encode(), source_name="e.csv")[0][0]
        self.assertIsNone(without_c["first_cm"])
        self.assertIsNone(without_c["config_scale"])
        self.assertEqual(without_c["value_divisor"], 1)
        self.assertTrue(without_c["scaled_values_available"])

    def test_malformed_d_c_e_and_blank_rows_are_auditable(self):
        line, _ = e_line()
        fragments = [b"", b"D,x,1,1,2", b"D,1,1,1", b"C,15,37.5,24,0", b"E,1,1,%%%%",
                     b"E,1,1,AA==", line.replace("E,1,", "E,x,", 1).encode(), b"D,x,1,\xff,2"]
        data = b"\n".join(fragments)+b"\n"
        rows, events, quality, manifest = build_capture_dataset(data, source_name="bad.csv")
        self.assertEqual(rows, [])
        self.assertEqual(quality["diagnostic_count"], 8)
        self.assertEqual([d["source_line"] for d in quality["diagnostics"]], list(range(1,9)))
        self.assertEqual(manifest["counts"]["source_lines"], 8)
        for k, event in enumerate(events):
            self.assertEqual(base64.b64decode(event["raw_bytes_base64"]), fragments[k])
            self.assertTrue(event["detail"]["reason"])
        for invalid in ("E,1,2", "E,-1,2,AA==", "E,1,no,AA==", "E,1,2,π"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_e_line(invalid)

    def test_bin_mismatch_preserves_raw_frames(self):
        line, _ = e_line(2, 60)
        rows, events, quality, _ = build_capture_dataset(
            ("C,15,37.5,3,10\nD,1,0,10,20\n"+line+"\n").encode(), source_name="mismatch.csv")
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["configuration_status"] for r in rows], ["bin_mismatch", "bin_mismatch"])
        self.assertFalse(rows[0]["scaled_values_available"])
        self.assertTrue(rows[1]["scaled_values_available"])
        self.assertEqual(quality["frame_quality"]["bin_count_mismatch_frames"], 2)
        self.assertEqual([e["source_line"] for e in events if e["origin"] == "ingestion_diagnostic"], [2,3])

    def test_event_payloads_and_explicit_vs_inferred_restarts(self):
        lines = ["C,15,37.5,1,10", "D,1,0,1,2", " S,40,radar restart: fifo overflow 0x0  ",
                 "D,2,60,1,2", "D,4,240,1,2", "D,5,300,1,2", "D,6,360,1,2", "D,7,600,1,2",
                 "D,7,600,1,2", "D,0,0,1,2", "Y,1,stopped,no_breath", "H,1,20,30", "B,1",
                 " S,1,no radar restart required ", "unknown,keep,commas,  "]
        _, events, quality, manifest = build_capture_dataset(("\r\n".join(lines)+"\r\n").encode(), source_name="hold.csv")
        explicit = [e for e in events if e["origin"] == "explicit_board_status"]
        self.assertEqual([e["source_line"] for e in explicit], [3])
        self.assertEqual(explicit[0]["raw"], lines[2])
        inferred = [e for e in events if e["origin"] == "inferred_from_frames"]
        self.assertEqual({e["event_type"] for e in inferred}, {"sequence_gap", "long_interval", "duplicate_sequence",
                                                                "backward_sequence", "repeated_timestamp", "backward_timestamp"})
        self.assertEqual(quality["frame_quality"]["estimated_missing_frames"], 1)
        self.assertEqual(quality["frame_quality"]["suspected_reset_events"], 1)
        for number in (11,12,13,14,15):
            self.assertEqual(next(e["raw"] for e in events if e["source_line"] == number), lines[number-1])
        self.assertIn("Y is not ground truth", manifest["events_semantics"])
        self.assertIsNone(manifest["scenario"]["name"])

    def test_mixed_stream_sequence_quality(self):
        line, _ = e_line(2, 60)
        d = ",".join(["1", "2"]*24)
        rows, _, quality, _ = build_capture_dataset(
            f"C,15,37.5,24,10\nD,1,0,{d}\n{line}\nD,3,120,{d}\n".encode(), source_name="mixed.csv")
        self.assertEqual([r["message_type"] for r in rows], ["D", "E", "D"])
        self.assertEqual(quality["frame_quality"]["estimated_missing_frames"], 0)
        self.assertEqual(quality["d_quality"]["estimated_missing_frames"], 1)

    def test_int64_precision_and_overflow_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/"large.csv"
            source.write_text(f"D,1,0,{2**60+1},-{2**60+1}\nD,2,60,{2**63},0\n")
            out = Path(directory)/"out"
            ingest_recording(source, out)
            _, rows, events, quality = load(out)
            self.assertEqual(rows[0]["raw_i"], [[2**60+1]])
            self.assertEqual(rows[0]["raw_q"], [[-(2**60+1)]])
            self.assertEqual(len(rows), 1)
            self.assertEqual(quality["diagnostics"][0]["event_type"], "schema_range")
            self.assertIn(str(2**63), next(e["raw"] for e in events if e["event_type"] == "schema_range"))

    def test_empty_schema_and_byte_determinism(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/"empty.csv"
            source.write_bytes(b"")
            a, b = Path(directory)/"a", Path(directory)/"b"
            ingest_recording(source, a)
            ingest_recording(source, b)
            self.assertEqual(pq.read_table(a/"frames.parquet").schema, frame_schema())
            self.assertEqual(pq.read_table(a/"frames.parquet").num_rows, 0)
            self.assertEqual((a/"events.jsonl").read_bytes(), b"")
            for name in DATASET_FILES:
                self.assertEqual((a/name).read_bytes(), (b/name).read_bytes())
            data = b"\xef\xbb\xbfS,0,hello\r\nD,1,100,10,20\r\nC,15,37.5,1,10\r\nY,2,empty,-\r\n"
            source.write_bytes(data)
            settings = {"session_id": "declared-session", "source_type": "recorded", "scenario": "example",
                        "scenario_provenance": "operator log, not truth"}
            ingest_recording(source, a, force=True, **settings)
            ingest_recording(source, b, force=True, **settings)
            manifest, rows, events, _ = load(a)
            self.assertEqual(rows[0]["session_id"], "declared-session")
            self.assertEqual(manifest["source_type_provenance"], "operator_declared")
            self.assertFalse(manifest["scenario"]["is_ground_truth"])
            self.assertEqual(base64.b64decode(events[0]["raw_bytes_base64"]), b"\xef\xbb\xbfS,0,hello")
            self.assertEqual(manifest["input"]["sha256"], hashlib.sha256(data).hexdigest())
            for name in DATASET_FILES:
                self.assertEqual((a/name).read_bytes(), (b/name).read_bytes())
                if name != "manifest.json":
                    self.assertEqual(manifest["artifacts"][name]["sha256"], hashlib.sha256((a/name).read_bytes()).hexdigest())

    def test_force_protects_original_and_unrelated_paths_and_write_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/"capture.csv"
            source.write_text("D,1,0,1,2\n")
            out = root/"out"
            ingest_recording(source, out)
            original = {name: (out/name).read_bytes() for name in DATASET_FILES}
            with self.assertRaises(FileExistsError):
                ingest_recording(source, out)
            with patch.object(pq, "write_table", side_effect=OSError("simulated write failure")):
                with self.assertRaises(OSError):
                    ingest_recording(source, out, force=True)
            self.assertEqual(original, {name: (out/name).read_bytes() for name in DATASET_FILES})
            self.assertEqual(sorted(p.name for p in root.iterdir()), ["capture.csv", "out"])
            with self.assertRaisesRegex(ValueError, "original recording"):
                ingest_recording(source, root, force=True)
            (out/"notes.txt").write_text("keep")
            with self.assertRaisesRegex(ValueError, "unrelated files"):
                ingest_recording(source, out, force=True)
            self.assertEqual((out/"notes.txt").read_text(), "keep")
            with self.assertRaises(ValueError):
                ingest_recording(source, source, force=True)

    def test_cli_and_missing_offline_dependency(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, out = root/"capture.csv", root/"out"
            source.write_text("D,1,0,1,2\n")
            command = [sys.executable, "-m", "ml.analysis.ingest", str(source), "--out-dir", str(out)]
            missing = subprocess.run([sys.executable, "-S", *command[1:]], capture_output=True, text=True)
            self.assertEqual(missing.returncode, 2)
            self.assertIn("ml/analysis/requirements.txt", missing.stderr)
            self.assertFalse(out.exists())
            success = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertIn(str(out), success.stdout)
            denied = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(denied.returncode, 2)
            forced = subprocess.run(command+["--force"], capture_output=True, text=True)
            self.assertEqual(forced.returncode, 0, forced.stderr)
            original = {name: (out/name).read_bytes() for name in DATASET_FILES}
            source.unlink()
            failed = subprocess.run(command+["--force"], capture_output=True, text=True)
            self.assertEqual(failed.returncode, 2)
            self.assertEqual(original, {name: (out/name).read_bytes() for name in DATASET_FILES})

    def test_invalid_paths_metadata_and_existing_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/"capture.csv"
            source.write_text("D,1,0,1,2\n")
            with self.assertRaises(ValueError):
                ingest_recording(root, root/"out")
            with self.assertRaises(ValueError):
                ingest_recording(source, root/"out", scenario_provenance="no scenario")
            self.assertFalse((root/"out").exists())
            ingest_recording(source, root/"out")
            (root/"out/manifest.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "recognized"):
                ingest_recording(source, root/"out", force=True)
            self.assertEqual((root/"out/manifest.json").read_text(), "[]")


if __name__ == "__main__":
    unittest.main()
