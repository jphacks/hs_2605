"""Run the existing team server from a read-only Git snapshot in an owned temp dir.

No pc/ checkout, source edits, git merge, device access, or notification requests.
Requires Node 22+, web dependencies, and the team's Python dependencies.
"""
import argparse
import os
from pathlib import Path
import runpy
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ref', default='origin/main')
    parser.add_argument('--replay', type=Path, help='optional existing recording; otherwise use team synthetic fixture')
    parser.add_argument('--serve', action='store_true', help='keep the unchanged team server running for browser QA')
    parser.add_argument('--port', type=int, help='HTTP port; default ephemeral for tests or 8000 for --serve')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    git = ['git', '-c', f'safe.directory={root.as_posix()}', '-C', str(root)]
    revision = subprocess.check_output(git + ['rev-parse', args.ref], text=True).strip()
    files = subprocess.check_output(git + ['ls-tree', '-r', '--name-only', revision, 'pc'], text=True).splitlines()
    if 'pc/ibuki/server.py' not in files:
        parser.error('selected Git revision does not contain the team server')
    with tempfile.TemporaryDirectory(prefix='ibuki-pc-integration-') as folder:
        temporary = Path(folder)
        for relative in files:
            target = temporary / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(subprocess.check_output(git + ['show', f'{revision}:{relative}']))
        recordings = sorted((root / 'recordings').glob('*.csv')) if (root / 'recordings').exists() else []
        replay = args.replay.resolve() if args.replay else recordings[0] if recordings else None
        synthetic = replay is None
        if synthetic:
            fixture = runpy.run_path(str(temporary / 'pc/tests/synth.py'))['make_lines']
            replay = temporary / 'SYNTHETIC-replay.csv'
            replay.write_text('\n'.join(fixture(seconds=14, shake=(6, 9, 2))) + '\n', encoding='ascii')
        if not replay.is_file():
            parser.error('replay file does not exist')
        port = args.port or (8000 if args.serve else None)
        if port is None:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
        env = dict(os.environ, IBUKI_NTFY_TOPIC='', PYTHONUNBUFFERED='1')
        command = [sys.executable, str(temporary / 'pc/ibuki/server.py'), '--brain', 'placeholder',
                   '--replay', str(replay), '--loop', '--speed', '2', '--host', '127.0.0.1',
                   '--http-port', str(port), '--web-dist', str(root / 'web/dist') if args.serve else '', '--env-file', '']
        print(f'Team server snapshot {revision[:12]}; input={"SYNTHETIC fixture (not sensor measurements)" if synthetic else "recording replay (not live measurements)"}', flush=True)
        with (temporary / 'server.log').open('w+', encoding='utf-8') as log:
            process = subprocess.Popen(command, env=env, cwd=temporary, stdout=log, stderr=subprocess.STDOUT)
            try:
                url = f'http://127.0.0.1:{port}'
                deadline = time.monotonic() + 15
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('team server exited before readiness')
                    try:
                        with urllib.request.urlopen(url + '/api/health', timeout=0.5) as response:
                            if response.status == 200:
                                break
                    except OSError:
                        if time.monotonic() >= deadline:
                            raise RuntimeError('team server readiness timed out')
                        time.sleep(0.1)
                if args.serve:
                    print(f'Browser QA: {url}/ (select LIVE); replay/placeholder only; notifications OFF. Ctrl+C to stop.', flush=True)
                    try:
                        process.wait()
                    except KeyboardInterrupt:
                        pass
                else:
                    check = ['node', str(root / 'web/scripts/check-pc-server.mjs'), url]
                    if synthetic:
                        check.append('--require-shake')
                    subprocess.run(check, cwd=root / 'web', check=True, timeout=22)
            except Exception:
                log.flush()
                log.seek(0)
                print(log.read(), file=sys.stderr)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()


if __name__ == '__main__':
    main()
