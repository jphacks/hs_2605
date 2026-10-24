"""End-to-end: start the real server on a replayed recording, connect over WebSocket."""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import requests
from websockets.sync.client import connect

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from tests.synth import make_lines  # noqa: E402

SERVER = os.path.join(os.path.dirname(__file__), "..", "ibuki", "server.py")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerRun:
    def __init__(self, recording, brain, env_extra=None, speed=4.0):
        self.port = free_port()
        # never send real pushes from tests: drop the user's topic unless the test sets its own
        env = {k: v for k, v in os.environ.items() if k != "IBUKI_NTFY_TOPIC"}
        env.update(env_extra or {})
        self.proc = subprocess.Popen(
            [sys.executable, SERVER, "--replay", recording, "--speed", str(speed), "--brain", brain,
             "--host", "127.0.0.1", "--http-port", str(self.port), "--web-dist", "",
             "--env-file", ""],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(100):
            try:
                requests.get(self.base + "/api/health", timeout=0.5)
                return
            except requests.RequestException:
                time.sleep(0.1)
        self.stop()
        raise RuntimeError("server did not start:\n" + self.output)

    def messages(self, seconds):
        out = []
        with connect(f"ws://127.0.0.1:{self.port}/ws", open_timeout=5) as ws:
            end = time.time() + seconds
            while time.time() < end:
                try:
                    out.append(json.loads(ws.recv(timeout=0.5)))
                except TimeoutError:
                    pass
        return out

    def stop(self):
        self.proc.terminate()
        try:
            self.output = self.proc.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.output = self.proc.communicate()[0]


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        f.write("\n".join(make_lines(seconds=40, target_bin=9, lose=(50,))) + "\n")
        f.close()
        cls.recording = f.name

    @classmethod
    def tearDownClass(cls):
        os.unlink(cls.recording)

    def test_placeholder_statuses_reach_websocket(self):
        s = ServerRun(self.recording, "placeholder")
        try:
            msgs = s.messages(3.0)
            health = requests.get(s.base + "/api/health").json()
        finally:
            s.stop()
        self.assertEqual(msgs[0], {"type": "board", "connected": True})
        statuses = [m for m in msgs if "state" in m]
        self.assertGreater(len(statuses), 30)  # ~8/s at x4 speed -> ~32/s
        for m in statuses:
            for k in ("t", "state", "bpm", "disp_mm", "motion", "present", "chest_cm"):
                self.assertIn(k, m)
        self.assertEqual(health["brain"], "placeholder")
        self.assertEqual(health["seq"]["lost"], 1)
        self.assertTrue(health["board_connected"])

    def test_alert_turns_led_on_and_survives_ntfy_failure(self):
        env = {"IBUKI_NTFY_TOPIC": "ibuki-test", "IBUKI_NTFY_URL": "http://127.0.0.1:9"}
        s = ServerRun(self.recording, "tests.fake_brains:AlertBrain", env)
        try:
            msgs = s.messages(2.0)
            # button from the dashboard cancels the alert -> LED off
            with connect(f"ws://127.0.0.1:{s.port}/ws") as ws:
                ws.send(json.dumps({"type": "button"}))
                time.sleep(0.5)
            alive = requests.get(s.base + "/api/health").json()
            test_push = requests.post(s.base + "/api/test-notification").json()
        finally:
            s.stop()
        self.assertIn({"type": "alert", "reason": "no_breath", "since_s": 10}, msgs)
        self.assertIn("ALERT", [m.get("state") for m in msgs])
        self.assertIn("would send to board: L,1", s.output)
        self.assertIn("would send to board: L,0", s.output)
        self.assertIn("ntfy: not sent (failed", s.output)
        self.assertTrue(alive["ntfy"])
        self.assertFalse(test_push["ok"])

    def test_brain_crash_does_not_stop_server(self):
        s = ServerRun(self.recording, "tests.fake_brains:CrashBrain")
        try:
            time.sleep(1.0)
            health = requests.get(s.base + "/api/health").json()
        finally:
            s.stop()
        self.assertGreater(health["brain_errors"], 5)
        self.assertGreater(health["lines"], 10)


if __name__ == "__main__":
    unittest.main()
