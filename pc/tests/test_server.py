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
        env["IBUKI_SIREN"] = "0"  # tests log the siren but never play it
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
        self.assertEqual(s.output.count("would send to board: L,1"), 1)  # once, not per status
        self.assertIn("would send to board: L,0", s.output)
        self.assertIn("ntfy [no_breath, priority 5]: not sent (failed", s.output)
        self.assertNotIn("ntfy [check", s.output)  # CHECK alone never pushes
        # the siren sounds for no breathing until someone presses the button
        self.assertLess(s.output.index("SIREN on"), s.output.index("SIREN off"))
        self.assertLess(s.output.index("button from dashboard"), s.output.index("SIREN off"))
        self.assertTrue(alive["ntfy"])
        self.assertFalse(alive["led_on"])
        self.assertFalse(test_push["ok"])

    def test_dashboard_stop_button_stops_the_siren(self):
        s = ServerRun(self.recording, "tests.fake_brains:AlertBrain")
        try:
            s.messages(2.0)  # the no_breath alert starts the siren
            before = requests.get(s.base + "/api/health").json()["siren"]
            requests.post(s.base + "/api/button")
            time.sleep(0.5)
            after = requests.get(s.base + "/api/health").json()["siren"]
        finally:
            s.stop()
        self.assertTrue(before)
        self.assertFalse(after)

    def test_woke_up_is_info_left_bed_is_alert(self):
        env = {"IBUKI_NTFY_TOPIC": "ibuki-test", "IBUKI_NTFY_URL": "http://127.0.0.1:9"}
        s = ServerRun(self.recording, "tests.fake_brains:StoryBrain", env)
        try:
            msgs = s.messages(2.5)
        finally:
            s.stop()
        out = s.output
        self.assertIn({"type": "info", "reason": "woke_up", "since_s": 0}, msgs)
        self.assertIn({"type": "alert", "reason": "left_bed", "since_s": 0}, msgs)  # dashboard needs since_s
        self.assertIn("ntfy [woke_up, priority 2]: not sent", out)  # low: arrives silently
        self.assertIn("ntfy [left_bed, priority 4]: not sent", out)  # high: time sensitive
        led_on = out.index("would send to board: L,1")
        self.assertLess(out.index("INFO event"), led_on)  # woke up: no LED
        self.assertLess(out.index("'reason': 'left_bed'"), led_on)  # left bed: LED on
        self.assertGreater(out.index("would send to board: L,0"), led_on)  # back in bed: off
        self.assertNotIn("SIREN on", out)  # the siren is only for no breathing

    def test_message_texts(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from ibuki.server import Notifier

        self.assertEqual(Notifier.message_for({"reason": "no_breath", "since_s": 20}),
                         "赤ちゃんの呼吸が20秒間見えません。すぐに様子を見てください。")
        self.assertEqual(Notifier.message_for({"reason": "left_bed"}),
                         "赤ちゃんがベッドにいません。様子を確認してください。")
        self.assertEqual(Notifier.message_for({"reason": "woke_up"}), "赤ちゃんが起きたようです。")
        self.assertEqual(Notifier.message_for({"reason": "fast_breathing", "bpm": 62.4}),
                         "1分に62回の速い呼吸が続いています。様子を見てください。")

    def test_titles_start_with_the_urgency(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from ibuki.server import Notifier

        self.assertEqual(Notifier.title_for({"reason": "no_breath"}, True), "【緊急】呼吸が確認できません")
        self.assertEqual(Notifier.title_for({"reason": "left_bed"}, True), "【注意】ベッドにいません")
        self.assertEqual(Notifier.title_for({"reason": "woke_up"}, False), "【お知らせ】起きました")
        self.assertEqual(Notifier.title_for({"reason": "something_new"}, True), "【緊急】息吹～IBUKI～")

    def test_urgency_levels(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from ibuki.server import Notifier

        self.assertEqual(Notifier.level_for({"reason": "no_breath"}, True), (5, "rotating_light"))
        self.assertEqual(Notifier.level_for({"reason": "left_bed"}, True), (4, "warning"))
        self.assertEqual(Notifier.level_for({"reason": "fast_breathing"}, False), (4, "warning"))
        self.assertEqual(Notifier.level_for({"reason": "woke_up"}, False), (2, "baby"))
        self.assertEqual(Notifier.level_for({"reason": "something_new"}, True), (5, "warning"))
        self.assertEqual(Notifier.level_for({"reason": "something_new"}, False), (3, "baby"))

    def test_raw_lines_stream(self):
        s = ServerRun(self.recording, "placeholder")
        try:
            got = []
            with connect(f"ws://127.0.0.1:{s.port}/ws/raw") as ws:
                end = time.time() + 1.5
                while time.time() < end:
                    try:
                        got.append(ws.recv(timeout=0.5))
                    except TimeoutError:
                        pass
        finally:
            s.stop()
        d_lines = [g for g in got if g.startswith("D,")]
        self.assertGreater(len(d_lines), 20)
        self.assertEqual(len(d_lines[0].split(",")), 3 + 48)

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
