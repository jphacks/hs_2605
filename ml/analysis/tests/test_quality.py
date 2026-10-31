import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ml.analysis.protocol import Capture, CaptureIssue, parse_capture, read_capture
from ml.analysis.quality import analyze_quality


def report_for(sequences, times=None):
    if times is None:
        times = [k * 60 for k in range(len(sequences))]
    return analyze_quality(parse_capture(
        ["C,15,37.5,1,10"] + [f"D,{s},{t},10,20" for s, t in zip(sequences, times)]))


class QualityTests(unittest.TestCase):
    def test_continuous(self):
        report = report_for([1, 2, 3, 4, 5])
        self.assertEqual(report.total_d_frames, 5)
        self.assertEqual(report.estimated_missing_frames, 0)
        self.assertEqual(report.typical_sampling_interval_ms, 60)
        self.assertEqual(report.long_interval_threshold_ms, 180)
        self.assertEqual(report.long_interval_events, 0)
        self.assertEqual(report.frames_without_usable_configuration, 0)

    def test_missing_frames(self):
        for seq, missing, events in [([1, 3], 1, 1), ([1, 4, 8], 5, 2)]:
            with self.subTest(seq=seq):
                report = report_for(seq)
                self.assertEqual(report.estimated_missing_frames, missing)
                self.assertEqual(report.sequence_gap_events, events)

    def test_duplicates_and_backward_sequence(self):
        report = report_for([5, 5, 2, 3])
        self.assertEqual(report.duplicate_sequence_events, 1)
        self.assertEqual(report.backward_sequence_events, 1)
        self.assertEqual(report.suspected_reset_events, 1)
        self.assertEqual(report.estimated_missing_frames, 0)

    def test_time_repetition_and_reset(self):
        report = report_for([10, 11, 20, 21], [100, 100, 0, 60])
        self.assertEqual(report.repeated_timestamp_events, 1)
        self.assertEqual(report.backward_timestamp_events, 1)
        self.assertEqual(report.suspected_reset_events, 1)
        self.assertEqual(report.estimated_missing_frames, 0)
        self.assertIsNone(report.typical_sampling_interval_ms)
        self.assertIsNone(report.long_interval_events)
        both = report_for([10, 1], [100, 0])
        self.assertEqual(both.suspected_reset_events, 1)

    def test_long_intervals_and_strict_threshold(self):
        report = report_for([1, 2, 3, 4, 5, 6], [0, 60, 120, 180, 360, 601])
        self.assertEqual(report.typical_sampling_interval_ms, 60)
        self.assertEqual(report.long_interval_events, 1)
        # A seq gap is excluded from the reference but can be a long interval.
        gap = report_for([1, 2, 3, 4, 8], [0, 60, 120, 180, 500])
        self.assertEqual(gap.timing_reference_interval_count, 3)
        self.assertEqual(gap.long_interval_events, 1)

    def test_reset_boundary_not_in_timing(self):
        report = report_for([10, 11, 1, 2, 3], [0, 60, 100000, 100060, 100120])
        self.assertEqual(report.typical_sampling_interval_ms, 60)
        self.assertEqual(report.long_interval_events, 0)

    def test_issues_and_distinct_lines(self):
        capture = parse_capture(["D,x,0,1,2", "", "C,1,2,1,0", "D,1,0,1,2"])
        report = analyze_quality(capture)
        self.assertEqual(report.parser_issue_count, 3)
        self.assertEqual(report.issue_source_line_count, 3)
        self.assertEqual(report.total_d_frames, 1)
        issues = (CaptureIssue(1, "a", ""), CaptureIssue(1, "b", ""))
        report = analyze_quality(Capture((), (), (), issues))
        self.assertEqual(report.parser_issue_count, 2)
        self.assertEqual(report.issue_source_line_count, 1)

    def test_missing_mismatched_and_late_config(self):
        missing = analyze_quality(parse_capture(["D,1,0,1,2"]))
        self.assertEqual(missing.frames_without_configuration, 1)
        self.assertEqual(missing.frames_without_usable_configuration, 1)
        mismatch = analyze_quality(parse_capture(["D,1,0,1,2", "C,15,37.5,2,10"]))
        self.assertEqual(mismatch.bin_count_mismatch_frames, 1)
        self.assertEqual(mismatch.frames_without_usable_configuration, 1)
        self.assertEqual(mismatch.frames_without_configuration, 0)
        late = analyze_quality(parse_capture(["D,1,0,1,2", "C,15,37.5,1,10"]))
        self.assertEqual(late.frames_without_usable_configuration, 0)

    def test_empty_insufficient_and_e_only(self):
        for sequences in ([], [1], [1, 2, 3]):
            report = report_for(sequences)
            self.assertIsNone(report.typical_sampling_interval_ms)
            self.assertIsNone(report.long_interval_threshold_ms)
            self.assertIsNone(report.long_interval_events)
        report = analyze_quality(parse_capture(["E,1,0,payload", "E,2,60,payload",
                                               "Y,0,empty,-", "H,0,20,30", "S,0,start"]))
        self.assertEqual(report.total_d_frames, 0)
        self.assertEqual(report.unparsed_message_kind_counts, {"E": 2, "Y": 1, "H": 1, "S": 1})
        self.assertIsNone(report.typical_sampling_interval_ms)

    def test_json_and_detached_dictionary(self):
        report = analyze_quality(parse_capture(["E,1,0,payload"]))
        result = report.to_dict()
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)
        result["unparsed_message_kind_counts"]["E"] = 9
        self.assertEqual(report.unparsed_message_kind_counts["E"], 1)

    def test_public_samples(self):
        root = Path(__file__).resolve().parents[3]
        for name, count in (("breathing", 999), ("hold", 1000), ("leave", 996)):
            with self.subTest(name=name):
                capture = read_capture(root / "recordings/samples" / f"{name}.sample.csv")
                report = analyze_quality(capture)
                self.assertEqual(report.total_d_frames, count)
                self.assertEqual(report.parser_issue_count, 0)
                self.assertEqual(report.estimated_missing_frames, 0)
                self.assertEqual(report.sequence_gap_events, 0)
                self.assertEqual(report.duplicate_sequence_events, 0)
                self.assertEqual(report.backward_sequence_events, 0)
                self.assertEqual(report.repeated_timestamp_events, 0)
                self.assertEqual(report.backward_timestamp_events, 0)
                self.assertEqual(report.typical_sampling_interval_ms, 60)
                self.assertEqual(report.long_interval_events, 0)
                self.assertEqual(report.frames_without_usable_configuration, 0)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "empty.csv"
            path.write_text("")
            command = [sys.executable, "-m", "ml.analysis.quality", str(path)]
            text = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(text.returncode, 0, text.stderr)
            self.assertIn("No parsed D frames", text.stdout)
            outputs = [subprocess.run(command + ["--json"], capture_output=True, text=True)
                       for _ in range(2)]
            self.assertTrue(all(p.returncode == 0 and not p.stderr for p in outputs))
            self.assertEqual(outputs[0].stdout, outputs[1].stdout)
            self.assertIsNone(json.loads(outputs[0].stdout)["typical_sampling_interval_ms"])
            absent = subprocess.run(command[:-1] + [str(path) + ".missing", "--json"],
                                    capture_output=True, text=True)
            self.assertEqual(absent.returncode, 2)
            self.assertEqual(absent.stdout, "")
            self.assertIn("cannot read", absent.stderr)
            self.assertEqual(list(Path(directory).iterdir()), [path])


if __name__ == "__main__":
    unittest.main()
