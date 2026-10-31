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
    try:
        return json.loads(data, parse_constant=reject, object_pairs_hook=unique)
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
