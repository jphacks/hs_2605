import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np  # noqa: E402

from ibuki.serial_reader import Config, Data, parse_line  # noqa: E402
from ibuki.shake import ShakeDetector  # noqa: E402
from tests.synth import make_lines  # noqa: E402


def flags_for(lines):
    cfg, det, t, out = Config(15, 37.5, 24, 10), ShakeDetector(), [], []
    for ln in lines:
        m = parse_line(ln)
        if isinstance(m, Config):
            cfg = m
        elif isinstance(m, Data):
            use = cfg.ranges_cm() >= 28
            out.append(det.feed(m.z(cfg), use))
            t.append(m.t_ms / 1000.0)
    return np.array(t), np.array(out)


class ShakeTest(unittest.TestCase):
    def test_still_breathing_is_not_a_shake(self):
        t, f = flags_for(make_lines(seconds=30, target_bin=9, amp_mm=2.0))
        self.assertEqual(f.sum(), 0)

    def test_person_moving_is_not_a_shake(self):
        t, f = flags_for(make_lines(seconds=30, target_bin=9, move=(12, 16, (8, 9, 10, 11))))
        self.assertEqual(f.sum(), 0)

    def test_board_shake_is_flagged_fast_and_clears(self):
        t, f = flags_for(make_lines(seconds=30, target_bin=9, shake=(15, 17, 0.5)))
        during = (t >= 15.1) & (t < 17)
        self.assertGreater(f[during].mean(), 0.9)
        first = t[np.argmax(f)]
        self.assertLess(first - 15.0, 0.2)  # flagged within ~2 lines
        self.assertEqual(f[t < 15].sum(), 0)
        self.assertEqual(f[t > 18.5].sum(), 0)  # cleared about 1 s after the shake


if __name__ == "__main__":
    unittest.main()
