"""Validate schema-v1 artifacts and generate a standalone offline HTML report."""
from __future__ import annotations

import base64
from bisect import bisect_right
from dataclasses import dataclass, fields
import hashlib
import json
import math
from pathlib import Path

from .quality import QualityReport
from .schema import DATASET_FILES, FORMAT_NAME, SCHEMA_VERSION, SOURCE_TYPES, frame_schema, require_arrow


@dataclass(frozen=True)
class ReportDataset:
    path: Path
    manifest: dict
    frames: tuple[dict, ...]
    events: tuple[dict, ...]
    quality: dict


def _require(condition, reason):
    if not condition:
        raise ValueError("Invalid canonical dataset: " + reason)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _number(value, minimum=0, *, positive=False):
    return (type(value) in (int, float) and math.isfinite(value)
            and (value > minimum if positive else value >= minimum))


def _json(data, name):
    def reject(value):
        raise ValueError(f"{name}: non-finite JSON number {value}")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"{name}: duplicate JSON key {key}")
            result[key] = value
        return result
    def finite(value):
        result = float(value)
        if not math.isfinite(result):
            reject(value)
        return result
    try:
        return json.loads(data, parse_constant=reject, parse_float=finite, object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid {name}: {exc}") from exc


def load_dataset(directory: str | Path) -> ReportDataset:
    """Read each managed artifact once; validate hashes before interpreting data.

    Quality metrics remain authoritative from quality.json, never recomputed.
    Hashes establish artifact consistency, not the authenticity of provenance.
    """
    root = Path(directory).resolve()
    _require(root.is_dir(), f"input must be a canonical dataset directory: {root}")
    content = {}
    for name in DATASET_FILES:
        path = root/name
        _require(path.is_file() and not path.is_symlink(), f"missing or unsafe artifact: {name}")
        content[name] = path.read_bytes()
    manifest = _json(content["manifest.json"], "manifest.json")
    _require(isinstance(manifest, dict), "manifest must be an object")
    _require(manifest.get("format") == FORMAT_NAME, "unsupported manifest format")
    _require(type(manifest.get("schema_version")) is int and manifest["schema_version"] == SCHEMA_VERSION,
             "unsupported schema_version (expected 1)")
    _require(isinstance(manifest.get("artifacts"), dict), "missing artifact hashes")
    for name in DATASET_FILES[1:]:
        entry = manifest["artifacts"].get(name, {})
        _require(isinstance(entry, dict) and entry.get("sha256") == hashlib.sha256(content[name]).hexdigest(),
                 f"SHA-256 mismatch or missing hash: {name}")
    session = manifest.get("session")
    _require(isinstance(session, dict) and isinstance(session.get("id"), str) and bool(session["id"].strip()),
             "missing session ID")
    source = manifest.get("source_type")
    _require(source in SOURCE_TYPES, "unsupported source_type")
    _require(manifest.get("source_type_provenance") == ("unspecified" if source == "unknown" else "operator_declared"),
             "invalid source_type_provenance")
    scenario, original = manifest.get("scenario"), manifest.get("input")
    _require(isinstance(scenario, dict) and scenario.get("is_ground_truth") is False
             and isinstance(scenario.get("provenance"), str)
             and (scenario.get("name") is None or isinstance(scenario["name"], str)), "invalid scenario metadata")
    _require(isinstance(original, dict) and isinstance(original.get("name"), str)
             and isinstance(original.get("sha256"), str) and len(original["sha256"]) == 64
             and all(c in "0123456789abcdef" for c in original["sha256"])
             and _integer(original.get("size_bytes")), "invalid original input metadata")
    counts = manifest.get("counts")
    count_names = ("source_lines", "frames", "d_frames", "e_frames", "events", "diagnostics",
                   "explicit_restart_statuses", "inferred_anomalies")
    _require(isinstance(counts, dict) and all(_integer(counts.get(k)) for k in count_names), "invalid manifest counts")
    configs = manifest.get("capture_configuration")
    _require(isinstance(configs, list), "capture_configuration must be a list")
    previous = 0
    for config in configs:
        _require(isinstance(config, dict) and _integer(config.get("source_line"), previous+1)
                 and config["source_line"] <= counts["source_lines"]
                 and _number(config.get("first_cm")) and _number(config.get("step_mm"), positive=True)
                 and _integer(config.get("n"), 1) and _number(config.get("scale"), positive=True), "invalid observed C configuration")
        previous = config["source_line"]
    pa, pq = require_arrow()
    try:
        table = pq.read_table(pa.BufferReader(content["frames.parquet"]))
    except Exception as exc:
        raise ValueError(f"Invalid frames.parquet: {exc}") from exc
    # Parquet normalizes nested list child names from 'item' to 'element'.
    expected_schema = frame_schema()
    _require(table.schema.equals(expected_schema) and table.schema.metadata == expected_schema.metadata,
             "Parquet schema does not match schema v1")
    rows = table.to_pylist()
    config_lines = [c["source_line"] for c in configs]
    previous = 0
    for row in rows:
        _require(row["schema_version"] == SCHEMA_VERSION and row["session_id"] == session["id"], "frame schema/session mismatch")
        _require(_integer(row["source_line"], previous+1) and row["source_line"] <= counts["source_lines"], "invalid frame source order/line")
        previous = row["source_line"]
        kind = row["message_type"]
        _require(kind in ("D", "E") and _integer(row["seq"]) and _integer(row["board_uptime_ms"]), "invalid frame type/sequence/uptime")
        _require(row["antenna_count"] == (1 if kind == "D" else 3) and _integer(row["bin_count"], 1)
                 and (kind == "D" or row["bin_count"] == 24), "invalid antenna/bin dimensions")
        for key in ("raw_i", "raw_q"):
            matrix = row[key]
            _require(isinstance(matrix, list) and len(matrix) == row["antenna_count"], f"invalid {key} antenna dimensions")
            _require(all(isinstance(a, list) and len(a) == row["bin_count"]
                         and all(type(v) is int and (kind == "D" or -32768 <= v <= 32767) for v in a)
                         for a in matrix), f"invalid {key} bin dimensions/integers")
        cfg = configs[max(0, bisect_right(config_lines, row["source_line"])-1)] if configs else None
        for key, value in (("config_source_line", cfg["source_line"] if cfg else None),
                           ("first_cm", cfg["first_cm"] if cfg else None),
                           ("step_mm", cfg["step_mm"] if cfg else None),
                           ("config_bin_count", cfg["n"] if cfg else None),
                           ("config_scale", cfg["scale"] if cfg else None)):
            _require(row[key] == value, f"frame {key} contradicts observed configuration")
        status = "missing_config" if cfg is None else "known" if cfg["n"] == row["bin_count"] else "bin_mismatch"
        _require(row["configuration_status"] == status, "frame configuration_status mismatch")
        _require(row["wire_encoding"] == ("decimal_integer_pairs" if kind == "D" else "base64_int16_le")
                 and row["value_divisor"] == ((cfg["scale"] if cfg else None) if kind == "D" else 1)
                 and row["scaled_values_available"] is (kind == "E" or status == "known"), "frame unit/scale semantics mismatch")
    events = [_json(line, f"events.jsonl line {k}") for k, line in enumerate(content["events.jsonl"].splitlines(), 1)]
    for event in events:
        _require(isinstance(event, dict) and type(event.get("schema_version")) is int
                 and event["schema_version"] == SCHEMA_VERSION and event.get("session_id") == session["id"]
                 and _integer(event.get("source_line"), 1) and event["source_line"] <= counts["source_lines"], "invalid event identity/source line")
        _require(all(isinstance(event.get(k), str) for k in ("event_type", "origin", "raw", "raw_bytes_base64"))
                 and isinstance(event.get("detail"), dict), "invalid event fields")
        _require(event["origin"] in ("recorded_message", "explicit_board_status", "inferred_from_frames", "ingestion_diagnostic"), "unknown event origin")
        try:
            raw = base64.b64decode(event["raw_bytes_base64"], validate=True).decode(
                "utf-8-sig" if event["source_line"] == 1 else "utf-8", errors="replace")
        except ValueError as exc:
            raise ValueError("Invalid canonical dataset: invalid event raw bytes") from exc
        _require(raw == event["raw"] and event.get("message_type") == (raw.strip().split(",", 1)[0].strip() or None), "event raw payload mismatch")
        _require((event["event_type"] == "radar_restart_status") == (event["origin"] == "explicit_board_status"), "explicit restart event origin mismatch")
    quality = _json(content["quality.json"], "quality.json")
    _require(isinstance(quality, dict) and type(quality.get("schema_version")) is int
             and quality["schema_version"] == SCHEMA_VERSION, "invalid quality schema")
    metrics = quality.get("frame_quality")
    metric_names = {f.name for f in fields(QualityReport)} - {"total_d_frames", "parser_issue_count"}
    metric_names |= {"total_frames", "reported_issue_count"}
    _require(isinstance(metrics, dict) and metric_names <= metrics.keys(), "missing frame_quality fields")
    nullable = {"typical_sampling_interval_ms", "long_interval_threshold_ms", "long_interval_events"}
    for key in metric_names - {"unparsed_message_kind_counts"}:
        value = metrics[key]
        floating = key in {"typical_sampling_interval_ms", "long_interval_multiplier", "long_interval_threshold_ms"}
        _require((value is None and key in nullable) or (_number(value) if floating else _integer(value)),
                 f"invalid quality metric: {key}")
    diagnostics = [e for e in events if e["origin"] == "ingestion_diagnostic"]
    explicit = sum(e["origin"] == "explicit_board_status" for e in events)
    inferred = {}
    for event in events:
        if event["origin"] == "inferred_from_frames":
            inferred[event["event_type"]] = inferred.get(event["event_type"], 0)+1
    _require(quality.get("diagnostics") == diagnostics and quality.get("diagnostic_count") == len(diagnostics)
             and quality.get("diagnostic_source_line_count") == len({e["source_line"] for e in diagnostics})
             and quality.get("explicit_restart_status_count") == explicit
             and quality.get("inferred_anomaly_counts") == inferred, "quality/events diagnostics or status counts disagree")
    expected = {"frames": len(rows), "d_frames": sum(r["message_type"] == "D" for r in rows),
                "e_frames": sum(r["message_type"] == "E" for r in rows), "events": len(events),
                "diagnostics": len(diagnostics), "explicit_restart_statuses": explicit,
                "inferred_anomalies": sum(inferred.values())}
    _require(all(counts[k] == v for k, v in expected.items()) and metrics["total_frames"] == len(rows)
             and metrics["reported_issue_count"] == len(diagnostics), "manifest/quality/frame counts disagree")
    return ReportDataset(root, manifest, tuple(rows), tuple(events), quality)


def prepare_blocks(dataset: ReportDataset, bin_index: int):
    """Reuse existing scaled I/Q and variation logic on homogeneous ordered blocks.

    E antenna 0 uses an adapter scale of 1; canonical configuration stays intact.
    Resets, repeated seq/time, explicit restart status, config and D/E transitions
    split blocks. Existing visualization splits traces across gaps/diagnostics.
    """
    from .protocol import Capture, CaptureFrame, CaptureIssue, Config, Data
    from .visualize import prepare_visualization

    if type(bin_index) is not int or bin_index < 0:
        raise ValueError("Bin index must be a nonnegative integer")
    restarts = [e["source_line"] for e in dataset.events if e["origin"] == "explicit_board_status"]
    issues = tuple(CaptureIssue(d["source_line"], d["detail"].get("reason", "diagnostic"), d["raw"])
                   for d in dataset.quality["diagnostics"])
    groups = []
    def signature(row):
        return tuple(row[k] for k in ("message_type", "configuration_status", "first_cm", "step_mm",
                                      "config_bin_count", "config_scale"))
    for row in dataset.frames:
        previous = groups[-1][-1] if groups else None
        boundary = previous is None or (signature(row) != signature(previous)
            or row["seq"] <= previous["seq"] or row["board_uptime_ms"] <= previous["board_uptime_ms"]
            or any(previous["source_line"] < line < row["source_line"] for line in restarts))
        if boundary:
            groups.append([])
        groups[-1].append(row)
    results = []
    for group in groups:
        first = group[0]
        result = {"first_line": first["source_line"], "last_line": group[-1]["source_line"],
                  "kind": first["message_type"], "frame_count": len(group), "data": None, "reason": None}
        if first["configuration_status"] != "known":
            result["reason"] = "Missing or incompatible C geometry: scaled range-time and distance-labeled I/Q plots unavailable."
        else:
            config = Config(first["first_cm"], first["step_mm"], first["config_bin_count"],
                            first["config_scale"] if first["message_type"] == "D" else 1)
            frames = tuple(CaptureFrame(r["source_line"], Data(r["seq"], r["board_uptime_ms"],
                                tuple(r["raw_i"][0]), tuple(r["raw_q"][0])), config, r["config_source_line"])
                           for r in group)
            capture = Capture(frames, ((first["config_source_line"], config),), (), issues)
            try:
                result["data"] = prepare_visualization(capture, bin_index)
            except (ValueError, OverflowError) as exc:
                result["reason"] = str(exc)
        results.append(result)
    return results


def _png(figure):
    from io import BytesIO
    try:
        with BytesIO() as buffer:
            figure.savefig(buffer, format="png", dpi=120, metadata={"Software": "IBUKI offline analysis"})
            return base64.b64encode(buffer.getvalue()).decode("ascii")
    finally:
        figure.clear()


def _timeline(dataset):
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(10, 3.5), layout="constrained")
    FigureCanvasAgg(figure)
    ax = figure.subplots()
    times = {r["source_line"]: r["board_uptime_ms"] for r in dataset.frames}
    origin = dataset.frames[0]["board_uptime_ms"] if dataset.frames else 0
    if times:
        ax.scatter(list(times), [(t-origin)/1000 for t in times.values()], s=7, color="#1f6f94", label="Observed frames")
    for label, marker, color, event_origin in (("Explicit board restart status", "x", "#bf3b43", "explicit_board_status"),
                                               ("Inferred integrity anomaly", "o", "#b27600", "inferred_from_frames")):
        locations = []
        for event in dataset.events:
            if event["origin"] != event_origin:
                continue
            uptime = event_uptime(event, times)
            if uptime is not None:
                locations.append((event["source_line"], (uptime-origin)/1000))
        if locations:
            ax.scatter(*zip(*locations), marker=marker, color=color, s=40, label=label, zorder=3)
    ax.set_xlabel("Physical source line (recording order)")
    ax.set_ylabel("Observed uptime offset (s)")
    ax.set_title(f"Sampling timeline; offset from board uptime {origin} ms (not Unix time)")
    ax.grid(alpha=.2)
    if ax.collections:
        ax.legend(fontsize=9)
    else:
        ax.text(.5, .5, "No observed frame/status timestamps", ha="center", transform=ax.transAxes)
    return _png(figure)


def event_uptime(event, frame_times):
    """Use an actual frame timestamp or explicit S wire timestamp; never estimate."""
    if event["origin"] == "inferred_from_frames":
        return frame_times.get(event["source_line"])
    if event["origin"] == "explicit_board_status":
        parts = event["raw"].split(",", 2)
        value = parts[1].strip() if len(parts) > 1 else ""
        if len(value) <= 19 and value.isascii() and value.isdecimal() and int(value) <= 2**63-1:
            return int(value)
    return None


def build_html(dataset: ReportDataset, bin_index: int = 0) -> str:
    """Render verified artifacts with no network resources, scripts or timestamps."""
    from html import escape
    from .visualize import build_figure

    def text(value):
        if value is None:
            return "unavailable"
        if isinstance(value, (dict, list)):
            value = json.dumps(value, sort_keys=True, ensure_ascii=True, allow_nan=False)
        return escape(str(value), quote=True)
    def table(pairs):
        return '<div class="table-scroll"><table><tbody>'+''.join(
            f'<tr><th scope="row">{text(k)}</th><td>{text(v)}</td></tr>' for k, v in pairs)+'</tbody></table></div>'
    def image(encoded, alt):
        return f'<div class="plot"><img alt="{text(alt)}" src="data:image/png;base64,{encoded}"></div>'
    m, q = dataset.manifest, dataset.quality
    blocks = prepare_blocks(dataset, bin_index)
    counts, metrics = m["counts"], q["frame_quality"]
    exported_lines = {r["source_line"] for r in dataset.frames}
    rejected = len({d["source_line"] for d in q["diagnostics"]
                    if d["message_type"] in ("D", "E") and d["source_line"] not in exported_lines})
    overview = table([
        ("Session ID", m["session"]["id"]), ("Source type", m["source_type"]),
        ("Source provenance", m["source_type_provenance"]), ("Scenario", m["scenario"]["name"]),
        ("Scenario provenance", m["scenario"]["provenance"]), ("Ground truth", "Not established"),
        ("Original filename", m["input"]["name"]), ("Input SHA-256", m["input"]["sha256"]),
        ("Schema version", m["schema_version"]), ("Exported raw frames", counts["frames"]),
        ("D / E frames", f'{counts["d_frames"]} / {counts["e_frames"]}'), ("Events", counts["events"])])
    quality = table([("Exported raw frames (including incompatible config)", counts["frames"]),
                     ("Rejected D/E source rows (quality diagnostics, not missing-frame estimate)", rejected),
                     ("All row diagnostics (D/C/E/blank/config)", q["diagnostic_count"]),
                     ("Explicit board restart status messages", q["explicit_restart_status_count"]),
                     ("Inferred integrity anomaly counts", q["inferred_anomaly_counts"])]
                    + [(k.replace("_", " "), v) for k, v in sorted(metrics.items())])
    plots = []
    for number, block in enumerate(blocks, 1):
        label = f'Block {number} · {block["kind"]} · source lines {block["first_line"]}–{block["last_line"]} · {block["frame_count"]} frames'
        plots.append(f'<article><h3>{text(label)}</h3>')
        if block["data"] is None:
            plots.append(f'<p class="notice">Plot unavailable: {text(block["reason"])}</p>')
        else:
            data = block["data"]
            figure = build_figure(data, f'{block["kind"]} antenna 0 · bin {bin_index} · {data.distances_cm[bin_index]:g} cm',
                                  include_summary=False, frame_kind=block["kind"])
            figure.set_size_inches(10, 7)
            plots.append(image(_png(figure), f'{label}: range-time variation and selected-bin I/Q'))
            plots.append(f'<p class="caption">Bin {bin_index} at {data.distances_cm[bin_index]:g} cm; '
                         f'{len(data.trace_segments)} disconnected I/Q trace segment(s). Antenna 0 only. '
                         + ('D integers divided by observed C.scale.' if block["kind"] == 'D' else
                            'E int16 values already scaled; C.scale is not applied.')+'</p>')
        plots.append('</article>')
    if not blocks:
        plots.append('<p class="notice">No exported frames. Range-time and I/Q plots unavailable.</p>')
    frame_times = {r["source_line"]: r["board_uptime_ms"] for r in dataset.frames}
    event_rows = ''.join('<tr>'+''.join(f'<td>{text(v)}</td>' for v in (
        e["source_line"], event_uptime(e, frame_times), e["event_type"], e["origin"], e["raw"], e["detail"]))+'</tr>'
                        for e in dataset.events)
    return '''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>IBUKI · Offline radar analysis</title><style>
:root{font-family:system-ui,sans-serif;color:#203142;background:#edf2f6;line-height:1.55}
*{box-sizing:border-box}body{margin:0}main{max-width:1120px;margin:auto;padding:32px 24px 60px}
header{padding:26px 0}h1{font-size:clamp(1.8rem,4vw,2.8rem);line-height:1.15;margin:10px 0}
h2{font-size:1.35rem;margin:0 0 16px}h3{font-size:1.05rem}.eyebrow{color:#236384;font-weight:700;letter-spacing:.1em}
section{background:white;border:1px solid #d6e0e8;border-radius:12px;padding:24px;margin:18px 0}
table{border-collapse:collapse;width:100%;font-size:.9rem}th,td{padding:10px 12px;border-bottom:1px solid #e0e7ed;text-align:left;vertical-align:top;overflow-wrap:anywhere}
th{color:#415d71}tbody th{width:33%}thead{background:#edf3f7}.table-scroll{overflow-x:auto}.events{min-width:800px}.events td:nth-child(5){white-space:pre-wrap}
.plot{overflow-x:auto}.plot img{display:block;width:100%;min-width:720px}.caption{font-size:.88rem;color:#496275}
.notice{background:#fff5dd;border-left:4px solid #b47b16;padding:12px 16px}.badge{display:inline-block;background:#dcecf5;border-radius:20px;padding:5px 12px;font-size:.85rem}
article+article{border-top:1px solid #d6e0e8;margin-top:24px;padding-top:16px}code{overflow-wrap:anywhere}
@media(max-width:600px){main{padding:12px 12px 36px}section{padding:16px}th,td{padding:8px}tbody th{width:40%}}
</style></head><body><main><header><div class="eyebrow">IBUKI / ENGINEERING ANALYSIS</div>
<h1>Offline radar capture report</h1><span class="badge">Canonical schema v1 · Verified artifact hashes</span>
<p>Recorded observations and integrity diagnostics. No classification or medical-performance evaluation.</p></header>
<section><h2>A. Dataset overview</h2>'''+overview+'''<p class="caption">Provenance is declared metadata, not independently verified authenticity. The original CSV is never read by this report.</p></section>
<section><h2>B. Data quality</h2><p>Authoritative source: <code>quality.json</code> (combined exported D/E frame quality). Explicit board status and inferred anomalies remain separate.</p>'''+quality+'''
<p class="caption">Rejected-row counts use quality diagnostics and exclude exported bin-mismatch frames. Configuration diagnostics are not additional lost frames. Sequence-based missing counts are estimates.</p></section>
<section><h2>C. Range-time heatmap</h2><p>Color shows |z − per-bin temporal mean| within each homogeneous block, reusing the existing visualization. Vertical strokes represent actual observations, not filled time intervals. Variation does not prove breathing.</p>
<h2>D. Selected-bin I/Q waveform</h2><p>Scaled I and Q at the selected zero-based bin. Explicit restart status, repeated/backward sequence or uptime, configuration changes and D/E transitions split blocks. Gaps and malformed rows break trace connections. No samples are interpolated. Charts scroll horizontally on narrow screens.</p>'''+''.join(plots)+'''
</section><section><h2>E. Sampling timeline and source events</h2><p>Source-line order versus actual observed uptime offset; points are never connected across a reset. Marker timestamps come only from frames or explicit S payloads. Unavailable timestamps remain unavailable.</p>'''+image(_timeline(dataset), "Observed board uptime and explicit/inferred event timeline")+'''
<div class="table-scroll"><table class="events"><thead><tr><th>Source line</th><th>Board uptime (ms)</th><th>Event</th><th>Origin</th><th>Raw message</th><th>Detail</th></tr></thead><tbody>'''+event_rows+'''</tbody></table></div></section>
<section><h2>F. Interpretation and limitations</h2><p>This is an offline engineering report. Radar variation is not labeled breathing accuracy, apnea-event detection, alarm performance or medical-device validation. No infant-safety or sensor-accuracy claims are made.</p>
<p>Board timestamps are uptime, not Unix timestamps. First-valid-C backfill is an explicit ingestion assumption, not proof of earlier settings. Missing or incompatible geometry prevents distance plots. E plots use antenna 0; other antennas remain in Parquet. Separate blocks have separate means and color scales, so colors across blocks are not directly comparable.</p>
<p>Artifact hashes detect changes relative to the manifest, not malicious replacement of the entire dataset. Original input SHA-256 is displayed without accessing or rehashing the original CSV. Reports are deterministic within the same Matplotlib/font/NumPy/PyArrow environment; no wall-clock generation timestamp is embedded.</p></section>
</main></body></html>'''


def render_report(directory, output, bin_index=0, *, force=False):
    """Write a separate HTML file; managed datasets and recordings stay read-only."""
    import os
    import tempfile

    dataset = load_dataset(directory)
    requested = Path(output)
    if requested.is_symlink():
        raise ValueError("Report output must not be a symlink")
    target = requested.resolve()
    if target.suffix.lower() != ".html":
        raise ValueError("Report output must have a .html extension")
    if target == dataset.path or dataset.path in target.parents:
        raise ValueError("Report output must be outside the managed canonical dataset directory")
    if target.exists() and not force:
        raise FileExistsError(f"Output already exists: {target}; use --force")
    content = build_html(dataset, bin_index).encode("utf-8")
    if not force:
        with target.open("xb") as stream:
            stream.write(content)
    else:
        # Replace the directory entry, not a possible hard-linked recording.
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".ibuki-report-", delete=False) as stream:
            stage = Path(stream.name)
            try:
                stream.write(content)
            except OSError:
                stream.close()
                stage.unlink()
                raise
        try:
            os.replace(stage, target)
        finally:
            if stage.exists():
                stage.unlink()
    return target


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="schema-v1 canonical dataset directory, not CSV")
    parser.add_argument("--output", required=True, help="separate .html path; parent must exist")
    parser.add_argument("--bin", type=int, default=0, dest="bin_index")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        output = render_report(args.dataset, args.output, args.bin_index, force=args.force)
    except (OSError, ValueError, ImportError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Saved offline HTML report: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
