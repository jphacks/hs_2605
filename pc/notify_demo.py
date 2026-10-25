"""Send IBUKI's notifications to the phone, to show what each one looks like.

Run from the repo root:
  ../env/bin/python pc/notify_demo.py                all four, lowest to highest urgency, 5 s apart
  ../env/bin/python pc/notify_demo.py no_breath      only some (woke_up, fast_breathing, left_bed,
                                                     no_breath)

Uses the same ntfy topic as the server: IBUKI_NTFY_TOPIC in <repo>/.env.
"""
import argparse
import os
import sys
import time

PC_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_DIR = os.path.dirname(PC_DIR)
sys.path.insert(0, PC_DIR)

from dotenv import load_dotenv  # noqa: E402

from ibuki.server import Notifier  # noqa: E402

# lowest to highest urgency: (event like the Brain sends it, urgent)
DEMO = [
    ({"reason": "woke_up"}, False),
    ({"reason": "fast_breathing", "bpm": 55}, False),
    ({"reason": "left_bed"}, True),
    ({"reason": "no_breath", "since_s": 10}, True),
]


def main():
    names = [e["reason"] for e, _ in DEMO]
    ap = argparse.ArgumentParser(description="send IBUKI's notifications to the phone")
    ap.add_argument("only", nargs="*", metavar="name",
                    help=f"only these: {', '.join(names)} (default: all)")
    ap.add_argument("--gap", type=float, default=5.0, help="seconds between two notifications")
    ap.add_argument("--env-file", default=os.path.join(REPO_DIR, ".env"),
                    help="file with IBUKI_NTFY_TOPIC=...; '' to skip")
    a = ap.parse_args()
    unknown = [n for n in a.only if n not in names]
    if unknown:
        ap.error(f"unknown name {', '.join(unknown)}; names are: {', '.join(names)}")
    if a.env_file:
        load_dotenv(a.env_file, override=False)
    notifier = Notifier(os.environ.get("IBUKI_NTFY_TOPIC"), os.environ.get("IBUKI_NTFY_URL", "https://ntfy.sh"))
    if not notifier.enabled:
        sys.exit("ntfy is off: put IBUKI_NTFY_TOPIC=<topic> in .env (see .env.example)")

    plan = [(e, urgent) for e, urgent in DEMO if not a.only or e["reason"] in a.only]
    for i, (event, urgent) in enumerate(plan):
        if i:
            time.sleep(a.gap)
        priority, tag = Notifier.level_for(event, urgent)
        title = Notifier.title_for(event, urgent)
        ok, info = notifier.send(title, Notifier.message_for(event), force=True, priority=priority, tag=tag)
        print(f"priority {priority}  {title}  {'sent' if ok else 'NOT sent'} ({info})")


if __name__ == "__main__":
    main()
