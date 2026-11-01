"""Generate a self-contained, synthetic-only spatial inspection page."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .report import _json as read_json, load_dataset
from .spatial_sim import evaluate


def load_case(directory: str | Path) -> dict:
    """Validate canonical inputs and reuse the existing spatial evaluator.

    A saved evaluation is checked against a fresh evaluation with its original
    parameters; it is never trusted as an independent source of positions.
    Hashes establish consistency, not authenticity or hardware calibration.
    """
    root = Path(directory).resolve()
    if not root.is_dir():
        raise ValueError("Input must be a spatial_sim case directory; generate one with --scenario sweep")
    truth_path = root / "truth.json"
    if not truth_path.is_file() or truth_path.is_symlink():
        raise ValueError("Missing or unsafe truth.json; use a spatial_sim case_00 directory")
    truth_bytes = truth_path.read_bytes()
    truth = read_json(truth_bytes, "truth.json")
    dataset = load_dataset(root / "canonical")
    if dataset.manifest["source_type"] != "synthetic":
        raise ValueError("Explorer requires source_type='synthetic'; physical/unknown captures are not supported")
    if not dataset.frames:
        raise ValueError("No E frames to inspect; generate a nonempty spatial_sim case")
    if any(r["message_type"] != "E" or r["antenna_count"] != 3 or r["bin_count"] != 24
           or r["configuration_status"] != "known" or r["first_cm"] != 15
           or r["step_mm"] != 37.5 or r["config_bin_count"] != 24 for r in dataset.frames):
        raise ValueError("Require E frames with three antennas, 24 bins and known 15 cm / 37.5 mm geometry; regenerate the case")
    if not isinstance(truth, dict) or truth.get("antenna_y_wavelengths") != [0, 0, 0]:
        raise ValueError("Require spatial_sim truth with the declared horizontal collinear array geometry")

    saved_path = root / "evaluation.json"
    parameters = {}
    saved = None
    saved_bytes = None
    if saved_path.exists() or saved_path.is_symlink():
        if not saved_path.is_file() or saved_path.is_symlink():
            raise ValueError("Unsafe evaluation.json; use an unmodified spatial_sim case")
        saved_bytes = saved_path.read_bytes()
        saved = read_json(saved_bytes, "evaluation.json")
        if not isinstance(saved, dict) or not isinstance(saved.get("parameters"), dict):
            raise ValueError("Invalid spatial_sim evaluation.json; regenerate the simulation case")
        keys = ("window_frames", "hop_frames", "min_movement", "min_coherence")
        if any(k not in saved["parameters"] for k in keys):
            raise ValueError("Missing saved evaluation parameters; regenerate the simulation case")
        parameters = {k: saved["parameters"][k] for k in keys}
        # bools/strings/objects must fail before entering numerical code.
        for key in ("min_movement", "min_coherence"):
            if type(parameters[key]) not in (int, float):
                raise ValueError(f"Invalid saved parameter: {key}")
    result = evaluate(dataset, truth, **parameters)
    if saved is not None and saved != result:
        raise ValueError("evaluation.json differs from canonical/truth evaluation; regenerate the case in this environment")
    result["explorer"] = dict(
        version=1, truth_sha256=hashlib.sha256(truth_bytes).hexdigest(),
        manifest_sha256=hashlib.sha256((dataset.path / "manifest.json").read_bytes()).hexdigest(),
        saved_evaluation_sha256=hashlib.sha256(saved_bytes).hexdigest() if saved_bytes is not None else None,
        evaluation_source="verified_saved_parameters" if saved is not None else "spatial_sim_defaults",
        frame_count=len(dataset.frames), event_count=len(dataset.events),
    )
    return result


def build_html(result: dict) -> str:
    """Embed data as inert JSON, including safe script-boundary characters."""
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    for char, replacement in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                              ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
        encoded = encoded.replace(char, replacement)
    return _PAGE.replace("__IBUKI_DATA__", encoded)


def render_explorer(directory: str | Path, output: str | Path) -> Path:
    """Fresh HTML only, under the repository's ignored analysis_runs tree."""
    requested = Path(output)
    target = requested.resolve()
    runs = Path(__file__).resolve().parents[2] / "analysis_runs"
    if requested.is_symlink() or runs not in target.parents:
        raise ValueError("Output must be a new .html file under the repository's ignored analysis_runs/ directory (no symlinks)")
    if target.suffix.lower() != ".html":
        raise ValueError("Output must have a .html extension")
    case = Path(directory).resolve()
    canonical = case / "canonical"
    if canonical == target or canonical in target.parents:
        raise ValueError("Output must stay outside the canonical dataset directory")
    if target.exists():
        raise FileExistsError("Output already exists; choose a fresh filename (no overwrite)")
    if not target.parent.is_dir():
        raise ValueError("Output parent directory must exist; generate the simulation run first")
    content = build_html(load_case(case)).encode("utf-8")
    with target.open("xb") as stream:
        stream.write(content)
    return target


_PAGE = """<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'none'; img-src 'none'; font-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'">
<title>IBUKI Radar Explorer</title>
<h1>IBUKI Radar Explorer</h1><p>シミュレーション / SYNTHETIC DATA</p>
<script type="application/json" id="explorer-data">__IBUKI_DATA__</script>
</html>"""


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case", help="spatial_sim case directory containing canonical/ and truth.json")
    parser.add_argument("--output", required=True, help="new .html under ignored analysis_runs/; parent must exist")
    args = parser.parse_args(argv)
    try:
        target = render_explorer(args.case, args.output)
    except (OSError, ValueError, ImportError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Saved offline synthetic Explorer: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
