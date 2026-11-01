"""Export an illustrative 3D crib installation scene, never radar measurements."""
from __future__ import annotations

import hashlib
from html import escape
import json
from pathlib import Path
import re


RESOURCES = Path(__file__).with_name("scene3d")


def build_html(result: dict | None = None) -> str:
    """Use committed offline resources; Node/npm/network are not export dependencies."""
    try:
        bundle = (RESOURCES / "scene.bundle.js").read_bytes()
        digest = (RESOURCES / "scene.bundle.sha256").read_text(encoding="ascii").strip()
        template = (RESOURCES / "page.html").read_text(encoding="utf-8")
        license_text = (RESOURCES / "THREE-LICENSE.txt").read_text(encoding="utf-8")
        panel = (RESOURCES / "analysis.html").read_text(encoding="utf-8") if result is not None else ""
    except OSError as exc:
        raise ValueError("Missing offline scene resources; restore the checkout or run npm ci && npm run build in ml/analysis/scene3d") from exc
    if hashlib.sha256(bundle).hexdigest() != digest:
        raise ValueError("Offline scene bundle SHA-256 mismatch; rebuild in ml/analysis/scene3d")
    script = bundle.decode("utf-8")
    if re.search(r"</script", script, re.IGNORECASE):
        raise ValueError("Unsafe inline script boundary; rebuild the offline scene bundle")
    playback = ""
    encoded = ""
    if result is not None:
        # Share the Explorer's single state machine, not a second playback implementation.
        from .spatial_explorer import _PLAYBACK_JS
        playback = _PLAYBACK_JS
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        for char, replacement in (("&", "\\u0026"), ("<", "\\u003c"), (">", "\\u003e"),
                                  ("\u2028", "\\u2028"), ("\u2029", "\\u2029")):
            encoded = encoded.replace(char, replacement)
        # JavaScript-disabled viewers can inspect every original window in order.
        def value(number, digits=2):
            return "—" if number is None else f"{number:.{digits}f}"
        rows = []
        for index, window in enumerate(result["windows"]):
            reference, estimated = window["truth_position_cm"], window["position_cm"]
            cells = (str(index+1), value(window["reference_board_uptime_ms"]/1000),
                     value((reference["x"]**2+reference["y"]**2)**.5), value(window["configured_angle_deg"], 1),
                     value((estimated["x"]**2+estimated["y"]**2)**.5) if estimated is not None else "—",
                     value(window["estimated_angle_deg"], 1), value(window["position_error_cm"]),
                     "推定あり" if window["estimation_available"] else "推定できません")
            rows.append("<tr>"+"".join(f"<td>{escape(cell)}</td>" for cell in cells)+"</tr>")
        panel = panel.replace("__STATIC_ROWS__", "".join(rows))
    page = (template.replace("__THREE_LICENSE__", escape(license_text))
            .replace("__BUNDLE_SHA256__", digest).replace("__SCENE_BUNDLE__", script)
            .replace("__PLAYBACK_JS__", playback)
            .replace("__MAIN_CLASS__", ' class="analysis-mode"' if result is not None else "")
            .replace("__DATA_NOTICE__", " · 合成データ / SYNTHETIC DATA" if result is not None else "")
            .replace("__ANALYSIS_PANEL__", panel))
    # Embed untrusted strings last: they must not be interpreted as template tokens.
    return page.replace("__CASE_DATA__", '<script id="scene-data" type="application/json">'+encoded+'</script>' if result is not None else "")


def render_scene(output: str | Path, *, case: str | Path | None = None) -> Path:
    """Write a fresh self-contained HTML under ignored analysis_runs, never overwrite."""
    requested = Path(output)
    target = requested.resolve()
    runs = Path(__file__).resolve().parents[2] / "analysis_runs"
    if requested.is_symlink() or runs not in target.parents:
        raise ValueError("Output must be a new .html file under this repository's ignored analysis_runs/ (no symlinks)")
    if target.suffix.lower() != ".html":
        raise ValueError("Output must have a .html extension")
    if target.exists():
        raise FileExistsError("Output already exists; choose a fresh filename (no overwrite)")
    result = None
    if case is not None:
        canonical = (Path(case).resolve() / "canonical").resolve()
        if canonical == target or canonical in target.parents:
            raise ValueError("Output must stay outside the canonical dataset directory")
        try:
            from .spatial_explorer import load_case
            result = load_case(case)
        except ImportError as exc:
            raise ValueError("Analysis mode needs offline dependencies: install requirements.txt and ml/analysis/requirements.txt") from exc
    content = build_html(result).encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(content)
    return target


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new .html under ignored analysis_runs/")
    parser.add_argument("--case", help="optional existing, validated spatial_sim case directory; never regenerated")
    args = parser.parse_args(argv)
    try:
        target = render_scene(args.output, case=args.case)
    except (OSError, ValueError, UnicodeError, OverflowError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Saved illustrative offline 3D scene: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
