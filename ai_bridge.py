#!/usr/bin/env python3
"""Packaged AI Bridge entry point: serve HTTP or install a macOS login service."""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import service

LABEL = 'local.ai-bridge.api'


def default_home():
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/AI Bridge'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local/share'))) / 'ai-bridge'


def install(home, port, launch_agents=None, start=False):
    if sys.platform != 'darwin' and launch_agents is None:
        raise ValueError('Login-service installation currently supports macOS only')
    if not getattr(sys, 'frozen', False):
        raise ValueError('Install the packaged executable; run make build first')
    home = home.expanduser().resolve()
    service.bridge.initialize(home)
    target = home / 'bin/ai-bridge'
    target.parent.mkdir(mode=0o700, exist_ok=True)
    source = Path(sys.executable).resolve()
    if source != target:
        temporary = target.with_suffix('.new')
        shutil.copy2(source, temporary)
        temporary.chmod(0o700)
        temporary.replace(target)
    directory = launch_agents or Path.home() / 'Library/LaunchAgents'
    directory.mkdir(parents=True, exist_ok=True)
    plist = directory / f'{LABEL}.plist'
    value = {'Label': LABEL, 'ProgramArguments': [str(target), '--home', str(home), '--port', str(port), '--log-file-only', 'serve'],
             'RunAtLoad': True, 'KeepAlive': {'SuccessfulExit': False}, 'ThrottleInterval': 10,
             'StandardOutPath': str(home / 'console.log'), 'StandardErrorPath': str(home / 'console.log'),
             'EnvironmentVariables': {'PATH': str(Path.home() / '.local/bin') + ':/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin'}}
    plist.write_bytes(plistlib.dumps(value))
    plist.chmod(0o600)
    (home / 'console.log').touch(mode=0o600, exist_ok=True)
    if start:
        subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)], check=True)
    return {'executable': str(target), 'plist': str(plist), 'home': str(home), 'started': start, 'token_file': str(home / 'token')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=default_home())
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--log-file-only', action='store_true')
    sub = parser.add_subparsers(dest='action')
    sub.add_parser('serve')
    install_parser = sub.add_parser('install-service')
    install_parser.add_argument('--start', action='store_true', help='Load immediately; otherwise starts at next login')
    uninstall = sub.add_parser('uninstall-service')
    uninstall.add_argument('--stop', action='store_true', help='Unload the login service now')
    sub.add_parser('service-info')
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('port must be between 1 and 65535')
    # Subscription-oriented CLI authentication; do not accidentally inherit API-key overrides.
    for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'ANTHROPIC_AUTH_TOKEN'):
        os.environ.pop(key, None)
    if args.action in (None, 'serve'):
        service.serve(args.home, args.port, not args.log_file_only)
    elif args.action == 'install-service':
        print(json.dumps(install(args.home, args.port, start=args.start), indent=2))
    elif args.action == 'service-info':
        path = args.home.expanduser() / 'service.json'
        print(path.read_text() if path.exists() else json.dumps({'running': False, 'home': str(args.home)}))
    else:
        if sys.platform != 'darwin':
            raise ValueError('Login-service uninstallation currently supports macOS only')
        if args.stop:
            subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'], check=True)
        (Path.home() / 'Library/LaunchAgents' / f'{LABEL}.plist').unlink(missing_ok=True)
        print(json.dumps({'uninstalled': True, 'data_preserved': str(args.home)}))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'ai-bridge: {exc}', file=sys.stderr)
        sys.exit(1)
