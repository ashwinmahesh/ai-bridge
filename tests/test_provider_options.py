import contextlib
import copy
import io
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
from provider_options import command_options, validate_options


class SelectionTests(unittest.TestCase):
    def test_native_mappings_and_config_precedence(self):
        for name in bridge.AGENTS:
            agent = copy.deepcopy(bridge.DEFAULTS['agents'][name])
            agent.update(model='configured-model', effort='low')
            cmd, model, effort = command_options(name, agent, 'requested-model', 'high')
            self.assertEqual((model, effort), ('requested-model', 'high'))
            self.assertEqual(cmd[cmd.index('--model') + 1], 'requested-model')
            if name == 'chatgpt':
                self.assertIn('model_reasoning_effort="high"', cmd)
                self.assertEqual(cmd[-1], '-')
            else:
                self.assertEqual(cmd[cmd.index('--effort') + 1], 'high')
            _, model, effort = command_options(name, agent)
            self.assertEqual((model, effort), ('configured-model', 'low'))

    def test_optional_settings_preserve_native_defaults(self):
        for name in bridge.AGENTS:
            agent = bridge.DEFAULTS['agents'][name]
            cmd, model, effort = command_options(name, agent)
            self.assertEqual(cmd, agent['command'])
            self.assertIsNone(model)
            self.assertIsNone(effort)
        with self.assertRaises(ValueError):
            validate_options('gemini', effort='ultra')
        for invalid in ('', '--flag', 'model\nname', 'model name', 123):
            with self.assertRaises(ValueError):
                validate_options('claude', model=invalid)

    def test_replaces_old_flags_without_removing_other_config(self):
        agent = {'command': ['codex', 'exec', '--model=old', '-c', 'model_reasoning_effort="low"', '--config', 'other_setting=true', '-'], 'format':'codex'}
        cmd, model, effort = command_options('chatgpt', agent, 'new', 'high')
        self.assertNotIn('--model=old', cmd)
        self.assertNotIn('model_reasoning_effort="low"', cmd)
        self.assertIn('other_setting=true', cmd)
        self.assertEqual((model, effort), ('new', 'high'))
        native = {'command':['agy','--model','old','--effort=low']}
        cmd, model, effort = command_options('gemini', native, 'new', 'high')
        self.assertEqual(cmd, ['agy','--model','new','--effort','high'])

    def test_selection_persisted_dispatched_and_not_inherited(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cfg = bridge.initialize(home)
            store = bridge.Store(home, cfg)
            store.enqueue('chatgpt', 'gemini', 'review', model='custom-model', effort='high')
            worker = bridge.Worker(home, cfg, store)
            calls = []
            def provider(name, prompt, **options):
                calls.append((name, options))
                if name == 'gemini':
                    return json.dumps({'reply':'reviewed','messages':[{'to':'claude','body':'check review'}]})
                return json.dumps({'reply':'checked','messages':[]})
            worker.run_provider = provider
            import threading
            thread = threading.Thread(target=worker.run)
            thread.start()
            try:
                for _ in range(200):
                    rows = store.list()
                    if len(rows) == 2 and rows[-1]['state'] == 'done':
                        break
                    time.sleep(.01)
                self.assertEqual(rows[0]['model'], 'custom-model')
                self.assertEqual(calls[0], ('gemini', {'model':'custom-model','effort':'high'}))
                self.assertEqual(calls[1], ('claude', {}))
                self.assertIsNone(rows[1]['model'])
            finally:
                worker.stopping.set()
                thread.join(timeout=3)

    def test_console_and_file_logs_contain_same_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = io.StringIO()
            with contextlib.redirect_stderr(capture):
                log = bridge.EventLog(Path(directory), 'test', console=True)
                log.emit('delivery_completed', sender='chatgpt', recipient='gemini', configured_model='test-model', configured_effort='high', response_bytes=17)
                log.close()
            console = json.loads(capture.getvalue())
            stored = json.loads((Path(directory)/'operations.jsonl').read_text())
            self.assertEqual(console, stored)
            self.assertEqual(console['recipient'], 'gemini')
        with tempfile.TemporaryDirectory() as directory:
            capture = io.StringIO()
            with contextlib.redirect_stderr(capture):
                log = bridge.EventLog(Path(directory), console=False)
                log.emit('test')
                log.close()
            self.assertEqual(capture.getvalue(), '')

    def test_old_daemon_cannot_silently_ignore_overrides(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cfg = bridge.initialize(home)
            opener = mock.Mock()
            opener.open.return_value = io.BytesIO(b'{"ok":true}')
            with mock.patch.object(bridge, 'local_opener', return_value=opener):
                with self.assertRaisesRegex(ValueError, 'no message was queued'):
                    bridge.request(home, cfg, '/messages', {'to':'gemini','body':'review','model':'selected-model'})
            self.assertEqual(opener.open.call_count, 1)
            self.assertTrue(opener.open.call_args.args[0].full_url.endswith('/health'))

    def test_foreground_cli_streams_logs_and_dispatches_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            cfg = bridge.initialize(home)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                cfg['port'] = sock.getsockname()[1]
            fake = home/'fake.py'
            fake.write_text("import json,sys\nsys.stdin.read()\nassert sys.argv[sys.argv.index('--model')+1]=='test-model'\nassert sys.argv[sys.argv.index('--effort')+1]=='high'\nprint(json.dumps({'reply':'selection received','messages':[]}))\n")
            for name in bridge.AGENTS:
                cfg['agents'][name] = {'command':[sys.executable,str(fake)],'format':'text'}
            (home/'config.json').write_text(json.dumps(cfg))
            with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
                proc = subprocess.Popen([sys.executable, bridge.__file__, '--home', str(home), 'serve'], stdout=stdout, stderr=stderr)
                try:
                    for _ in range(100):
                        try:
                            bridge.request(home,cfg,'/health')
                            break
                        except OSError:
                            time.sleep(.03)
                    sent = subprocess.run([sys.executable, bridge.__file__, '--home', str(home), 'send', '--model','test-model','--effort','high','claude','review'],capture_output=True,text=True,timeout=10)
                    self.assertEqual(sent.returncode,0,sent.stderr)
                    for _ in range(100):
                        rows = bridge.request(home,cfg,'/messages')
                        if rows and rows[0]['state'] in ('done','failed'):
                            break
                        time.sleep(.03)
                    self.assertEqual(rows[0]['state'],'done',rows)
                    self.assertEqual(rows[0]['result'],'selection received')
                finally:
                    proc.terminate()
                    proc.wait(timeout=10)
                stderr.seek(0)
                events = [json.loads(line) for line in stderr.read().decode().splitlines()]
                provider = next(event for event in events if event['event']=='provider_started')
                self.assertEqual(provider['configured_model'],'test-model')
                self.assertEqual(provider['configured_effort'],'high')
                self.assertTrue(any(event['event']=='delivery_completed' for event in events))
                self.assertFalse(any('review' in str(event) for event in events))


if __name__ == '__main__':
    unittest.main()
