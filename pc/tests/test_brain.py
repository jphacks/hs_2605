"""The Brain on synthetic board data: states, rate, alerts, button."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ibuki.brain import Brain  # noqa: E402
from tests.synth import make_lines  # noqa: E402

FIELDS = {"t", "state", "bpm", "disp_mm", "dt_ms", "motion", "present", "chest_cm", "shaking"}


def run(lines, brain=None):
    brain = brain or Brain(no_breath_s=10)
    statuses, events = [], []
    for ln in lines:
        for o in brain.feed(ln):
            (events if "type" in o else statuses).append(o)
    return statuses, events


def states_between(statuses, t0, t1):
    return {s["state"] for s in statuses if t0 * 1000 <= s["t"] < t1 * 1000}


def later(lines, dt_ms):
    """The D lines of another make_lines() run, continuing dt_ms later (same room: same seed)."""
    out = []
    for ln in lines:
        if ln.startswith("D,"):
            kind, seq, t, rest = ln.split(",", 3)
            out.append(f"D,{int(seq) + 100000},{int(t) + dt_ms},{rest}")
    return out


class BrainTest(unittest.TestCase):
    def test_empty_room(self):
        st, ev = run(make_lines(seconds=20, target_bin=None))
        self.assertEqual(states_between(st, 3, 20), {"EMPTY"})
        self.assertEqual(ev, [])

    def test_breathing_is_still_with_the_right_rate(self):
        st, ev = run(make_lines(seconds=40, target_bin=9, breaths_per_min=40, amp_mm=1.0))
        for s in st:
            self.assertTrue(FIELDS <= set(s), s)
        self.assertEqual(states_between(st, 15, 40), {"STILL"})
        rates = [s["bpm"] for s in st if s["t"] > 25000 and s["bpm"] is not None]
        self.assertTrue(rates, "no breathing rate reported")
        self.assertAlmostEqual(sum(rates) / len(rates), 40.0, delta=2.0)
        self.assertEqual(ev, [])

    def test_breath_hold_alerts_and_recovers(self):
        st, ev = run(make_lines(seconds=60, target_bin=9, breaths_per_min=20, amp_mm=1.0,
                                hold=(25, 45)))
        self.assertEqual(states_between(st, 15, 25), {"STILL"})
        alerts = [e for e in ev if e["type"] == "alert" and e["reason"] == "no_breath"]
        self.assertEqual(len(alerts), 1)
        self.assertIn("ALERT", states_between(st, 35, 45))
        self.assertIn("STILL", states_between(st, 52, 60))  # breathing again

    def test_light_breathing_after_deep_breaths_is_still_breathing(self):
        # paced deep breaths, then light ones (Manami's were only 16-30% as deep)
        st, ev = run(make_lines(seconds=75, target_bin=9, breaths_per_min=10, amp_mm=2.0,
                                lighter=(30, 0.7)))
        self.assertEqual([e for e in ev if e["type"] == "alert"], [])
        self.assertEqual(states_between(st, 60, 75), {"STILL"})

    def test_moving_after_a_short_rest_is_not_waking_up(self):
        # woke_up is for waking from sleep (10 min still), so a short rest must not send it
        st, ev = run(make_lines(seconds=55, target_bin=9, breaths_per_min=20, amp_mm=1.0,
                                move=(40, 55, (7, 8, 9, 10, 11))))
        self.assertIn("OK", states_between(st, 42, 55))
        self.assertEqual([e for e in ev if e.get("reason") == "woke_up"], [])

    def test_moving_is_awake_and_never_alerts(self):
        st, ev = run(make_lines(seconds=40, target_bin=9, move=(10, 40, (7, 8, 9, 10, 11))))
        self.assertIn("OK", states_between(st, 15, 40))
        self.assertNotIn("ALERT", states_between(st, 0, 40))
        self.assertEqual([e for e in ev if e["type"] == "alert"], [])

    def test_leaving_the_bed_sends_left_bed(self):
        # room=30: where the person sat, the room is much quieter than them (as in our recordings)
        st, ev = run(make_lines(seconds=40, target_bin=9, move=(17, 20, (7, 8, 9, 10, 11)), leave_at=20,
                                room=30))
        self.assertEqual(states_between(st, 26, 40), {"EMPTY"})
        self.assertIn({"type": "alert", "reason": "left_bed", "since_s": 0}, ev)
        self.assertNotIn("no_breath", [e["reason"] for e in ev])

    def test_leaving_without_moving_is_still_leaving(self):
        # sliding out of view quietly: their echo is gone, so "not in bed", not "not breathing"
        st, ev = run(make_lines(seconds=40, target_bin=9, breaths_per_min=20, amp_mm=1.0, leave_at=20,
                                room=30))
        self.assertEqual(states_between(st, 27, 40), {"EMPTY"})
        self.assertIn({"type": "alert", "reason": "left_bed", "since_s": 0}, ev)
        self.assertNotIn("no_breath", [e["reason"] for e in ev])

    def test_breathing_gone_with_their_echo_is_leaving_not_alarm(self):
        # walking off slowly: breathing vanishes before the room is quiet (seen live at 14:14,
        # no_breath came 1 s before left_bed). Their echo is gone, so it must be "left the bed"
        b = Brain(no_breath_s=10)
        run(make_lines(seconds=25, target_bin=9, breaths_per_min=20, amp_mm=1.0, room=30), b)
        b.echo_now = b.person_echo * 0.02
        b._update_state(b.last_t, False, 0.1)
        reasons = [e["reason"] for e in b._take_events()]
        self.assertEqual(b.state, "EMPTY")
        self.assertIn("left_bed", reasons)
        self.assertNotIn("no_breath", reasons)

    def test_moving_then_not_breathing_is_no_breath_not_left_bed(self):
        # the dangerous mix-up: their echo is still there, so the alert must be about breathing
        st, ev = run(make_lines(seconds=45, target_bin=9, breaths_per_min=20, amp_mm=1.0,
                                move=(17, 20, (7, 8, 9, 10, 11)), hold=(20, 45), room=30))
        self.assertNotIn("EMPTY", states_between(st, 20, 45))
        self.assertNotIn("left_bed", [e["reason"] for e in ev])
        self.assertIn("no_breath", [e["reason"] for e in ev])

    def test_a_hand_next_to_the_board_is_not_the_chest(self):
        # in the presentation the "chest" ended up at 22.5 cm: a hand moving by the board
        st, ev = run(make_lines(seconds=40, target_bin=12, breaths_per_min=20, amp_mm=1.0,
                                target_amp=300, move=(0, 40, (1, 2)), room=30))
        chests = {s["chest_cm"] for s in st if s["t"] > 10000 and s["chest_cm"] is not None}
        self.assertTrue(chests, "no chest found")
        self.assertGreaterEqual(min(chests), 28.0, chests)

    def test_others_moving_away_from_the_bed_do_not_hide_a_breath_hold(self):
        # a hand by the board (19-23 cm) and someone walking past (90-94 cm) while the sleeper
        # (49 cm) stops breathing: their movement is not the sleeper's
        for bins in ((1, 2), (20, 21)):
            st, ev = run(make_lines(seconds=60, target_bin=9, breaths_per_min=20, amp_mm=1.0,
                                    hold=(25, 60), move=(0, 60, bins), room=30))
            self.assertIn("no_breath", [e["reason"] for e in ev], bins)

    def test_leaving_forgets_their_normal_breathing(self):
        # the next person is compared with their own breathing. Mixing our recordings, Manami
        # lying down after lemonwasp (deep breaths) got the no-breathing alarm before any hold
        b = Brain(no_breath_s=10)
        first = make_lines(seconds=30, target_bin=9, breaths_per_min=12, amp_mm=2.0, leave_at=22, room=30)
        st, ev = run(first, b)
        self.assertIn("left_bed", [e["reason"] for e in ev])
        self.assertEqual(b.norms, {})
        second = make_lines(seconds=45, target_bin=9, breaths_per_min=16, amp_mm=0.4, room=30)
        st, ev = run(later(second, 30000), b)
        self.assertEqual([e for e in ev if e["type"] == "alert"], [])
        self.assertEqual(states_between(st, 60, 75), {"STILL"})

    def test_board_shake_never_alerts(self):
        st, ev = run(make_lines(seconds=50, target_bin=9, breaths_per_min=20, amp_mm=1.0,
                                shake=(25, 32, 0.5)))
        self.assertTrue(any(s["shaking"] for s in st))
        self.assertEqual([e for e in ev if e["type"] == "alert"], [])

    def test_button_acknowledges_an_alert(self):
        brain = Brain(no_breath_s=10)
        lines = make_lines(seconds=60, target_bin=9, breaths_per_min=20, amp_mm=1.0, hold=(25, 60))
        st, ev = run(lines[: int(len(lines) * 0.75)], brain)  # up to ~45 s: in ALERT
        self.assertEqual(st[-1]["state"], "ALERT")
        out = brain.feed("B,1")
        # no extra status: it repeated the last time with no wave, so the dashboard's graph
        # started again from zero every time someone pressed stop
        self.assertEqual([o for o in out if "state" in o], [])
        st2, ev2 = run(lines[int(len(lines) * 0.75):], brain)
        self.assertIn(st2[0]["state"], ("OK", "STILL"))  # acknowledged
        self.assertGreater(st2[0]["t"], st[-1]["t"])
        self.assertTrue(st2[0]["disp_mm"])
        self.assertEqual([e for e in ev2 if e["type"] == "alert"], [])  # snoozed for 30 s

    def test_ignores_junk(self):
        brain = Brain()
        for junk in ["", "garbage", "S,10,hello", "D,1,2"]:
            self.assertEqual(brain.feed(junk), [])


if __name__ == "__main__":
    unittest.main()
