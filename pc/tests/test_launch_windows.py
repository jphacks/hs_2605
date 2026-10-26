"""Launcher decisions are tested without installing packages or opening a browser."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("launch_windows", Path(__file__).parents[1] / "launch_windows.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ibuki launcher space ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "Project With Spaces"
        self.root.mkdir()

    def put(self, relative, content=""):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def test_creates_environment_only_when_missing(self):
        with patch.object(launcher, "run") as run:
            self.assertEqual(launcher.environment(self.root), self.root / ".venv/Scripts/python.exe")
        self.assertEqual(run.call_args.args[0][-2:], ["venv", self.root / ".venv"])

    def test_reuses_team_then_local_environment_without_mutating_them(self):
        local = self.put(".venv/Scripts/python.exe")
        with patch.object(launcher.subprocess, "run", return_value=Mock(returncode=0)), patch.object(launcher, "run") as run:
            self.assertEqual(launcher.environment(self.root), local)
            team = self.root.parent / "env/Scripts/python.exe"
            team.parent.mkdir(parents=True); team.touch()
            self.assertEqual(launcher.environment(self.root), team)
            run.assert_not_called()

    def test_broken_environment_is_preserved_and_fallback_is_created(self):
        python = self.put(".venv/Scripts/python.exe", "existing")
        with patch.object(launcher.subprocess, "run", return_value=Mock(returncode=1)), patch.object(launcher, "run") as run:
            self.assertEqual(launcher.environment(self.root), self.root / ".venv-windows/Scripts/python.exe")
            run.assert_called_once_with([launcher.sys.executable, "-m", "venv", self.root / ".venv-windows"])
        self.assertEqual(python.read_text(), "existing")

    def test_requirements_install_refresh_and_reuse(self):
        requirements = self.put("requirements.txt", "fastapi>=0.110\n")
        python = self.put(".venv/Scripts/python.exe")
        with patch.object(launcher.subprocess, "run", return_value=Mock(returncode=0)), patch.object(launcher, "run") as run:
            launcher.dependencies(self.root, python)
            run.assert_called_once_with([python, "-m", "pip", "install", "-r", requirements])
            launcher.dependencies(self.root, python)
            self.assertEqual(run.call_count, 1)
            requirements.write_text("fastapi>=0.111\n")
            launcher.dependencies(self.root, python)
            self.assertEqual(run.call_count, 2)
        with patch.object(launcher.subprocess, "run", side_effect=[Mock(returncode=1), Mock(returncode=0)]), patch.object(launcher, "run") as run:
            launcher.dependencies(self.root, python)
            self.assertEqual(run.call_count, 1)

    def test_incompatible_native_packages_are_reinstalled_and_rechecked(self):
        self.put("requirements.txt", "numpy>=1.26\n")
        python = self.put(".venv/Scripts/python.exe")
        with patch.object(launcher.subprocess, "run", side_effect=[Mock(returncode=1), Mock(returncode=1), Mock(returncode=0)]), patch.object(launcher, "run") as run:
            launcher.dependencies(self.root, python)
            self.assertIn("--force-reinstall", run.call_args.args[0])
            self.assertEqual(run.call_count, 2)
        with patch.object(launcher.subprocess, "run", return_value=Mock(returncode=1)), patch.object(launcher, "run"):
            with self.assertRaisesRegex(RuntimeError, "still cannot import"):
                launcher.dependencies(self.root, python)

    def test_build_tracks_changes_additions_deletions_public_and_configuration(self):
        source = self.put("web/src/App.tsx", "first")
        self.put("web/package.json", "{}")
        self.put("web/package-lock.json", "{}")
        cli = self.put("Node With Spaces/node_modules/npm/bin/npm-cli.js")
        node, npm = str(cli.parents[3] / "node.exe"), str(cli.parents[3] / "npm.cmd")
        def build(command, **kwargs):
            if "build" in command:
                self.put("web/dist/index.html", "built")
        with patch.object(launcher.shutil, "which", side_effect=lambda name: node if name == "node" else npm), patch.object(launcher, "run", side_effect=build) as run:
            launcher.build_web(self.root)
            self.assertEqual(run.call_count, 2)  # npm ci and build
            self.put("web/node_modules/.present")
            launcher.build_web(self.root); self.assertEqual(run.call_count, 2)
            for path, content in [(source, "changed"), (self.root / "web/public/image.svg", "asset"), (self.root / "web/vite.config.ts", "config")]:
                path.parent.mkdir(parents=True, exist_ok=True); path.write_text(content)
                launcher.build_web(self.root)
            source.unlink(); launcher.build_web(self.root)
            self.assertEqual(run.call_count, 6)

    def test_missing_node_or_npm_is_clear_and_does_not_launch_server(self):
        for available in [None, "C:/Node/node.exe"]:
            with patch.object(launcher.shutil, "which", side_effect=lambda name: available if name == "node" else None):
                with self.assertRaisesRegex(RuntimeError, "requires Node.js and npm"):
                    launcher.build_web(self.root)

    def test_server_arguments_remain_unmodified_and_nonzero_exit_propagates(self):
        arguments = ["--replay", "recordings/a file.csv", "--loop", "--brain", "custom:Brain", "--http-port", "8123"]
        process = Mock(); process.wait.return_value = 7; process.poll.return_value = 7
        with patch.dict(os.environ, {"IBUKI_OPEN_BROWSER": "0"}), patch.object(launcher.subprocess, "Popen", return_value=process) as popen:
            self.assertEqual(launcher.serve(self.root, Path("python.exe"), arguments), 7)
        self.assertEqual(popen.call_args.args[0][2:], arguments)
        self.assertEqual(launcher.dashboard_url(arguments), "http://localhost:8123")
        self.assertEqual(launcher.dashboard_url(["--host", "::1"]), "http://[::1]:8000")

    def test_ctrl_c_waits_for_graceful_server_cleanup_and_reaps_it(self):
        process = Mock(); process.wait.side_effect = [KeyboardInterrupt, 0]; process.poll.return_value = 0
        with patch.dict(os.environ, {"IBUKI_OPEN_BROWSER": "0"}), patch.object(launcher.subprocess, "Popen", return_value=process):
            self.assertEqual(launcher.serve(self.root, Path("python.exe"), []), 130)
        process.wait.assert_any_call(timeout=10); process.terminate.assert_not_called()

    def test_setup_failure_code_is_preserved(self):
        with patch.object(launcher, "ROOT", self.root), patch.object(launcher, "environment", side_effect=subprocess.CalledProcessError(19, ["pip"])):
            original = Path.cwd()
            try:
                self.assertEqual(launcher.main([]), 19)
            finally:
                os.chdir(original)

    def test_browser_only_opens_after_valid_health_and_exits_when_server_dies(self):
        process = Mock(); process.poll.return_value = None
        stopped = Mock(); stopped.wait.return_value = False; stopped.is_set.return_value = False
        response = Mock(); response.__enter__ = Mock(return_value=response); response.__exit__ = Mock(return_value=False)
        opener = Mock(); opener.open.return_value = response
        with patch.object(launcher.urllib.request, "build_opener", return_value=opener), patch.object(launcher.json, "load", side_effect=[{}, {"board_connected": True}]), patch.object(launcher.webbrowser, "open") as open_browser:
            launcher.open_when_ready(process, "http://localhost:8000", stopped)
            open_browser.assert_called_once_with("http://localhost:8000")
            self.assertEqual(opener.open.call_count, 2)
            process.poll.return_value = 1; open_browser.reset_mock()
            launcher.open_when_ready(process, "http://localhost:8000", stopped)
            open_browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
