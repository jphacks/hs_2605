"""The trained models as the Brain (ibuki.model_brain) on the three sample recordings.

Skipped where the exported models aren't there (ml/data/ is not in git; see ml/README.md).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HAVE_MODELS = os.path.exists(os.path.join(REPO, "ml", "data", "models", "state.npz"))


def alerts(name):
    from ibuki.model_brain import Brain
    b, found = Brain(), []
    with open(os.path.join(REPO, "recordings", "samples", f"{name}.sample.csv")) as f:
        for ln in f:
            found += [m["reason"] for m in b.feed(ln.strip()) if m.get("type") == "alert"]
    return found


@unittest.skipUnless(HAVE_MODELS, "exported models not found (ml/data/models)")
class ModelBrainTest(unittest.TestCase):
    def test_breathing_gives_no_alert(self):
        self.assertEqual(alerts("breathing"), [])

    def test_breath_hold_gives_no_breath(self):
        self.assertEqual(alerts("hold"), ["no_breath"])

    def test_walking_away_gives_left_bed(self):
        self.assertEqual(alerts("leave"), ["left_bed"])


if __name__ == "__main__":
    unittest.main()
