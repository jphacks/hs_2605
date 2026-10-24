import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from ibuki.placeholder_brain import PlaceholderBrain  # noqa: E402
from tests.synth import make_lines  # noqa: E402

FIELDS = {"t", "state", "bpm", "disp_mm", "motion", "present", "chest_cm"}


def run(lines):
    brain = PlaceholderBrain()
    out = []
    for ln in lines:
        out += brain.feed(ln)
    return out


class PlaceholderBrainTest(unittest.TestCase):
    def test_breathing_target(self):
        st = run(make_lines(seconds=20, target_bin=9, amp_mm=2.0))
        self.assertGreater(len(st), 100)  # ~8 statuses/s
        for s in st:
            self.assertTrue(FIELDS <= set(s), s)
            self.assertIsNone(s["bpm"])  # placeholder never claims a breathing rate
            self.assertEqual(s["source"], "placeholder")
        last = st[-1]
        self.assertAlmostEqual(last["chest_cm"], 48.75)
        self.assertTrue(last["present"])
        self.assertEqual(last["state"], "OK")
        # the displacement wave over the last 10 s should swing about +-2 mm (4 mm peak to peak)
        wave = np.concatenate([s["disp_mm"] for s in st[-80:]])
        self.assertGreater(np.ptp(wave), 2.5)
        self.assertLess(np.ptp(wave), 6.0)

    def test_empty_room(self):
        st = run(make_lines(seconds=20, target_bin=None))
        self.assertFalse(st[-1]["present"])
        self.assertEqual(st[-1]["state"], "EMPTY")

    def test_board_shake_means_unknown_then_recovers(self):
        st = run(make_lines(seconds=30, target_bin=9, shake=(15, 17, 0.5)))
        during = [s for s in st if 15200 <= s["t"] < 17000]
        self.assertTrue(during and all(s["shaking"] for s in during))
        self.assertTrue(all(s["disp_mm"] == [] for s in during))  # never a fake flat line
        self.assertTrue(all(s["present"] for s in during))  # presence held, not dropped
        after = [s for s in st if s["t"] >= 22000]
        self.assertTrue(after and not any(s["shaking"] for s in after))
        self.assertEqual(after[-1]["chest_cm"], 48.75)
        self.assertTrue(after[-1]["disp_mm"])
        before = [s for s in st if 5000 <= s["t"] < 15000]
        self.assertFalse(any(s["shaking"] for s in before))

    def test_ignores_junk_and_status_lines(self):
        brain = PlaceholderBrain()
        for junk in ["", "garbage", "S,10,hello", "B,1", "D,1,2"]:
            self.assertEqual(brain.feed(junk), [])


if __name__ == "__main__":
    unittest.main()
