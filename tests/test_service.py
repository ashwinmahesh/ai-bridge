"""Isolated HTTP integration tests: no account access or installed AI tools required."""
import copy
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ai_bridge
import bridge
import service
from local_http import local_opener

ROOT = Path(__file__).resolve().parents[1]


class ServiceTests(unittest.TestCase):
    executable = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / 'state'
        self.cfg = bridge.initialize(self.home)
        self.fake = Path(self.temp.name) / 'provider.sh'
        self.fake.write_text('#!/bin/sh\n/bin/cat >/dev/null\nprintf \'{"reply":"PACKAGED_OK","messages":[]}\\n\'\n')
        self.fake.chmod(0o700)
        for agent in self.cfg['agents'].values():
            agent.clear()
            agent.update(command=[str(self.fake)], format='text')
        service.atomic_json(self.home / 'config.json', self.cfg)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        cmd = [self.executable] if self.executable else [sys.executable, str(ROOT / 'ai_bridge.py')]
        env = dict(os.environ, PATH='/nonexistent') if self.executable else None
        self.output = (Path(self.temp.name) / 'console').open('w+')
        self.command = cmd + ['--home', str(self.home), '--port', str(self.port), '--log-file-only', 'serve']
        self.env = env
        self.proc = subprocess.Popen(self.command, stdout=self.output, stderr=self.output, env=env)
        self.token = (self.home / 'token').read_text().strip()
        for _ in range(200):
            if self.proc.poll() is not None:
                self.output.seek(0)
                self.fail(self.output.read())
            try:
                self.api('GET', '/health')
                break
            except OSError:
                time.sleep(.05)
        else:
            self.fail('Service failed to become ready')

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=15)
        self.output.close()
        self.temp.cleanup()

    def api(self, verb, path, data=None, headers=None):
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json', **(headers or {})}
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}{path}', method=verb, headers=headers, data=json.dumps(data).encode() if data is not None else None)
        try:
            with local_opener().open(req, timeout=10) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            exc.close()
            raise

    def project(self, name):
        return self.api('POST', '/v1/projects', {'name': name})['id']

    def poll(self, base, message):
        for _ in range(100):
            rows = self.api('GET', base + '/messages')
            row = next(r for r in rows if r['id'] == message['id'])
            if row['state'] in ('done', 'failed'):
                return row
            time.sleep(.05)
        self.fail('Provider did not complete')

    def test_projects_delivery_context_config_restart(self):
        a, b = self.project('alpha'), self.project('beta')
        base = '/v1/projects/' + a
        self.assertEqual(len(self.api('GET', '/v1/projects')), 2)
        self.api('POST', base + '/context/scaffold', {'task_id': '123'})
        doc = self.api('GET', base + '/context/file?path=project/brief.md')
        self.api('POST', base + '/context/file', {'path': doc['path'], 'content': 'Shared brief', 'expected_version': doc['version'], 'writer': 'chatgpt'})
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.api('POST', base + '/context/file', {'path': doc['path'], 'content': 'Stale', 'expected_version': doc['version']})
        self.assertEqual(error.exception.code, 409)
        self.assertFalse(self.api('GET', '/v1/projects/' + b + '/context/file?path=project/brief.md')['exists'])
        for recipient in ('gemini', 'claude'):
            message = self.api('POST', base + '/messages', {'to': recipient, 'body': 'test', 'context_files': ['project/brief.md'], 'model': 'test-model', 'effort': 'high'})
            result = self.poll(base, message)
            self.assertEqual(result['state'], 'done', result.get('error'))
            self.assertEqual(result['result'], 'PACKAGED_OK')
        configuration = self.api('GET', base + '/providers')
        configuration['agents']['claude']['model'] = 'next-model'
        self.api('PUT', base + '/providers', {'agents': configuration['agents']})
        self.assertEqual(self.api('GET', base + '/providers')['agents']['claude']['model'], 'next-model')
        self.api('PUT', '/v1/providers', {'agents': configuration['agents']})
        events = self.api('GET', '/v1/logs?limit=1000')
        self.assertTrue(any(e['event'] == 'provider_response' for e in events))
        self.assertTrue(any(e.get('response_bytes') for e in events))
        self.assertEqual(os.stat(self.home / 'token').st_mode & 0o777, 0o600)
        self.api('POST', '/v1/shutdown', {})
        self.proc.wait(timeout=15)
        self.assertFalse((self.home / 'service.json').exists())

    def test_http_mcp_auth_origin_protocol_and_sse(self):
        identifier = self.project('mcp')
        path = '/v1/projects/' + identifier + '/mcp?identity=claude'
        headers = {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'}
        def rpc(method, params=None, **extra):
            return self.api('POST', path, {'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}}, headers={**headers, **extra})
        self.assertEqual(rpc('initialize', {'protocolVersion': '2025-06-18'})['result']['protocolVersion'], '2025-06-18')
        self.assertEqual(len(rpc('tools/list')['result']['tools']), 8)
        result = rpc('tools/call', {'name': 'bridge_send', 'arguments': {'to': 'gemini', 'body': 'hi'}})
        message = json.loads(result['result']['content'][0]['text'])
        row = self.poll('/v1/projects/' + identifier, message)
        self.assertEqual(row['sender'], 'claude')
        self.assertIsNone(self.api('POST', path, {'jsonrpc': '2.0', 'method': 'notifications/initialized'}, headers))
        for extras, code in (({'Authorization': 'Bearer invalid'}, 401), ({'Origin': 'https://evil.example'}, 403), ({'MCP-Protocol-Version': 'bad'}, 400)):
            with self.assertRaises(urllib.error.HTTPError) as error:
                rpc('ping', **extras)
            self.assertEqual(error.exception.code, code)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.api('GET', path)
        self.assertEqual(error.exception.code, 405)
        req = urllib.request.Request(f'http://127.0.0.1:{self.port}/v1/events', headers={'Authorization': 'Bearer ' + self.token})
        with local_opener().open(req, timeout=5) as response:
            self.assertEqual(response.headers['Content-Type'], 'text/event-stream')
            self.assertEqual(response.readline().strip(), b': connected')
            response.readline()
            self.assertTrue(response.readline().startswith(b'id:'))

    def test_crash_recovery_resumes_queue_without_repeating_running_turn(self):
        identifier = self.project('recovery')
        base = '/v1/projects/' + identifier
        self.fake.write_text('#!/bin/sh\n/bin/cat >/dev/null\n/bin/sleep 2\nprintf \'{"reply":"RECOVERED","messages":[]}\\n\'\n')
        first = self.api('POST', base + '/messages', {'to': 'claude', 'body': 'first'})
        second = self.api('POST', base + '/messages', {'to': 'claude', 'body': 'second'})
        for _ in range(100):
            if self.api('GET', base + '/messages')[0]['state'] == 'running':
                break
            time.sleep(.02)
        else:
            self.fail('First delivery never started')
        # One-file launchers may have a supervisor parent; crash the actual API PID.
        import signal
        os.kill(self.api('GET', '/health')['pid'], signal.SIGKILL)
        self.proc.wait(timeout=10)
        self.proc = subprocess.Popen(self.command, stdout=self.output, stderr=self.output, env=self.env)
        for _ in range(200):
            try:
                self.api('GET', '/health')
                break
            except OSError:
                time.sleep(.05)
        result = self.poll(base, second)
        self.assertEqual(result['state'], 'done', result.get('error'))
        first_row = next(r for r in self.api('GET', base + '/messages') if r['id'] == first['id'])
        self.assertEqual(first_row['state'], 'failed')
        self.assertIn('unknown', first_row['error'])

    def test_input_validation_and_profile_exclusivity(self):
        for payload in ({}, {'name': '../escape'}, {'name': 'p', 'folder': 'relative'}):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.api('POST', '/v1/projects', payload)
            self.assertEqual(error.exception.code, 400)
        with self.assertRaises(urllib.error.HTTPError):
            self.api('GET', '/v1/projects/missing/messages')
        identifier = self.project('locked')
        with (self.home / 'projects' / identifier / 'daemon.lock').open('a') as lock:
            import fcntl
            with self.assertRaises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        cfg = self.api('GET', '/v1/providers')['agents']
        cfg['claude']['command'] = []
        with self.assertRaises(urllib.error.HTTPError):
            self.api('PUT', '/v1/providers', {'agents': cfg})


class InstallerTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('AI_BRIDGE_TEST_BINARY'), 'requires the packaged artifact')
    def test_real_binary_installs_and_runs_without_source_or_python(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            user = root / 'user'
            user.mkdir()
            state = root / 'service'
            environment = dict(os.environ, HOME=str(user), PATH='/nonexistent')
            result = subprocess.run([os.environ['AI_BRIDGE_TEST_BINARY'], '--home', str(state), 'install-service'], env=environment, check=True, capture_output=True, text=True)
            installed = json.loads(result.stdout)
            self.assertFalse(installed['started'])
            self.assertTrue(Path(installed['plist']).is_relative_to(user))
            binary = installed['executable']
            info = subprocess.run([binary, '--home', str(state), 'service-info'], cwd=root, env=environment, check=True, capture_output=True, text=True)
            self.assertFalse(json.loads(info.stdout)['running'])
            self.assertEqual(os.stat(state / 'token').st_mode & 0o777, 0o600)

    def test_install_is_stable_private_and_does_not_start_without_flag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            executable = root / 'downloaded-ai-bridge'
            executable.write_bytes(b'fake binary')
            with mock.patch.object(sys, 'frozen', True, create=True), mock.patch.object(sys, 'executable', str(executable)), mock.patch('ai_bridge.subprocess.run') as launch:
                result = ai_bridge.install(root / 'state', 9876, root / 'LaunchAgents')
                launch.assert_not_called()
                import plistlib
                plist = plistlib.loads(Path(result['plist']).read_bytes())
                self.assertEqual(plist['ProgramArguments'][0], str((root / 'state/bin/ai-bridge').resolve()))
                self.assertEqual(plist['KeepAlive'], {'SuccessfulExit': False})
                self.assertEqual((root / 'state/bin/ai-bridge').read_bytes(), b'fake binary')
                self.assertEqual(os.stat(result['plist']).st_mode & 0o777, 0o600)


if __name__ == '__main__':
    executable = os.environ.get('AI_BRIDGE_TEST_BINARY')
    ServiceTests.executable = str(Path(executable).resolve()) if executable else None
    unittest.main()
