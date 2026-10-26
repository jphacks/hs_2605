"""Windows launcher support; setup uses only the Python standard library.

The server stays in the foreground with inherited console input, including Enter
and Ctrl+C. The browser readiness watcher is a bounded thread, not a detached job.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent.parent
IMPORTS = "import fastapi, uvicorn, websockets, numpy, scipy, serial, dotenv, requests, matplotlib"


def run(command, **kwargs):
    return subprocess.run([str(arg) for arg in command], check=True, **kwargs)


def environment(root: Path) -> Path:
    invalid = set()
    for directory in (root.parent / "env", root / ".venv", root / ".venv-windows"):
        python = directory / "Scripts" / "python.exe"
        if python.is_file():
            valid = subprocess.run([str(python), "-c", "import sys; sys.exit(sys.version_info < (3, 10))"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
            if not valid:
                print(f"Skipping an unusable environment (files preserved): {directory}", flush=True)
                invalid.add(directory)
                continue
            print(f"Using Python environment: {directory}", flush=True)
            return python
    directory = root / (".venv-windows" if root / ".venv" in invalid else ".venv")
    if directory in invalid:
        raise RuntimeError(f"Both local environments are unusable. Restore Python or move {directory} aside; existing files were not changed.")
    print(f"Creating Python environment: {directory}", flush=True)
    run([sys.executable, "-m", "venv", directory])
    return directory / "Scripts" / "python.exe"


def dependencies(root: Path, python: Path):
    requirements = root / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes()).hexdigest()
    marker = python.parent.parent / ".ibuki-requirements.sha256"
    def imports_work():
        return subprocess.run([str(python), "-c", IMPORTS], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    imports_ok = imports_work()
    if not imports_ok or not marker.is_file() or marker.read_text() != digest:
        print("Installing Python packages from requirements.txt ...", flush=True)
        run([python, "-m", "pip", "install", "-r", requirements])
        if not imports_work():
            # A copied environment may have installed metadata but incompatible
            # native wheels (for example cp311 NumPy with a cp312 interpreter).
            print("Existing packages cannot import; reinstalling for this Python ...", flush=True)
            run([python, "-m", "pip", "install", "--force-reinstall", "-r", requirements])
            if not imports_work():
                raise RuntimeError("Python packages still cannot import after reinstall. Check the Python installation and requirements.txt.")
        marker.write_text(digest)


def web_digest(web: Path) -> str:
    files = [web / name for name in ("index.html", "package.json", "package-lock.json", "vite.config.ts")]
    files += list(web.glob("tsconfig*.json"))
    for directory in (web / "src", web / "public"):
        if directory.is_dir():
            files += [p for p in directory.rglob("*") if p.is_file()]
    digest = hashlib.sha256()
    for file in sorted(set(files)):
        if file.is_file():
            digest.update(file.relative_to(web).as_posix().encode("utf-8") + b"\0")
            digest.update(file.read_bytes() + b"\0")
    return digest.hexdigest()


def build_web(root: Path):
    web = root / "web"
    digest = web_digest(web)
    marker = web / "dist" / ".ibuki-source.sha256"
    if (web / "dist" / "index.html").is_file() and marker.is_file() and marker.read_text() == digest:
        print("Dashboard is up to date.", flush=True)
        return
    node, npm = shutil.which("node"), shutil.which("npm.cmd")
    if not node or not npm:
        raise RuntimeError("Building the dashboard requires Node.js and npm. Install Node.js 22.12+ from https://nodejs.org/ and restart this terminal.")
    # Invoke npm's JS entry point directly: no cmd quoting/call expansion for paths
    # containing spaces, %, & or non-ASCII characters.
    npm_cli = next((p for p in (Path(npm).resolve().parent / "node_modules/npm/bin/npm-cli.js",
                                Path(node).resolve().parent / "node_modules/npm/bin/npm-cli.js") if p.is_file()), None)
    if npm_cli is None:
        raise RuntimeError("npm-cli.js was not found. Repair the Node.js/npm installation and try again.")
    print("Building the dashboard ...", flush=True)
    if not (web / "node_modules").is_dir():
        run([node, npm_cli, "ci" if (web / "package-lock.json").is_file() else "install"], cwd=web)
    run([node, npm_cli, "run", "build"], cwd=web)
    if not (web / "dist" / "index.html").is_file():
        raise RuntimeError("The web build did not produce web/dist/index.html.")
    marker.write_text(digest)


def dashboard_url(arguments: list[str]) -> str:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--http-port", default=8000, type=int)
    parser.add_argument("--host", default="0.0.0.0")
    options, _ = parser.parse_known_args(arguments)
    host = "localhost" if options.host in ("0.0.0.0", "::") else options.host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{options.http_port}"


def open_when_ready(process, url: str, stopped: threading.Event):
    # Ignore user proxy settings for a local readiness request.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(40):
        if stopped.wait(0.5) or process.poll() is not None:
            return
        try:
            with opener.open(url + "/api/health", timeout=0.5) as response:
                health = json.load(response)
            if isinstance(health, dict) and isinstance(health.get("board_connected"), bool):
                if not stopped.is_set() and process.poll() is None:
                    print(f"Opening dashboard: {url}", flush=True)
                    webbrowser.open(url)
                return
        except (OSError, ValueError, urllib.error.URLError):
            pass
    print(f"Dashboard did not become ready yet. Check server output, then open {url} manually.", flush=True)


def serve(root: Path, python: Path, arguments: list[str]) -> int:
    url = dashboard_url(arguments)
    stopped = threading.Event()
    # No shell and no new process group: Enter and Ctrl+C reach the real server.
    process = subprocess.Popen([str(python), str(root / "pc/ibuki/server.py"), *arguments])
    watcher = None
    if os.environ.get("IBUKI_OPEN_BROWSER", "1") != "0":
        watcher = threading.Thread(target=open_when_ready, args=(process, url, stopped), daemon=True)
        watcher.start()
    try:
        return process.wait()
    except KeyboardInterrupt:
        # Windows delivers the console interrupt to the server too. Give uvicorn
        # time to run lifespan cleanup and stop the alarm before any forced exit.
        try:
            process.wait(timeout=10)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            process.terminate()
            process.wait()
        return 130
    finally:
        stopped.set()
        if process.poll() is None:
            process.terminate()
            process.wait()
        if watcher:
            watcher.join(timeout=2)


def main(arguments: list[str] | None = None) -> int:
    if sys.version_info < (3, 10):
        print("IBUKI requires Python 3.10+.", file=sys.stderr)
        return 1
    try:
        os.chdir(ROOT)
        python = environment(ROOT)
        dependencies(ROOT, python)
        build_web(ROOT)
        return serve(ROOT, python, list(sys.argv[1:] if arguments is None else arguments))
    except subprocess.CalledProcessError as error:
        print(f"IBUKI setup failed (exit {error.returncode}). See the command output above.", file=sys.stderr)
        return error.returncode or 1
    except KeyboardInterrupt:
        return 130
    except (OSError, RuntimeError) as error:
        print(f"IBUKI could not start: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
