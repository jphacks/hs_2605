"""Run from the repo root:  ../env/bin/python -m unittest discover -s pc/tests -t pc"""
import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402
import serial.tools.list_ports  # noqa: E402,F401  (loaded so the Windows test can patch comports on any OS)

from ibuki.serial_reader import (Button, Config, Data, FileReplay, SeqTracker,  # noqa: E402
                                 Status, find_port, parse_line)


class WindowsPortTest(unittest.TestCase):
    def test_only_one_kitprog_is_selected_and_unrelated_com_ports_are_ignored(self):
        kit = SimpleNamespace(device="COM7", description="KitProg3 USB-UART", product=None)
        unrelated = SimpleNamespace(device="COM2", description="Bluetooth Serial", product=None)
        with patch("ibuki.serial_reader.sys.platform", "win32"), patch("serial.tools.list_ports.comports", return_value=[unrelated, kit]):
            self.assertEqual(find_port(), "COM7")
        for ports in [[], [unrelated], [kit, kit]]:
            with patch("ibuki.serial_reader.sys.platform", "win32"), patch("serial.tools.list_ports.comports", return_value=ports):
                self.assertIsNone(find_port())

    def test_macos_detection_is_unchanged(self):
        with patch("ibuki.serial_reader.sys.platform", "darwin"), patch("ibuki.serial_reader.glob.glob", return_value=["/dev/cu.usbmodem2", "/dev/cu.usbmodem1"]):
            self.assertEqual(find_port(), "/dev/cu.usbmodem1")


class ParseTest(unittest.TestCase):
    def test_config(self):
        m = parse_line("C,15.0,37.5,24,10\r\n")
        self.assertIsInstance(m, Config)
        self.assertEqual((m.first_cm, m.step_mm, m.n, m.scale), (15.0, 37.5, 24, 10.0))
        self.assertAlmostEqual(m.ranges_cm()[-1], 15.0 + 23 * 3.75)

    def test_data(self):
        m = parse_line("D,7,1234,10,-20,30,40")
        self.assertIsInstance(m, Data)
        self.assertEqual((m.seq, m.t_ms), (7, 1234))
        np.testing.assert_array_equal(m.raw, [10 - 20j, 30 + 40j])
        np.testing.assert_allclose(m.z(Config(15, 37.5, 2, 10)), [1 - 2j, 3 + 4j])

    def test_status_keeps_commas(self):
        m = parse_line("S,500,radar init failed, code 0x1")
        self.assertIsInstance(m, Status)
        self.assertEqual(m.text, "radar init failed, code 0x1")

    def test_button(self):
        self.assertIsInstance(parse_line("B,1"), Button)

    def test_rejects_junk(self):
        for junk in ["", "hello", "D,1,2,3", "D,x,2,3,4", "C,1,2", "�D,1,2,3,4", "B,0"]:
            self.assertIsNone(parse_line(junk), junk)


class SeqTest(unittest.TestCase):
    def test_gaps_and_reset(self):
        s = SeqTracker()
        self.assertEqual([s.feed(x) for x in [5, 6, 7, 10, 11]], [0, 0, 0, 2, 0])
        self.assertEqual((s.gaps, s.lost), (1, 2))
        self.assertEqual(s.feed(0), -1)
        self.assertEqual(s.resets, 1)


class ReplayTest(unittest.TestCase):
    def test_replay_plays_all_lines_fast(self):
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            f.write("C,15.0,37.5,1,10\n")
            for i in range(20):
                f.write(f"D,{i},{i * 60},1,2\n")
        got = []
        r = FileReplay(f.name, on_line=got.append, speed=100.0).start()
        r._thread.join(timeout=5)
        os.unlink(f.name)
        self.assertEqual(len(got), 21)
        self.assertTrue(got[-1].startswith("D,19,"))
        self.assertTrue(r.send("L,1"))
        self.assertEqual(r.sent, ["L,1"])


if __name__ == "__main__":
    unittest.main()
