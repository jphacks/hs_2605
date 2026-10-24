"""Guided recording session: walks through all the recordings we need, with spoken cues.

Usage (repo root, board plugged in, viewer closed):
  ../env/bin/python pc/record_session.py                   # asks for your name
  ../env/bin/python pc/record_session.py --person ramadhan
  ../env/bin/python pc/record_session.py --server          # if the server is running
  ../env/bin/python pc/record_session.py --only hold,shake # just some of them

Before each recording it tells you what to do and waits for Enter. The Mac says "start",
then the cues (for example "hold your breath now"), then "done". Each recording is 60 s.
Each person gets a folder: recordings/<person>/rec_<name>_<time>.csv (git-ignored).
"""
import argparse
import os
import re
import subprocess
import sys
import threading
import time

PC_DIR = os.path.dirname(os.path.abspath(__file__))
RECORD = os.path.join(PC_DIR, "record.py")
RECORDINGS = os.path.join(os.path.dirname(PC_DIR), "recordings")

# From 2 s to 32 s: "in"/"out" every 3 s = exactly 10 breaths/min, so we know the true rate.
# After that: natural breathing, the realistic part.
PACED = [(2 + 6 * k, "in") for k in range(5)] + [(5 + 6 * k, "out") for k in range(5)] + \
        [(32, "Now breathe normally")]

# name, what to do, spoken cues (seconds after the start, text)
AT_50 = "Chest 50 cm from the board (a bit less than an arm's length)."
SESSION = [
    ("empty", "Nobody within 1.2 m of the board. Press Enter, then step away (you get 8 s). "
              "Stay away until you hear 'Done'.",
     [(-8, "Step away now, and stay away until I say done")]),
    ("breathing_20cm", "Chest 20 cm from the board (about one hand span). Stay still. "
                       "Breathe with the voice, then normally.", PACED),
    ("breathing_30cm", "Chest 30 cm from the board (about one school ruler). Stay still. "
                       "Breathe with the voice, then normally.", PACED),
    ("breathing_50cm", AT_50 + " Stay still. Breathe with the voice, then normally.", PACED),
    ("hold", AT_50 + " Breathe normally, hold your breath when you hear it, breathe again when told.",
     [(20, "Hold your breath now"), (40, "Breathe again")]),
    ("moving", AT_50 + " Shift around and move your arms the whole minute.", []),
    ("shake", AT_50 + " Sit still and breathe quietly. Tap or bump the table when you hear it, "
                      "stop when you hear 'Stop'.",
     [(20, "Tap the table now"), (25, "Stop"), (40, "Tap the table again"), (45, "Stop")]),
    ("leave", AT_50 + " Breathe quietly. Walk away when you hear it, and stay away until you "
                      "hear 'Done'.",
     [(20, "Walk away now, and stay away until I say done")]),
]


def speak(text, enabled):
    print(f"   >> {text}")
    if enabled:
        subprocess.Popen(["say", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_cues(cues, start_time, enabled, stop):
    for at, text in sorted(cues):
        while not stop.is_set() and time.time() < start_time + at:
            time.sleep(0.05)
        if stop.is_set():
            return
        speak(text, enabled)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", action="store_true", help="record through the running server")
    ap.add_argument("--only", help="comma-separated names to record")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--no-say", action="store_true", help="print the cues instead of speaking")
    ap.add_argument("--from-file", help="for tests: record from a saved file")
    ap.add_argument("--person", help="your name; recordings go to recordings/<person>/")
    ap.add_argument("--out-dir", help="for tests: save here instead")
    ap.add_argument("--yes", action="store_true", help="for tests: don't wait for Enter")
    a = ap.parse_args()

    wanted = set(a.only.split(",")) if a.only else None
    plan = [s for s in SESSION if wanted is None or s[0] in wanted]
    if not plan:
        sys.exit(f"nothing to record; names are: {', '.join(s[0] for s in SESSION)}")
    person = a.person
    if not person and not a.out_dir:
        person = "test" if a.yes else input("Your name (for your recordings folder): ")
    if person:
        person = re.sub(r"[^a-z0-9_-]+", "_", person.strip().lower()).strip("_") or "anonymous"
    out_dir = a.out_dir or os.path.join(RECORDINGS, person)
    say_on = not a.no_say
    print(f"{len(plan)} recordings, {a.seconds:.0f} s each, saved in {out_dir}")
    print("Ctrl+C stops the session.\n")

    done = []
    for i, (name, todo, cues) in enumerate(plan, 1):
        print(f"[{i}/{len(plan)}] {name}: {todo}")
        if not a.yes:
            answer = input("   Enter = start, s = skip, q = quit: ").strip().lower()
            if answer == "q":
                break
            if answer == "s":
                continue
        lead = 8 if name == "empty" else 3  # time to get in position after Enter
        cmd = [sys.executable, RECORD, name, "--seconds", str(a.seconds), "--countdown", str(lead)]
        if a.server:
            cmd.append("--server")
        if a.from_file:
            cmd += ["--from-file", a.from_file]
        cmd += ["--out-dir", out_dir]

        speak("Get ready", say_on)
        start_time = time.time() + lead  # record.py counts down before it starts
        stop = threading.Event()
        threading.Thread(target=run_cues, args=([(0, "Start")] + cues, start_time, say_on, stop),
                         daemon=True).start()
        proc = subprocess.run(cmd, capture_output=True, text=True)
        stop.set()
        speak("Done", say_on)
        lines = [ln for ln in proc.stdout.splitlines() if ln.startswith(("done:", "saved"))]
        for ln in lines:
            print(f"   {ln}")
        if proc.returncode != 0:
            print(f"   FAILED: {proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr.strip()}")
        else:
            done.append(name)
        print()

    print(f"recorded: {', '.join(done) if done else 'nothing'}")


if __name__ == "__main__":
    main()
