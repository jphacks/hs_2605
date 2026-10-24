"""Board recovery test: the radar must restart by itself and keep streaming.

Usage (repo root, nothing else using the port):
  ../env/bin/python firmware/tools/recovery_test.py

Phases: baseline, "R,1" (restart request), "T,300" (300 ms stall -> FIFO overflow),
"T,1500" (1.5 s stall -> watchdog). Expected: an "S,...,radar restart: ..." line after each
command, data continuing at ~16 lines/s, and no seq gaps. Measured on 2026-10-24: all passed.
"""
import argparse
import glob
import sys
import time

import serial


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    a = ap.parse_args()
    port = a.port or (sorted(glob.glob("/dev/cu.usbmodem*")) or [None])[0]
    if not port:
        sys.exit("no /dev/cu.usbmodem* port")

    ser = serial.Serial(port, 115200, timeout=0.1)
    time.sleep(0.3)
    ser.reset_input_buffer()
    t0 = time.time()
    rest = b""
    log = []

    def read_for(seconds):
        nonlocal rest
        end = time.time() + seconds
        while time.time() < end:
            rest += ser.read(4096)
            *lines, rest = rest.split(b"\n")
            log.extend((time.time() - t0, ln.decode("ascii", "replace").strip()) for ln in lines)

    phases = [("baseline", None, 3, None), ("restart request R,1", "R,1", 3, "requested"),
              ("stall 300 ms", "T,300", 4, "fifo overflow"),
              ("stall 1500 ms", "T,1500", 5, "radar restart")]
    marks = []
    for name, cmd, secs, _ in phases:
        marks.append(time.time() - t0)
        if cmd:
            ser.write((cmd + "\n").encode())
        read_for(secs)
    ser.close()
    marks.append(time.time() - t0)

    ok_all = True
    for (name, _, _, expect), a_t, b_t in zip(phases, marks, marks[1:]):
        d = [ln for t, ln in log if a_t <= t < b_t and ln.startswith("D,")]
        s = [ln for t, ln in log if a_t <= t < b_t and ln.startswith("S,")]
        seqs = [int(x.split(",")[1]) for x in d]
        gaps = sum(1 for p, q in zip(seqs, seqs[1:]) if q != p + 1)
        rate = len(d) / (b_t - a_t)
        restarted = expect is None or any(expect in x for x in s)
        ok = restarted and gaps == 0 and rate > 10
        ok_all &= ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}: {rate:.1f} D lines/s, seq gaps {gaps}")
        for x in s:
            print(f"      {x}")
    print("ALL PASSED" if ok_all else "SOME FAILED")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
