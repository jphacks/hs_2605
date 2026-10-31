"""Offline capture integrity metrics; board t_ms is uptime, not Unix time."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import math
from statistics import median

from .protocol import Capture


@dataclass(frozen=True)
class QualityReport:
    """Counts describe adjacent parsed D frames in capture order, never repairs.

    Timing needs >=3 positive intervals with seq delta exactly one. Long gaps
    are forward seq/time pairs above 3 * their median. Resets are boundaries,
    not elapsed capture duration. See README for all field definitions.
    """
    total_d_frames: int
    parser_issue_count: int
    issue_source_line_count: int
    estimated_missing_frames: int
    sequence_gap_events: int
    duplicate_sequence_events: int
    backward_sequence_events: int
    repeated_timestamp_events: int
    backward_timestamp_events: int
    suspected_reset_events: int
    timing_reference_interval_count: int
    typical_sampling_interval_ms: float | None
    long_interval_multiplier: float
    long_interval_threshold_ms: float | None
    long_interval_events: int | None
    frames_without_usable_configuration: int
    frames_without_configuration: int
    bin_count_mismatch_frames: int
    unparsed_message_kind_counts: dict[str, int]

    def to_dict(self) -> dict:
        """Return a detached, JSON-serializable dictionary with stable keys."""
        return asdict(self)


def analyze_quality(capture: Capture) -> QualityReport:
    """Diagnose parsed D adjacency without assuming fixed firmware constants.

    Positive seq gaps estimate missing D frames except across backward time.
    Equal seq is a duplicate event; negative seq is a backward event. A negative
    seq OR negative time delta marks one suspected reset, not proof of reboot.
    No duration/rate is computed across those boundaries.
    """
    missing = gaps = duplicates = backward_seq = repeated_time = backward_time = resets = 0
    reference = []
    forward_intervals = []
    for previous, current in zip(capture.frames, capture.frames[1:]):
        ds = current.data.seq - previous.data.seq
        dt = current.data.t_ms - previous.data.t_ms
        duplicates += ds == 0
        backward_seq += ds < 0
        repeated_time += dt == 0
        backward_time += dt < 0
        resets += ds < 0 or dt < 0
        if ds > 1 and dt >= 0:
            gaps += 1
            missing += ds - 1
        if ds > 0 and dt > 0:
            forward_intervals.append(dt)
            if ds == 1:
                reference.append(dt)
    typical = float(median(reference)) if len(reference) >= 3 else None
    multiplier = 3.0
    threshold = typical * multiplier if typical is not None else None
    long_events = sum(dt > threshold for dt in forward_intervals) if threshold is not None else None
    no_config = mismatches = unusable = 0
    for frame in capture.frames:
        cfg = frame.config
        no_config += cfg is None
        mismatch = cfg is not None and (len(frame.data.i) != cfg.n or len(frame.data.q) != cfg.n)
        mismatches += mismatch
        valid = (cfg is not None and all(math.isfinite(v) for v in
                 (cfg.first_cm, cfg.step_mm, cfg.scale)) and cfg.first_cm >= 0
                 and cfg.step_mm > 0 and cfg.n > 0 and cfg.scale > 0)
        unusable += not valid or mismatch
    return QualityReport(
        total_d_frames=len(capture.frames), parser_issue_count=len(capture.issues),
        issue_source_line_count=len({issue.line_number for issue in capture.issues}),
        estimated_missing_frames=missing, sequence_gap_events=gaps,
        duplicate_sequence_events=duplicates, backward_sequence_events=backward_seq,
        repeated_timestamp_events=repeated_time, backward_timestamp_events=backward_time,
        suspected_reset_events=resets, timing_reference_interval_count=len(reference),
        typical_sampling_interval_ms=typical, long_interval_multiplier=multiplier,
        long_interval_threshold_ms=threshold, long_interval_events=long_events,
        frames_without_usable_configuration=unusable, frames_without_configuration=no_config,
        bin_count_mismatch_frames=mismatches,
        unparsed_message_kind_counts=dict(sorted(Counter(
            message.kind for _, message in capture.other_messages).items())))


def main(argv: list[str] | None = None) -> int:
    """Print a report to stdout; input errors go to stderr with exit status 2."""
    import argparse
    import json
    from .protocol import read_capture

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="raw radar recording path")
    parser.add_argument("--json", action="store_true", help="emit deterministic JSON")
    args = parser.parse_args(argv)
    try:
        report = analyze_quality(read_capture(args.path))
    except OSError as exc:
        parser.exit(2, f"error: cannot read {args.path!r}: {exc}\n")
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True, allow_nan=False))
    else:
        print("Radar capture integrity (t_ms is board uptime, not Unix time)")
        if report.total_d_frames == 0:
            print("No parsed D frames; timing metrics are unavailable.")
        for name, value in report.to_dict().items():
            print(f"{name.replace('_', ' ')}: {'unavailable' if value is None else value}")
        print("Missing frames are sequence-derived estimates; no data is repaired.")
        print("Capture integrity does not establish signal quality or AI accuracy.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
