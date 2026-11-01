"""Export an illustrative 3D crib installation scene, never radar measurements."""
from __future__ import annotations

import hashlib
from html import escape
from pathlib import Path
import re


RESOURCES = Path(__file__).with_name("scene3d")


def build_html() -> str:
    """Use committed offline resources; Node/npm/network are not export dependencies."""
    try:
        bundle = (RESOURCES / "scene.bundle.js").read_bytes()
        digest = (RESOURCES / "scene.bundle.sha256").read_text(encoding="ascii").strip()
        template = (RESOURCES / "page.html").read_text(encoding="utf-8")
        license_text = (RESOURCES / "THREE-LICENSE.txt").read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError("Missing offline scene resources; restore the checkout or run npm ci && npm run build in ml/analysis/scene3d") from exc
    if hashlib.sha256(bundle).hexdigest() != digest:
        raise ValueError("Offline scene bundle SHA-256 mismatch; rebuild in ml/analysis/scene3d")
    script = bundle.decode("utf-8")
    if re.search(r"</script", script, re.IGNORECASE):
        raise ValueError("Unsafe inline script boundary; rebuild the offline scene bundle")
    return (template.replace("__THREE_LICENSE__", escape(license_text))
            .replace("__BUNDLE_SHA256__", digest).replace("__SCENE_BUNDLE__", script))


def render_scene(output: str | Path) -> Path:
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
    content = build_html().encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb") as stream:
        stream.write(content)
    return target


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new .html under ignored analysis_runs/")
    args = parser.parse_args(argv)
    try:
        target = render_scene(args.output)
    except (OSError, ValueError, UnicodeError) as exc:
        parser.exit(2, f"error: {exc}\n")
    print(f"Saved illustrative offline 3D scene: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
