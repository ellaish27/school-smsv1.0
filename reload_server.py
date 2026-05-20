#!/usr/bin/env python3
import os
import subprocess
import sys
import time
from pathlib import Path

WATCH_EXTENSIONS = {'.py', '.html', '.css', '.js', '.json'}
EXCLUDE_DIRS = {'__pycache__', '.git', 'node_modules', 'venv', '.venv'}
WATCHED_PATHS = ['app.py', 'templates', 'static']
POLL_INTERVAL = 1.0


def should_watch(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in WATCH_EXTENSIONS:
        return False
    if any(part in EXCLUDE_DIRS for part in path.parts):
        return False
    return True


def snapshot_files() -> dict[str, float]:
    snapshot = {}
    for base in WATCHED_PATHS:
        base_path = Path(base)
        if base_path.is_file() and should_watch(base_path):
            snapshot[str(base_path)] = base_path.stat().st_mtime_ns
            continue
        if not base_path.exists():
            continue
        for path in base_path.rglob('*'):
            if should_watch(path):
                snapshot[str(path)] = path.stat().st_mtime_ns
    return snapshot


def start_server() -> subprocess.Popen:
    env = os.environ.copy()
    env['FLASK_ENV'] = 'development'
    print('Starting server: python app.py --no-watch')
    return subprocess.Popen([sys.executable, 'app.py', '--no-watch'], env=env)


def stop_server(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


if __name__ == '__main__':
    current_snapshot = snapshot_files()
    server_proc = start_server()
    try:
        while True:
            time.sleep(POLL_INTERVAL)
            new_snapshot = snapshot_files()
            if new_snapshot != current_snapshot:
                print('Change detected, restarting server...')
                stop_server(server_proc)
                server_proc = start_server()
                current_snapshot = new_snapshot
            if server_proc.poll() is not None:
                print('Server process exited. Restarting...')
                server_proc = start_server()
    except KeyboardInterrupt:
        print('\nStopping server watcher...')
        stop_server(server_proc)
        sys.exit(0)
