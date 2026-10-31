"""Deterministic, auditable offline conversion of board recordings to schema v1."""
from __future__ import annotations

import base64
from bisect import bisect_right
from collections import Counter
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import tempfile

from .protocol import Capture, CaptureFrame, CaptureIssue, Data, parse_capture, parse_e_line
from .quality import analyze_quality
from .schema import (DATASET_FILES, FORMAT_NAME, INT64_MAX, INT64_MIN, SCHEMA_VERSION, SOURCE_TYPES,
                     FrameRecord, frame_schema, require_arrow)


def _json_bytes(value) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _lines(data: bytes) -> list[bytes]:
    if not data:
        return []
    chunks = re.split(b"\r\n|\r|\n", data)
    return chunks[:-1] if data.endswith((b"\r", b"\n")) else chunks


def _frame_record(session_id, frame, kind, i, q):
    cfg = frame.config
    integers = [frame.line_number, frame.data.seq, frame.data.t_ms,
                *(v for antenna in i+q for v in antenna)]
    if cfg is not None:
        integers.append(cfg.n)
    if any(not INT64_MIN <= value <= INT64_MAX for value in integers) or len(i[0]) > 2**31-1:
        raise ValueError("Frame/config integers exceed schema v1 signed int64 or bin-count int32 range; raw row retained")
    status = "missing_config" if cfg is None else "known" if cfg.n == len(i[0]) else "bin_mismatch"
    return FrameRecord(session_id, frame.line_number, kind, frame.data.seq, frame.data.t_ms,
                       len(i), len(i[0]), i, q,
                       "decimal_integer_pairs" if kind == "D" else "base64_int16_le",
                       (cfg.scale if cfg is not None else None) if kind == "D" else 1.0,
                       kind == "E" or status == "known", status, frame.config_line_number,
                       cfg.first_cm if cfg else None, cfg.step_mm if cfg else None,
                       cfg.n if cfg else None, cfg.scale if cfg else None)


def build_capture_dataset(data: bytes, *, source_name: str, session_id: str | None = None,
                          source_type: str = "unknown", scenario: str | None = None,
                          scenario_provenance: str | None = None):
    """Parse once using protocol; original line bytes accompany every event/diagnostic."""
    if source_type not in SOURCE_TYPES:
        raise ValueError("source_type must be one of: " + ", ".join(SOURCE_TYPES))
    for name, value in (("source_name", source_name), ("session_id", session_id),
                        ("scenario", scenario), ("scenario_provenance", scenario_provenance)):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{name} must be nonempty text when supplied")
    if scenario is None and scenario_provenance is not None:
        raise ValueError("scenario_provenance requires an explicitly supplied scenario")
    sha = hashlib.sha256(data).hexdigest()
    session_id = session_id if session_id is not None else "sha256:" + sha
    wire_lines = _lines(data)
    lines = [line.decode("utf-8-sig" if index == 0 else "utf-8", errors="replace")
             for index, line in enumerate(wire_lines)]
    capture = parse_capture(lines)
    frames, rows, events, diagnostics = [], [], [], []
    def event(number, event_type, origin, detail=None):
        raw = lines[number-1]
        return {"schema_version": SCHEMA_VERSION, "session_id": session_id,
                "source_line": number, "message_type": raw.strip().split(",", 1)[0].strip() or None,
                "event_type": event_type, "origin": origin, "raw": raw,
                "raw_bytes_base64": base64.b64encode(wire_lines[number-1]).decode("ascii"),
                "detail": detail or {}}
    def issue(number, category, reason):
        item = event(number, category, "ingestion_diagnostic", {"reason": reason})
        diagnostics.append(item)
        events.append(item)
    d_lines = {frame.line_number for frame in capture.frames}
    for problem in capture.issues:
        issue(problem.line_number, "configuration_mismatch" if problem.line_number in d_lines else "malformed_message",
              problem.reason)
    for frame in capture.frames:
        try:
            record = _frame_record(session_id, frame, "D", (frame.data.i,), (frame.data.q,))
        except ValueError as exc:
            issue(frame.line_number, "schema_range", str(exc))
            continue
        frames.append(frame)
        rows.append(record.to_row())
    for number, config in capture.configs:
        events.append(event(number, "configuration", "recorded_message", asdict(config)))
    config_lines = [number for number, _ in capture.configs]
    for number, message in capture.other_messages:
        if message.kind != "E":
            parts = lines[number-1].split(",", 2)
            restart = (message.kind == "S" and len(parts) == 3
                       and re.match(r"^\s*radar restart(?:\s*:|\s*$)", parts[2], re.IGNORECASE))
            events.append(event(number, "radar_restart_status" if restart else "message",
                                "explicit_board_status" if restart else "recorded_message"))
            continue
        try:
            decoded = parse_e_line(lines[number-1])
        except ValueError as exc:
            issue(number, "malformed_message", str(exc))
            continue
        source = (capture.configs[max(0, bisect_right(config_lines, number)-1)] if config_lines else (None, None))
        config_line, config = source
        frame = CaptureFrame(number, Data(decoded.seq, decoded.t_ms, decoded.i[0], decoded.q[0]), config, config_line)
        try:
            record = _frame_record(session_id, frame, "E", decoded.i, decoded.q)
        except ValueError as exc:
            issue(number, "schema_range", str(exc))
            continue
        frames.append(frame)
        rows.append(record.to_row())
        if config is not None and config.n != 24:
            issue(number, "configuration_mismatch", f"E has 24 bins; C expects {config.n}")
    frames.sort(key=lambda f: f.line_number)
    rows.sort(key=lambda row: row["source_line"])
    diagnostics.sort(key=lambda item: (item["source_line"], item["event_type"]))
    adapter = Capture(tuple(frames), capture.configs,
                      tuple((n, m) for n, m in capture.other_messages if m.kind != "E"),
                      tuple(CaptureIssue(d["source_line"], d["detail"]["reason"], d["raw"]) for d in diagnostics))
    frame_quality = analyze_quality(adapter).to_dict()
    frame_quality["total_frames"] = frame_quality.pop("total_d_frames")
    frame_quality["reported_issue_count"] = frame_quality.pop("parser_issue_count")
    pair_fields = {"sequence_gap_events": "sequence_gap", "duplicate_sequence_events": "duplicate_sequence",
                   "backward_sequence_events": "backward_sequence", "repeated_timestamp_events": "repeated_timestamp",
                   "backward_timestamp_events": "backward_timestamp"}
    for previous, current in zip(frames, frames[1:]):
        pair = analyze_quality(Capture((previous, current), (), (), ()))
        detail = {"previous_source_line": previous.line_number,
                  "sequence_delta": current.data.seq-previous.data.seq,
                  "uptime_delta_ms": current.data.t_ms-previous.data.t_ms}
        for field, name in pair_fields.items():
            if getattr(pair, field):
                events.append(event(current.line_number, name, "inferred_from_frames",
                                    {**detail, "estimated_missing_frames": pair.estimated_missing_frames}))
        threshold = frame_quality["long_interval_threshold_ms"]
        if threshold is not None and detail["sequence_delta"] > 0 and detail["uptime_delta_ms"] > threshold:
            events.append(event(current.line_number, "long_interval", "inferred_from_frames",
                                {**detail, "threshold_ms": threshold}))
    events.sort(key=lambda item: (item["source_line"], item["event_type"], item["origin"]))
    inferred = dict(sorted(Counter(e["event_type"] for e in events if e["origin"] == "inferred_from_frames").items()))
    explicit = sum(e["event_type"] == "radar_restart_status" for e in events)
    quality = {"schema_version": SCHEMA_VERSION, "diagnostic_count": len(diagnostics),
               "diagnostic_source_line_count": len({d["source_line"] for d in diagnostics}),
               "diagnostics": diagnostics, "d_quality": analyze_quality(capture).to_dict(),
               "frame_quality": frame_quality,
               "frame_quality_scope": "exported D and E in source order; E first antenna used only for bin-count/config checks",
               "explicit_restart_status_count": explicit, "inferred_anomaly_counts": inferred}
    counts = {"source_lines": len(lines), "frames": len(rows),
              "d_frames": sum(row["message_type"] == "D" for row in rows),
              "e_frames": sum(row["message_type"] == "E" for row in rows),
              "events": len(events), "diagnostics": len(diagnostics),
              "explicit_restart_statuses": explicit, "inferred_anomalies": sum(inferred.values())}
    manifest = {"schema_version": SCHEMA_VERSION, "format": FORMAT_NAME, "session": {"id": session_id},
                "input": {"name": source_name, "sha256": sha, "size_bytes": len(data)},
                "source_type": source_type,
                "source_type_provenance": "unspecified" if source_type == "unknown" else "operator_declared",
                "scenario": {"name": scenario, "provenance": scenario_provenance or
                             ("operator_declared" if scenario is not None else "unspecified"), "is_ground_truth": False},
                "capture_configuration": [{"source_line": n, **asdict(c)} for n, c in capture.configs],
                "configuration_association": "first valid C backfills preceding frames; subsequent valid C applies forward",
                "units": {"time": "milliseconds since board boot, not Unix time",
                          "D": "raw signed integers / observed C.scale; unknown without C; mismatch conversion unavailable",
                          "E": "raw signed int16 values already scaled; divisor 1, never C.scale",
                          "geometry": "only observed C; first_cm + bin*step_mm/10; null without C"},
                "events_semantics": "recorded messages/status and inferred integrity diagnostics; Y is not ground truth",
                "counts": counts}
    return rows, events, quality, manifest


def _check_target(path: Path, force: bool):
    if path.is_symlink():
        raise ValueError("Output directory must not be a symlink")
    if not path.exists():
        return
    if not path.is_dir():
        raise ValueError("Output path must be a directory")
    if not force:
        raise FileExistsError(f"Output already exists: {path}; use --force")
    children = list(path.iterdir())
    if not children:
        return
    if {p.name for p in children} != set(DATASET_FILES) or any(not p.is_file() or p.is_symlink() for p in children):
        raise ValueError("--force only replaces an empty directory or a complete managed ingestion dataset; unrelated files are protected")
    try:
        manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("Existing output has no valid managed manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_NAME or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("--force requires a recognized schema v1 ingestion dataset")


def _remove_bundle(path: Path):
    """Delete only known flat artifact files, never recursively remove user paths."""
    if path.is_symlink():
        raise ValueError("Refusing symlink cleanup")
    children = list(path.iterdir())
    if any(p.name not in DATASET_FILES or not p.is_file() or p.is_symlink() for p in children):
        raise ValueError(f"Refusing cleanup of unexpected entries in {path}")
    for child in children:
        child.unlink()
    path.rmdir()


def ingest_recording(source: str | Path, out_dir: str | Path, *, force: bool = False,
                     session_id: str | None = None, source_type: str = "unknown",
                     scenario: str | None = None, scenario_provenance: str | None = None) -> dict:
    """Build a complete staged bundle before replacing the explicitly named output.

    Source bytes are read once, never written. Force protects unrelated folders
    and any output containing the source; replacement uses a recoverable backup.
    """
    source, requested = Path(source).resolve(), Path(out_dir)
    if not source.is_file():
        raise ValueError(f"Input recording is not a file: {source}")
    if requested.is_symlink():
        raise ValueError("Output directory must not be a symlink")
    target = requested.resolve()
    if target == source or target in source.parents:
        raise ValueError("Output must not contain or replace the original recording")
    _check_target(target, force)
    pa, pq = require_arrow()
    rows, events, quality, manifest = build_capture_dataset(source.read_bytes(), source_name=source.name,
                                                           session_id=session_id, source_type=source_type,
                                                           scenario=scenario, scenario_provenance=scenario_provenance)
    table = pa.Table.from_pylist(rows, schema=frame_schema())
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=target.parent)).resolve()
    backup = stage.with_name(stage.name + ".previous")
    assert stage.parent == backup.parent == target.parent
    try:
        pq.write_table(table, stage / "frames.parquet", version="2.6", compression="NONE", use_dictionary=False,
                       row_group_size=4096, data_page_version="1.0", write_statistics=True)
        (stage / "events.jsonl").write_bytes(b"".join(
            (json.dumps(e, sort_keys=True, separators=(",", ":"), allow_nan=False)+"\n").encode("utf-8") for e in events))
        (stage / "quality.json").write_bytes(_json_bytes(quality))
        manifest["writer"] = {"pyarrow_version": pa.__version__, "parquet_version": "2.6", "compression": "NONE",
                              "use_dictionary": False, "row_group_size": 4096, "data_page_version": "1.0"}
        manifest["artifacts"] = {name: {"sha256": hashlib.sha256((stage/name).read_bytes()).hexdigest()}
                                 for name in DATASET_FILES if name != "manifest.json"}
        (stage / "manifest.json").write_bytes(_json_bytes(manifest))
        _check_target(target, force)
        if target.exists():
            target.rename(backup)
        try:
            stage.rename(target)
        except OSError:
            if backup.exists() and not target.exists():
                backup.rename(target)
            raise
        if backup.exists():
            _remove_bundle(backup)
    finally:
        if stage.exists():
            _remove_bundle(stage)
    return manifest


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--session-id")
    parser.add_argument("--source-type", choices=SOURCE_TYPES, default="unknown")
    parser.add_argument("--scenario")
    parser.add_argument("--scenario-provenance")
    args = parser.parse_args(argv)
    try:
        manifest = ingest_recording(args.source, args.out_dir, force=args.force, session_id=args.session_id,
                                    source_type=args.source_type, scenario=args.scenario,
                                    scenario_provenance=args.scenario_provenance)
    except (OSError, ValueError, ImportError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Saved canonical dataset: {args.out_dir} ({manifest['counts']['frames']} frames, "
          f"{manifest['counts']['events']} events, {manifest['counts']['diagnostics']} diagnostics)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
