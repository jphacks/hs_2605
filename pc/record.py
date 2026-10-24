"""Record raw board lines to recordings/rec_<name>_<time>.csv (one board line per row).

Usage (repo root):
  ../env/bin/python pc/record.py empty --seconds 60
  ../env/bin/python pc/record.py breathing_50cm --seconds 60

Prints progress every 5 s. Close the live viewer first: only one program can use the port.
The files are ignored by git; copy a short one to recordings/<name>.sample.csv to share it.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ibuki.serial_reader import BoardLink, Data, FileReplay, SeqTracker, parse_line  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="short label, for example empty, breathing_50cm, hold, moving")
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--port")
    ap.add_argument("--countdown", type=int, default=3, help="seconds to get in position")
    ap.add_argument("--from-file", help="record from a saved file instead of the board (for tests)")
    ap.add_argument("--out-dir", default=os.path.join(REPO, "recordings"))
    a = ap.parse_args()

    os.makedirs(a.out_dir, exist_ok=True)
    path = os.path.join(a.out_dir, f"rec_{a.name}_{time.strftime('%Y%m%d-%H%M%S')}.csv")

    for i in range(a.countdown, 0, -1):
        print(f"starting in {i} ...")
        time.sleep(1)

    seq = SeqTracker()
    counts = {"D": 0, "other": 0}
    recording = [False]
    out = open(path, "w", encoding="ascii", errors="replace")

    def on_line(text):
        if not recording[0]:
            return
        out.write(text + "\n")
        m = parse_line(text)
        if isinstance(m, Data):
            counts["D"] += 1
            seq.feed(m.seq)
        else:
            counts["other"] += 1

    def on_state(connected):
        print("board connected" if connected else "board DISCONNECTED (check the cable)")

    if a.from_file:
        src = FileReplay(a.from_file, on_line=on_line, on_state=on_state, speed=20.0)
    else:
        src = BoardLink(a.port, on_line=on_line, on_state=on_state)
    recording[0] = True
    src.start()
    print(f"recording '{a.name}' for {a.seconds:.0f} s -> {path}")
    t0 = time.time()
    next_report = 5.0
    try:
        while time.time() - t0 < a.seconds:
            time.sleep(0.2)
            if time.time() - t0 >= next_report:
                print(f"  {next_report:3.0f} s: {counts['D']} D lines, lost {seq.lost}")
                next_report += 5.0
    except KeyboardInterrupt:
        print("stopped early")
    recording[0] = False
    src.stop()
    out.close()
    secs = time.time() - t0
    print(f"done: {counts['D']} D lines in {secs:.0f} s ({counts['D'] / max(secs, 1e-9):.1f}/s), "
          f"lost {seq.lost}, board restarts {seq.resets}")
    print(f"saved {path}")
    if counts["D"] == 0:
        sys.exit("no data recorded: is the board plugged in and the viewer closed?")


if __name__ == "__main__":
    main()
