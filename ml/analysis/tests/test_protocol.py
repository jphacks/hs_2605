from pathlib import Path
import unittest
import tempfile

import numpy as np

from ml.analysis.protocol import Config, Data, OtherMessage, parse_line, parse_capture, read_capture
from ml.features import read_csv
from pc.ibuki.serial_reader import parse_line as live_parse_line


class ProtocolTests(unittest.TestCase):
    def test_valid_messages_and_live_equivalence(self):
        config = parse_line("C,15,37.5,2,10\r\n")
        data = parse_line("D,7,1234,10,-20,30,40\r\n")
        self.assertEqual(config, Config(15, 37.5, 2, 10))
        self.assertEqual(data, Data(7, 1234, (10, 30), (-20, 40)))
        self.assertEqual(config.ranges_cm(), (15, 18.75))
        self.assertEqual(data.z(config), (1-2j, 3+4j))
        np.testing.assert_array_equal(data.z(config), live_parse_line(
            "D,7,1234,10,-20,30,40").z(live_parse_line("C,15,37.5,2,10")))

    def test_malformed(self):
        for row in ("", "D", "D,1,2", "D,1,2,3", "D,1,2,3,4,5",
                    "D,x,2,3,4", "D,1,NaN,3,4", "D,1,2,3.5,4",
                    "D,1,2,3,no", "D,-1,2,3,4", "D,1,-2,3,4",
                    "C,1,2", "C,1,2,3,4,5", "C,x,2,3,4", "C,1,2,x,4",
                    "C,1,2,3,0", "C,nan,2,3,4", "C,1,inf,3,4",
                    "C,1,2,3,-4", "C,-1,2,3,4", "C,1,0,3,4", "C,1,2,0,4"):
            with self.subTest(row=row), self.assertRaises(ValueError):
                parse_line(row)

    def test_raw_integers_are_lossless(self):
        value = 2**60 + 1
        data = parse_line(f"D,1,2,{value},-{value}")
        self.assertEqual(data.i, (value,))
        self.assertEqual(data.q, (-value,))

    def test_late_and_changing_config(self):
        capture = parse_capture(["D,1,2,10,20", "C,15,37.5,1,10",
                                 "D,2,3,30,40", "C,20,10,1,2", "D,3,4,6,8"])
        self.assertFalse(capture.issues)
        self.assertEqual([f.config_line_number for f in capture.frames], [2, 2, 4])
        self.assertEqual([f.z() for f in capture.frames], [(1+2j,), (3+4j,), (3+4j,)])

    def test_missing_config(self):
        frame = parse_capture(["D,1,2,10,20"]).frames[0]
        self.assertIsNone(frame.config)
        self.assertIsNone(frame.config_line_number)
        self.assertEqual(frame.data.i, (10,))
        with self.assertRaisesRegex(ValueError, "unknown"):
            frame.z()

    def test_mismatched_config_and_error_rows(self):
        capture = parse_capture(["D,1,2,1,2", "C,15,37.5,2,10",
                                 "D,x,2,1,2", "D,2,3,1", "", "C,1,2,3,0",
                                 "D,3,4,1,2,3,4"])
        self.assertEqual([issue.line_number for issue in capture.issues], [1, 3, 4, 5, 6])
        self.assertTrue(all(issue.reason for issue in capture.issues))
        self.assertEqual(capture.issues[1].raw, "D,x,2,1,2")
        self.assertEqual(len(capture.frames), 2)
        with self.assertRaisesRegex(ValueError, "expects 2"):
            capture.frames[0].z()
        self.assertEqual(capture.frames[1].z(), (.1+.2j, .3+.4j))

    def test_empty_and_other_messages(self):
        self.assertEqual(parse_capture([]).frames, ())
        self.assertEqual(parse_capture([]).issues, ())
        rows = ["E,1,2,not-decoded", "Y,1,state,-", "H,1,20,40", "S,1,text,comma"]
        capture = parse_capture(rows)
        self.assertEqual([m.raw for _, m in capture.other_messages], rows)
        self.assertIsInstance(parse_line(rows[0]), OtherMessage)
        self.assertFalse(capture.issues)

    def test_file_input_and_invalid_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "capture.csv"
            path.write_text("", encoding="utf-8")
            self.assertEqual(read_capture(path).frames, ())
            path.write_text("\ufeffC,15,37.5,1,10\nC,1,2,1,0\nD,1,2,10,20\n",
                            encoding="utf-8")
            capture = read_capture(path)
            self.assertEqual(capture.frames[0].config_line_number, 1)
            self.assertEqual(capture.frames[0].z(), (1+2j,))
            self.assertEqual(capture.issues[0].line_number, 2)
            with self.assertRaises(FileNotFoundError):
                read_capture(Path(directory) / "missing.csv")

    def test_public_samples_match_features(self):
        root = Path(__file__).resolve().parents[3]
        for name in ("breathing", "hold", "leave"):
            with self.subTest(name=name):
                path = root / "recordings" / "samples" / f"{name}.sample.csv"
                capture = read_capture(path)
                t, z = read_csv(path)
                self.assertGreater(len(capture.frames), 0)
                self.assertFalse(capture.issues)
                self.assertGreater(capture.frames[0].config_line_number, 1)
                np.testing.assert_array_equal([f.data.t_ms for f in capture.frames], t)
                np.testing.assert_array_equal([f.z() for f in capture.frames], z)
                self.assertEqual(len(capture.frames), sum(
                    row.startswith("D,") for row in path.read_text().splitlines()))


if __name__ == "__main__":
    unittest.main()
