#!/usr/bin/env python3
"""Build Developer ID-signed macOS releases and notarize/staple their DMG."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def run(argv, capture=False):
    result = subprocess.run([str(item) for item in argv], check=True, text=True, capture_output=capture)
    return result.stdout.strip() if capture else None


def verify_binary(binary):
    run(['/usr/bin/codesign', '--verify', '--strict', '--verbose=2', binary])
    result = subprocess.run(['/usr/bin/codesign', '--display', '--verbose=4', str(binary)], check=True, capture_output=True, text=True)
    details = result.stdout + result.stderr
    if 'Authority=Developer ID Application:' not in details or '(runtime)' not in details or 'Timestamp=' not in details:
        raise ValueError('Binary must have a timestamped Developer ID Application signature with hardened runtime; rebuild using make signed-build')


def build(root=ROOT, identity=None, python=None):
    identity = identity or os.environ.get('CODE_SIGN_IDENTITY', '')
    if not identity.startswith('Developer ID Application:'):
        raise ValueError('Set CODE_SIGN_IDENTITY to the full Developer ID Application certificate name')
    identities = run(['/usr/bin/security', 'find-identity', '-v', '-p', 'codesigning'], capture=True)
    if f'"{identity}"' not in identities:
        raise ValueError('The requested valid signing identity is not available in the Keychain')
    python = python or str(root / '.venv-build/bin/python')
    if not Path(python).is_file():
        raise ValueError('Build environment missing; run make build first, or set BUILD_PYTHON')
    output = root / 'dist/signed'
    output.mkdir(parents=True, exist_ok=True)
    # PyInstaller signs embedded libraries before packing the one-file binary.
    # Signing only the final executable would leave its embedded runtime ad-hoc signed.
    run([python, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--name', 'ai-bridge',
         '--codesign-identity', identity, '--osx-bundle-identifier', 'local.ai-bridge.api',
         '--distpath', output, '--workpath', root / 'build/signed', '--specpath', root / 'build/signed', root / 'ai_bridge.py'])
    binary = output / 'ai-bridge'
    verify_binary(binary)
    stage = root / 'build/release-image'
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    shutil.copy2(binary, stage / 'ai-bridge')
    for name in ('README.md', 'API.md', 'SKILL.md', 'MACOS_RELEASE.md'):
        shutil.copy2(root / name, stage / name)
    shutil.copytree(root / 'skills', stage / 'skills')
    dmg = output / f'ai-bridge-macos-{platform.machine()}.dmg'
    run(['/usr/bin/hdiutil', 'create', '-volname', 'AI Bridge', '-srcfolder', stage, '-ov', '-format', 'UDZO', dmg])
    run(['/usr/bin/codesign', '--force', '--sign', identity, '--timestamp', dmg])
    run(['/usr/bin/codesign', '--verify', '--verbose=2', dmg])
    print(f'Signed release: {dmg}\nNot notarized yet. Run make notarize NOTARY_PROFILE=your-keychain-profile')
    return dmg


def notarize(dmg, profile):
    if not profile or not profile.strip():
        raise ValueError('Set NOTARY_PROFILE to a notarytool Keychain credential profile name')
    if not dmg.is_file() or dmg.suffix != '.dmg':
        raise ValueError('Provide the signed DMG from make signed-build')
    run(['/usr/bin/codesign', '--verify', '--verbose=2', dmg])
    result = json.loads(run(['xcrun', 'notarytool', 'submit', dmg, '--keychain-profile', profile,
                             '--wait', '--timeout', '10m', '--output-format', 'json'], capture=True))
    report = dmg.with_suffix('.notarization.json')
    report.write_text(json.dumps(result, indent=2) + '\n')
    if result.get('status') != 'Accepted':
        raise ValueError(f'Apple did not accept this release. Submission {result.get("id")}; inspect {report} and notarytool log. Do not distribute it as notarized.')
    # Bare command-line binaries and ZIPs cannot carry a stapled ticket; the DMG can.
    run(['xcrun', 'stapler', 'staple', dmg])
    validate(dmg)
    print(f'Notarized and stapled release: {dmg}')


def validate(dmg):
    run(['xcrun', 'stapler', 'validate', dmg])
    run(['/usr/sbin/spctl', '--assess', '--type', 'open', '--context', 'context:primary-signature', '--verbose=2', dmg])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('build', 'notarize', 'validate'))
    parser.add_argument('--identity', default=os.environ.get('CODE_SIGN_IDENTITY'))
    parser.add_argument('--python', default=os.environ.get('BUILD_PYTHON'))
    parser.add_argument('--profile', default=os.environ.get('NOTARY_PROFILE'))
    parser.add_argument('--dmg', type=Path, default=ROOT / 'dist/signed' / f'ai-bridge-macos-{platform.machine()}.dmg')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This release workflow requires macOS and Xcode command-line tools')
    if args.action == 'build':
        build(identity=args.identity, python=args.python)
    elif args.action == 'notarize':
        notarize(args.dmg, args.profile)
    else:
        validate(args.dmg)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f'macOS release failed: {exc}', file=sys.stderr)
        sys.exit(1)
