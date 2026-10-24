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

    def test_person_walking_in_an_open_room_is_not_a_shake(self):
        # Weak room (no strong still objects) and a person walking toward the board across
        # many bins: their echo turns together like a shake, but there is no strong still
        # reference to compare with, so the detector must not claim a shake.
        rng = np.random.default_rng(3)
        lines = ["C,15.0,37.5,24,10"]
        static = rng.normal(0, 4, 24) + 1j * rng.normal(0, 4, 24)
        for i in range(500):
            z = static + rng.normal(0, 1, 24) + 1j * rng.normal(0, 1, 24)
            if i >= 200:  # walks from 100 cm toward the board at 0.5 m/s
                d_cm = 100 - (i - 200) * 0.06 * 50
                if d_cm > 30:
                    b = int(round((d_cm - 15) / 3.75))
                    for k in (b - 1, b, b + 1):
                        if 0 <= k < 24:
                            z[k] += 600 * np.exp(1j * 4 * np.pi * d_cm * 10 / 5.0)
            iq = np.round(z * 10)
            lines.append(f"D,{i},{i * 60}," + ",".join(f"{int(a)},{int(b)}" for a, b in zip(iq.real, iq.imag)))
        t, f = flags_for(lines)
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
