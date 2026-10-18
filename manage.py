#!/usr/bin/env python3
"""Start and stop this project's foreground-compatible bridge daemon."""
import argparse
import contextlib
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import bridge

START_TIMEOUT = 60
STOP_TIMEOUT = 15
HEALTH_TIMEOUT = 1


def locked(home):
    with (home / 'daemon.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


def status(home, cfg, timeout=HEALTH_TIMEOUT):
    value = bridge.request(home, cfg, '/health', timeout=timeout)
    pidfile = home / 'daemon.pid'
    if not pidfile.exists() or int(pidfile.read_text()) != value.get('pid'):
        raise ValueError('Port is occupied by another bridge; check state directory and port')
    return value


def start(home, cfg, timeout=START_TIMEOUT):
    if locked(home):
        info = status(home, cfg)
        print(f'Already running (PID {info["pid"]})')
        return
    output = home / 'console.log'
    # Only startup/shutdown diagnostics go here; operation logs rotate separately.
    with output.open('ab', buffering=0) as console:
        os.chmod(output, 0o600)
        proc = subprocess.Popen([sys.executable, str(Path(bridge.__file__).resolve()), '--home', str(home), '--log-file-only', 'serve'],
                                stdin=subprocess.DEVNULL, stdout=console, stderr=console, start_new_session=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise ValueError(f'Daemon exited with code {proc.returncode}; see {output}')
        try:
            remaining = max(.01, deadline - time.monotonic())
            info = status(home, cfg, timeout=min(HEALTH_TIMEOUT, remaining))
            if info['pid'] == proc.pid:
                print(f'Running in background (PID {proc.pid}); logs: {home / "operations.jsonl"}')
                return
        except (OSError, ValueError):
            pass
        time.sleep(min(.1, max(0, deadline - time.monotonic())))
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    raise ValueError(f'Daemon failed to become ready; see {output}')


def stop(home, cfg):
    if not locked(home):
        print('Not running')
        return
    # Authenticate the live daemon and match its PID file before signaling it.
    info = status(home, cfg)
    os.kill(info['pid'], signal.SIGTERM)
    deadline = time.monotonic() + STOP_TIMEOUT
    while time.monotonic() < deadline:
        if not locked(home):
            print('Stopped')
            return
        time.sleep(.1)
    raise ValueError('Daemon is still stopping; inspect console.log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(bridge.__file__).resolve().parent / '.bridge')
    parser.add_argument('--project')
    parser.add_argument('--folder', type=Path)
    parser.add_argument('--port', type=int)
    parser.add_argument('action', choices=('start', 'stop', 'restart', 'status', 'logs'))
    args = parser.parse_args()
    home, cfg = bridge.select_project(args.home, args.project, args.folder, args.port)
    execute(home, cfg, args.action)


def execute(home, cfg, action):
    if action == 'logs':
        path = home / 'operations.jsonl'
        path.touch(mode=0o600, exist_ok=True)
        os.execvp('tail', ['tail', '-n', '50', '-F', str(path)])
    with (home / 'manager.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        if action == 'status':
            if not locked(home):
                print('Not running')
                return
            info = status(home, cfg)
            print(f'Running (PID {info["pid"]}) on 127.0.0.1:{cfg["port"]}')
        else:
            if action in ('stop', 'restart'):
                stop(home, cfg)
            if action in ('start', 'restart'):
                start(home, cfg)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as exc:
        print(f'ai-bridge: {exc}', file=sys.stderr)
        sys.exit(1)
