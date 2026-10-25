"""pc/notify_demo.py: needs a topic, rejects unknown names (never sends anything here)."""
import os
import subprocess
import sys
import unittest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "notify_demo.py")


def run(*args):
    env = {k: v for k, v in os.environ.items() if k != "IBUKI_NTFY_TOPIC"}
    return subprocess.run([sys.executable, SCRIPT, "--env-file", "", *args],
                          capture_output=True, text=True, env=env)


class NotifyDemoTest(unittest.TestCase):
    def test_needs_a_topic(self):
        r = run()
        self.assertEqual(r.returncode, 1)
        self.assertIn("ntfy is off", r.stderr)

    def test_rejects_unknown_names(self):
        r = run("nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown name nope", r.stderr)
