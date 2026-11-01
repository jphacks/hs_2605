"""Offline export tests; no browser, npm, network, GPU or dataset required."""
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ml.analysis import spatial_scene3d as scene


ROOT = Path(__file__).resolve().parents[3]


class Tags(HTMLParser):
    def __init__(self, content):
        super().__init__()
        self.tags = []
        self.feed(content)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class SceneExportTest(unittest.TestCase):
    def setUp(self):
        runs = ROOT / "analysis_runs"
        runs.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runs)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def cli(self, *args):
        return subprocess.run([sys.executable, "-X", "utf8", "-m", "ml.analysis.spatial_scene3d", *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, encoding="utf-8")

    def test_deterministic_self_contained_html(self):
        first = scene.build_html()
        self.assertEqual(first, scene.build_html())
        tags = Tags(first).tags
        scripts = [a for t, a in tags if t == "script"]
        self.assertEqual(scripts, [{}])
        self.assertFalse(any("src" in a or "href" in a for _, a in tags))
        policy = next(a["content"] for _, a in tags if a.get("http-equiv") == "Content-Security-Policy")
        self.assertIn("connect-src 'none'", policy)
        self.assertNotIn("__SCENE_BUNDLE__", first)
        self.assertIn("実測ではありません", first)
        self.assertIn("Permission is hereby granted", first)

    def test_accessible_controls_and_static_fallback(self):
        tags = Tags(scene.build_html()).tags
        buttons = [a for t, a in tags if t == "button"]
        self.assertEqual({a["data-camera"] for a in buttons}, {"left", "right", "up", "down", "in", "out", "reset"})
        self.assertTrue(all(a.get("aria-label") or a["data-camera"] == "reset" for a in buttons))
        svg = next(a for t, a in tags if t == "svg")
        self.assertEqual(svg["id"], "scene-fallback")
        self.assertNotIn("hidden", svg)  # Also usable when JavaScript is disabled.
        canvas = next(a for t, a in tags if t == "canvas")
        self.assertEqual(canvas["tabindex"], "0")
        self.assertIn("hidden", canvas)

    def test_new_nested_output_and_identical_bytes(self):
        a = scene.render_scene(self.directory / "nested" / "a.html")
        b = scene.render_scene(self.directory / "b.html")
        self.assertEqual(a.read_bytes(), b.read_bytes())
        self.assertEqual(a.read_bytes(), scene.build_html().encode("utf-8"))

    def test_existing_output_unchanged(self):
        target = self.directory / "existing.html"
        target.write_bytes(b"original")
        with self.assertRaises(FileExistsError):
            scene.render_scene(target)
        self.assertEqual(target.read_bytes(), b"original")

    def test_invalid_extension_and_outside_directory(self):
        for target in (self.directory / "bad.json", ROOT / "not-allowed.html"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                scene.render_scene(target)
            self.assertFalse(target.exists())

    def test_symlink_rejected_without_changing_destination(self):
        target = self.directory / "original.html"
        target.write_bytes(b"keep")
        link = self.directory / "link.html"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable to this Windows account")
        with self.assertRaises(ValueError):
            scene.render_scene(link)
        self.assertEqual(target.read_bytes(), b"keep")

    def test_missing_resources_fail_before_creating_output(self):
        target = self.directory / "no-output" / "scene.html"
        with patch.object(scene, "RESOURCES", self.directory / "missing"):
            with self.assertRaisesRegex(ValueError, "Missing offline scene resources"):
                scene.render_scene(target)
        self.assertFalse(target.parent.exists())

    def resources(self):
        resources = self.directory / "resources"
        shutil.copytree(scene.RESOURCES, resources, ignore=shutil.ignore_patterns("node_modules"))
        return resources

    def test_tampered_bundle_rejected(self):
        resources = self.resources()
        (resources / "scene.bundle.js").write_bytes(b"tampered")
        with patch.object(scene, "RESOURCES", resources):
            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                scene.build_html()

    def test_inline_boundary_rejected_even_with_matching_hash(self):
        resources = self.resources()
        payload = b'console.log("</ScRiPt>");'
        (resources / "scene.bundle.js").write_bytes(payload)
        (resources / "scene.bundle.sha256").write_text(hashlib.sha256(payload).hexdigest(), encoding="ascii")
        with patch.object(scene, "RESOURCES", resources):
            with self.assertRaisesRegex(ValueError, "Unsafe inline script boundary"):
                scene.build_html()

    def test_license_is_escaped(self):
        resources = self.resources()
        (resources / "THREE-LICENSE.txt").write_text("<test>&notice", encoding="utf-8")
        with patch.object(scene, "RESOURCES", resources):
            self.assertIn("&lt;test&gt;&amp;notice", scene.build_html())

    def test_pinned_dependencies_and_bundle_hash(self):
        package = json.loads((scene.RESOURCES / "package.json").read_text())
        lock = json.loads((scene.RESOURCES / "package-lock.json").read_text())
        for name, version in {**package["dependencies"], **package["devDependencies"]}.items():
            entry = lock["packages"]["node_modules/" + name]
            self.assertEqual(version, entry["version"])
            self.assertTrue(entry["integrity"].startswith("sha512-"))
        expected = (scene.RESOURCES / "scene.bundle.sha256").read_text().strip()
        self.assertEqual(expected, hashlib.sha256((scene.RESOURCES / "scene.bundle.js").read_bytes()).hexdigest())

    def test_cli_success_and_clear_safe_failure(self):
        target = self.directory / "cli.html"
        result = self.cli("--output", target)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Saved illustrative offline 3D scene:", result.stdout)
        before = target.read_bytes()
        for args in (("--output", target), ("--output", self.directory / "bad.txt"), ()):
            result = self.cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertIn("error:", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(before, target.read_bytes())


if __name__ == "__main__":
    unittest.main()
