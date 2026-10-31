"""Headless offline D-capture visualization, without signal interpretation."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .protocol import Capture, Config
from .quality import QualityReport, analyze_quality


@dataclass(frozen=True)
class VisualizationData:
    elapsed_s: np.ndarray
    distances_cm: np.ndarray
    z: np.ndarray
    variation: np.ndarray
    bin_index: int
    config: Config
    start_t_ms: int
    trace_segments: tuple[slice, ...]
    quality: QualityReport


def prepare_visualization(capture: Capture, bin_index: int = 0) -> VisualizationData:
    """Prepare actual sample coordinates and |z - temporal mean(z)| per bin.

    Reject ambiguous geometry/time order. Malformed rows remain in the whole
    capture quality summary; parsed D frames are plotted without interpolation.
    """
    quality = analyze_quality(capture)
    if not capture.frames:
        raise ValueError("No parsed D frames (empty or E-only captures cannot be plotted)")
    if quality.frames_without_usable_configuration:
        raise ValueError("Every D frame needs usable C configuration and matching I/Q bin counts")
    config = capture.frames[0].config
    assert config is not None
    if any(frame.config != config for frame in capture.frames) or any(
            cfg != config for _, cfg in capture.configs):
        raise ValueError("Configuration changes are unsupported: geometry or scale must stay constant")
    if isinstance(bin_index, bool) or not isinstance(bin_index, int) or not 0 <= bin_index < config.n:
        raise ValueError(f"Bin index must be an integer from 0 to {config.n - 1}; got {bin_index!r}")
    if quality.repeated_timestamp_events or quality.backward_timestamp_events:
        raise ValueError("Timestamps must strictly increase; repeated/backward board time is unsupported")
    if quality.duplicate_sequence_events or quality.backward_sequence_events:
        raise ValueError("Duplicate/backward sequence numbers or suspected restarts are unsupported")
    start = capture.frames[0].data.t_ms
    elapsed = np.array([(frame.data.t_ms - start) / 1000 for frame in capture.frames])
    try:
        z = np.asarray([frame.z() for frame in capture.frames], dtype=np.complex128)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"Cannot scale I/Q values: {exc}") from exc
    with np.errstate(over="ignore", invalid="ignore"):
        variation = np.abs(z - z.mean(axis=0))
        distances = np.asarray(config.ranges_cm())
    if not all(np.isfinite(values).all() for values in (elapsed, distances, z, variation)):
        raise ValueError("Scaled values, distances and elapsed times must be finite")
    # Line connections express only short, uninterrupted adjacency; heatmap
    # columns always remain at the actual sample times regardless of segments.
    threshold = quality.long_interval_threshold_ms
    if threshold is None and len(capture.frames) > 1:
        threshold = 3 * min(b.data.t_ms - a.data.t_ms
                            for a, b in zip(capture.frames, capture.frames[1:]))
    issue_lines = sorted({issue.line_number for issue in capture.issues})
    breaks = [0]
    for k, (a, b) in enumerate(zip(capture.frames, capture.frames[1:]), 1):
        if (b.data.seq != a.data.seq + 1
                or (threshold is not None and b.data.t_ms - a.data.t_ms > threshold)
                or any(a.line_number < line < b.line_number for line in issue_lines)):
            breaks.append(k)
    breaks.append(len(capture.frames))
    segments = tuple(slice(a, b) for a, b in zip(breaks, breaks[1:]))
    return VisualizationData(elapsed, distances, z, variation, bin_index, config,
                             start, segments, quality)


def build_figure(data: VisualizationData, title: str = "Radar capture"):
    """Build an Agg figure with discrete time columns, I/Q, and whole-file counts."""
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.collections import LineCollection
    from matplotlib.figure import Figure
    from textwrap import shorten

    figure = Figure(figsize=(13, 10), layout="constrained")
    FigureCanvasAgg(figure)
    axes = figure.subplots(3, 1, gridspec_kw={"height_ratios": [3, 2, 1.8]})
    heat, trace, summary = axes
    half_step = data.config.step_mm / 20
    lower = data.distances_cm - half_step
    upper = data.distances_cm + half_step
    # Each colored vertical segment is one observed bin at one actual time.
    # Width is a display stroke (points), never a fabricated time interval.
    segments = np.empty((len(data.elapsed_s), data.config.n, 2, 2))
    segments[:, :, :, 0] = data.elapsed_s[:, None, None]
    segments[:, :, 0, 1] = lower
    segments[:, :, 1, 1] = upper
    columns = LineCollection(segments.reshape(-1, 2, 2), cmap="magma", linewidths=0.8)
    columns.set_array(data.variation.ravel())
    heat.add_collection(columns)
    heat.set_ylim(lower[0], upper[-1])
    if len(data.elapsed_s) == 1:
        limits = (-0.5, 0.5)
    else:
        margin = max(float(data.elapsed_s[-1]) * .01, .001)
        limits = (-margin, float(data.elapsed_s[-1]) + margin)
    heat.set_xlim(*limits)
    heat.set_ylabel("Distance (cm)")
    heat.set_title("Discrete signal variation: |z - per-bin temporal mean| (no interpolation)")
    figure.colorbar(columns, ax=heat, label="Complex variation magnitude (scaled radar units)")
    k = data.bin_index
    for component, label, color in ((data.z[:, k].real, "I (real)", "tab:blue"),
                                     (data.z[:, k].imag, "Q (imaginary)", "tab:orange")):
        for index, section in enumerate(data.trace_segments):
            trace.plot(data.elapsed_s[section], component[section], color=color,
                       marker=".", markersize=2, linewidth=.8,
                       label=label if index == 0 else "_nolegend_")
    trace.set_xlim(*limits)
    trace.set_ylabel("I/Q (scaled radar units)")
    trace.set_title(f"Bin {k} at {data.distances_cm[k]:g} cm - raw scaled I/Q")
    trace.legend(loc="upper right")
    trace.grid(alpha=.2)
    xlabel = f"Elapsed seconds from first plotted D (board t_ms = {data.start_t_ms}; not Unix time)"
    heat.set_xlabel(xlabel)
    trace.set_xlabel(xlabel)
    q = data.quality
    long = "unavailable" if q.long_interval_events is None else str(q.long_interval_events)
    threshold = "unavailable" if q.long_interval_threshold_ms is None else f"{q.long_interval_threshold_ms:g} ms"
    summary.axis("off")
    summary.set_title("Integrity summary - entire recording, including parser issues", loc="left")
    details = (
        f"D frames: {q.total_d_frames}    Parser issues: {q.parser_issue_count}    "
        f"Estimated missing: {q.estimated_missing_frames}\n"
        f"Seq gaps: {q.sequence_gap_events}    Duplicates: {q.duplicate_sequence_events}    "
        f"Backward seq: {q.backward_sequence_events}    Suspected resets: {q.suspected_reset_events}\n"
        f"Repeated/backward timestamps: {q.repeated_timestamp_events}/{q.backward_timestamp_events}    "
        f"Long intervals: {long} (threshold: {threshold})\n"
        f"Frames without usable config: {q.frames_without_usable_configuration}    "
        f"Bin mismatches: {q.bin_count_mismatch_frames}\n"
        f"Plot: {len(data.elapsed_s)} parsed D frames; malformed rows excluded; "
        f"{len(data.trace_segments)} I/Q segment(s).\n"
        "Colors show signal variation, not breathing, displacement, or AI accuracy.")
    summary.text(0, .94, details, va="top", transform=summary.transAxes, fontsize=10, linespacing=1.65)
    figure.suptitle(shorten(title, width=100, placeholder="..."), fontsize=15)
    return figure


def render_report(capture: Capture, output, bin_index: int = 0, *,
                  force: bool = False, title: str = "Radar capture"):
    """Validate and render entirely in memory before exclusively creating a PNG.

    Existing output is protected unless force=True, including a create race.
    No directories are created, and no GUI or pyplot global figures are used.
    """
    from io import BytesIO
    from pathlib import Path

    output = Path(output)
    if output.suffix.lower() != ".png":
        raise ValueError("Output must have a .png extension")
    if output.exists() and not force:
        raise FileExistsError(f"Output already exists: {output}; use --force to overwrite")
    data = prepare_visualization(capture, bin_index)
    figure = build_figure(data, title)
    try:
        with BytesIO() as buffer:
            figure.savefig(buffer, format="png", dpi=150, metadata={"Software": "IBUKI offline analysis"})
            with output.open("wb" if force else "xb") as stream:
                stream.write(buffer.getvalue())
    finally:
        figure.clear()
    return output


def main(argv: list[str] | None = None) -> int:
    import argparse
    from pathlib import Path
    from .protocol import read_capture

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="raw D-format capture")
    parser.add_argument("--output", required=True, help="PNG path; parent directory must exist")
    parser.add_argument("--bin", type=int, default=0, dest="bin_index", help="zero-based bin index (default: 0)")
    parser.add_argument("--force", action="store_true", help="overwrite existing output")
    args = parser.parse_args(argv)
    try:
        output = render_report(read_capture(args.path), args.output, args.bin_index,
                               force=args.force, title=Path(args.path).name)
    except (OSError, ValueError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Saved radar capture report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
