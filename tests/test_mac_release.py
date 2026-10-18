"""Release control flow tests; never use real credentials or contact Apple."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location('mac_release', Path(__file__).resolve().parents[1] / 'scripts/mac_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class MacReleaseTests(unittest.TestCase):
    def test_signing_requires_developer_id_identity(self):
        with mock.patch.dict('os.environ', {}, clear=True):
            with self.assertRaises(ValueError):
                release.build(identity='-')
            with self.assertRaises(ValueError):
                release.build(identity='Apple Distribution: Example')

    def test_verification_rejects_missing_hardened_runtime_or_timestamp(self):
        signed = 'Authority=Developer ID Application: Example\nflags=0x10000(runtime)\nTimestamp=now'
        for details in ('Signature=adhoc', signed.replace('(runtime)', ''), signed.replace('Timestamp=', 'NoTimestamp=')):
            # The final case removes the actual attribute, not just its line prefix.
            if 'NoTimestamp' in details:
                details = details.split('NoTimestamp')[0]
            with mock.patch.object(release, 'run'), mock.patch.object(release.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', details)):
                with self.assertRaises(ValueError):
                    release.verify_binary(Path('fake'))
        with mock.patch.object(release, 'run'), mock.patch.object(release.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', signed)):
            release.verify_binary(Path('fake'))

    def test_rejected_submission_never_staples(self):
        with tempfile.TemporaryDirectory() as folder:
            dmg = Path(folder) / 'release.dmg'
            dmg.touch()
            calls = []
            def run(argv, capture=False):
                calls.append(argv)
                return json.dumps({'id': 'submission', 'status': 'Invalid'}) if capture else None
            with mock.patch.object(release, 'run', side_effect=run):
                with self.assertRaises(ValueError):
                    release.notarize(dmg, 'local-profile')
            self.assertFalse(any('stapler' in command for command in calls))
            self.assertEqual(json.loads(dmg.with_suffix('.notarization.json').read_text())['status'], 'Invalid')

    def test_accepted_submission_staples_then_validates(self):
        with tempfile.TemporaryDirectory() as folder:
            dmg = Path(folder) / 'release.dmg'
            dmg.touch()
            calls = []
            def run(argv, capture=False):
                calls.append(argv)
                return json.dumps({'id': 'submission', 'status': 'Accepted'}) if capture else None
            with mock.patch.object(release, 'run', side_effect=run):
                release.notarize(dmg, 'local-profile')
            steps = [' '.join(map(str, call)) for call in calls]
            self.assertTrue(any('--keychain-profile local-profile' in step for step in steps))
            self.assertLess(next(i for i,s in enumerate(steps) if 'stapler staple' in s), next(i for i,s in enumerate(steps) if 'stapler validate' in s))
            self.assertTrue(any('spctl --assess' in step for step in steps))

    def test_missing_profile_fails_before_submission(self):
        with mock.patch.object(release, 'run') as run:
            with self.assertRaises(ValueError):
                release.notarize(Path('missing.dmg'), None)
            run.assert_not_called()

    def test_build_signs_embedded_runtime_before_creating_dmg(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            identity = 'Developer ID Application: Example (TESTTEAM)'
            python = root / 'python'
            python.touch()
            (root / 'skills').mkdir()
            for name in ('README.md', 'API.md', 'SKILL.md', 'MACOS_RELEASE.md'):
                (root / name).write_text(name)
            commands = []
            def run(argv, capture=False):
                commands.append(list(map(str, argv)))
                if capture:
                    return f'1) HASH "{identity}"'
                if 'PyInstaller' in argv:
                    (root / 'dist/signed/ai-bridge').write_bytes(b'packaged binary')
            with mock.patch.object(release, 'run', side_effect=run), mock.patch.object(release, 'verify_binary') as verify:
                release.build(root, identity, str(python))
            build = next(command for command in commands if 'PyInstaller' in command)
            self.assertEqual(build[build.index('--codesign-identity')+1], identity)
            verify.assert_called_once_with(root / 'dist/signed/ai-bridge')
            self.assertTrue((root / 'build/release-image/ai-bridge').is_file())
            self.assertFalse((root / 'build/release-image/token').exists())


if __name__ == '__main__':
    unittest.main()
