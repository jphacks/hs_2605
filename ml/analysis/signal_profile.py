"""Descriptive per-bin radar variation from verified canonical datasets only."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

import numpy as np

from .report import ReportDataset, load_dataset, prepare_blocks
from .visualize import variation_magnitude

PROFILE_VERSION = 1


def _bins(indices):
    if indices is None:
        return None
    try:
        values = tuple(indices)
    except TypeError as exc:
        raise ValueError("Bin indices must be distinct nonnegative integers") from exc
    if (not values or any(type(k) is not int or k < 0 for k in values)
            or len(set(values)) != len(values)):
        raise ValueError("Bin indices must be distinct nonnegative integers")
    return tuple(sorted(values))


def build_profile(dataset: ReportDataset, bin_indices=None) -> dict:
    """Profile independent continuous trace segments; antenna 0, no CNN features.

    Block preparation supplies scale/geometry and all discontinuity boundaries.
    Means are recomputed within each trace segment, never across its gaps.
    """
    selected = _bins(bin_indices)
    blocks, offset = [], 0
    for parent_index, parent in enumerate(prepare_blocks(dataset, 0), 1):
        count = parent["frame_count"]
        rows = dataset.frames[offset:offset+count]
        data = parent["data"]
        if data is not None and selected is not None and max(selected) >= data.config.n:
            raise ValueError(f"Bin {max(selected)} is outside parent block {parent_index}'s "
                             f"0..{data.config.n-1} range; omit --bin to profile all bins in each block")
        sections = data.trace_segments if data is not None else (slice(0, count),)
        for segment_index, section in enumerate(sections, 1):
            observed = rows[section]
            first, last = observed[0], observed[-1]
            entry = {
                "block_index": len(blocks)+1, "parent_block_index": parent_index,
                "trace_segment_index": segment_index if data is not None else None,
                "frame_row_start": offset+section.start,
                "frame_row_end_exclusive": offset+section.stop,
                "source_lines": [r["source_line"] for r in observed],
                "sequence_start": first["seq"], "sequence_end": last["seq"],
                "board_uptime_start_ms": first["board_uptime_ms"],
                "board_uptime_end_ms": last["board_uptime_ms"],
                "sample_count": len(observed), "message_type": first["message_type"],
                "selected_antenna": 0, "recorded_antenna_count": first["antenna_count"],
                "observed_bin_count": first["bin_count"],
                "configuration_status": first["configuration_status"],
                "config_source_lines": sorted({r["config_source_line"] for r in observed
                                               if r["config_source_line"] is not None}),
                "geometry": {k: first[k] for k in ("first_cm", "step_mm", "config_bin_count")},
                "recorded_config_scale": first["config_scale"],
                "value_divisor": first["value_divisor"],
                "status": "unavailable", "unavailable_reason": parent["reason"],
                "bin_statistics": None,
            }
            if data is not None:
                magnitude = variation_magnitude(data.z[section])
                if not np.isfinite(magnitude).all():
                    entry["unavailable_reason"] = "Non-finite segment variation; no statistics exported"
                else:
                    # Rescale only for numerical stability, not signal normalization.
                    # Multiplication by the same per-bin max restores value units.
                    scale = magnitude.max(axis=0)
                    normalized = np.divide(magnitude, scale, out=np.zeros_like(magnitude), where=scale != 0)
                    med = np.median(normalized, axis=0)*scale
                    p95 = np.quantile(normalized, .95, axis=0, method="linear")*scale
                    rms = np.sqrt(np.mean(normalized**2, axis=0))*scale
                    indices = selected if selected is not None else range(data.config.n)
                    entry.update(status="available", unavailable_reason=None, bin_statistics=[{
                        "bin_index": k, "distance_cm": float(data.distances_cm[k]),
                        "sample_count": len(observed), "median_variation_magnitude": float(med[k]),
                        "p95_variation_magnitude": float(p95[k]), "rms_variation_magnitude": float(rms[k]),
                    } for k in indices])
            blocks.append(entry)
        offset += count
    m = dataset.manifest
    analyzed = sum(b["sample_count"] for b in blocks if b["status"] == "available")
    return {
        "format": "ibuki.radar.signal_profile", "profile_version": PROFILE_VERSION,
        "dataset_schema_version": m["schema_version"], "selected_antenna": 0,
        "requested_bins": list(selected) if selected is not None else None,
        "provenance": deepcopy({k: m[k] for k in
                                ("session", "source_type", "source_type_provenance", "scenario", "input",
                                 "capture_configuration")}),
        "verified_artifact_hashes": deepcopy(m["artifacts"]),
        "dataset_writer": deepcopy(m.get("writer")), "numpy_version": np.__version__,
        "data_quality": deepcopy(dataset.quality),
        "coverage": {"exported_frames": len(dataset.frames), "analyzed_frames": analyzed,
                     "unavailable_frames": len(dataset.frames)-analyzed,
                     "analysis_block_count": len(blocks),
                     "available_block_count": sum(b["status"] == "available" for b in blocks),
                     "unavailable_block_count": sum(b["status"] == "unavailable" for b in blocks),
                     "malformed_or_configuration_diagnostics": dataset.quality["diagnostic_count"]},
        "unavailable_reason": "No exported frames" if not blocks else None,
        "assumptions": {
            "units": "scaled radar value units, not displacement or calibrated physical units",
            "scaling": "D: integers / observed C.scale; E: already-scaled int16, divisor 1; antenna 0 only",
            "variation": "v[j,k] = abs(z[j,k] - mean(z[:,k])) within each continuous analysis block",
            "median": "median(v[:,k]); equal weight per observed sample",
            "p95": "linear empirical quantile: h=(N-1)*0.95; interpolate adjacent sorted observed magnitudes",
            "rms": "sqrt(mean(v[:,k]**2)); per-bin maximum rescaling avoids intermediate overflow",
            "segmentation": "report.prepare_blocks then visualize trace_segments; no aggregation across boundaries",
            "boundaries": "resets/repeated sequence or time, explicit S restart, D/E/config transitions, gaps and diagnostics",
            "time": "board uptime milliseconds, not Unix time; no assumed sampling rate",
            "weighting": "sample weighted, not time weighted; irregular positive intervals can remain within a segment",
            "missing": "no filling, resampling or interpolation of measurements; unavailable blocks retain source lines",
            "geometry": "observed compatible C only; retain ingestion's delayed-first-C assumption",
            "singleton": "one-sample centered variation is zero by construction, not evidence of no movement",
            "limits": "descriptive only; not breathing accuracy, SNR improvement, medical validation or model performance",
        },
        "blocks": blocks,
    }


def write_profile(directory, output, bin_indices=None, *, force=False):
    """Create a deterministic JSON file outside the read-only canonical bundle."""
    root = Path(directory).resolve()
    requested = Path(output)
    if requested.is_symlink():
        raise ValueError("Profile output must not be a symlink")
    target = requested.resolve()
    if target.suffix.lower() != ".json":
        raise ValueError("Profile output must have a .json extension")
    if target == root or root in target.parents:
        raise ValueError("Profile output must be outside the canonical dataset directory")
    if target.exists() and not force:
        raise FileExistsError(f"Output already exists: {target}; use --force")
    profile = build_profile(load_dataset(root), bin_indices)
    content = (json.dumps(profile, sort_keys=True, indent=2, allow_nan=False)+"\n").encode("utf-8")
    if not force:
        with target.open("xb") as stream:
            stream.write(content)
    else:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".ibuki-profile-", delete=False) as stream:
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
    return profile


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", help="canonical schema-v1 directory, never CSV")
    parser.add_argument("--output", required=True, help="separate JSON path; parent must exist")
    parser.add_argument("--bin", type=int, action="append", dest="bin_indices", help="repeat to select bins; default all")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = write_profile(args.dataset, args.output, args.bin_indices, force=args.force)
    except (OSError, ValueError, ImportError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Saved signal profile: {args.output} ({result['coverage']['analyzed_frames']} analyzed frames)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
