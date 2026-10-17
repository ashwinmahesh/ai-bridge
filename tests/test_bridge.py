import copy
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cfg = copy.deepcopy(bridge.DEFAULTS)
        self.store = bridge.Store(self.home, self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def test_budget_stop_and_restart(self):
        self.cfg['max_turns'] = 2
        first = self.store.enqueue('user', 'gemini', 'Hi')
        self.store.enqueue('gemini', 'claude', 'Hi', first['conversation'])
        with self.assertRaises(ValueError):
            self.store.enqueue('claude', 'chatgpt', 'Hi', first['conversation'])
        self.assertEqual(self.store.claim()['id'], first['id'])
        self.store.recover()
        self.assertEqual(self.store.list()[0]['state'], 'failed')
        self.store.stop(first['conversation'])
        self.assertEqual(self.store.list()[1]['state'], 'cancelled')
        self.assertIsNone(self.store.claim())
        with self.assertRaises(ValueError):
            self.store.enqueue('user', 'gemini', 'Hi', first['conversation'])

    def test_concurrent_claim_is_unique(self):
        self.store.enqueue('user', 'gemini', 'Hi')
        claimed = []
        threads = [threading.Thread(target=lambda: claimed.append(self.store.claim())) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(x is not None for x in claimed), 1)

    def test_provider_parsers(self):
        event = {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'hello'}}
        self.assertEqual(bridge.parse_output('codex', json.dumps(event)), 'hello')
        self.assertEqual(bridge.parse_output('gemini', '{"response":"hello"}'), 'hello')
        self.assertEqual(bridge.parse_output('claude', '{"result":"hello"}'), 'hello')
        with self.assertRaises(RuntimeError):
            bridge.parse_output('claude', '{"is_error":true,"result":"quota"}')
        with self.assertRaises(RuntimeError):
            bridge.parse_output('codex', '{"type":"turn.failed","error":"quota"}')
        with self.assertRaises(ValueError):
            bridge.parse_output('gemini', '{}')
        self.assertEqual(bridge.envelope('plain answer'), ('plain answer', []))
        self.assertEqual(bridge.envelope('```json\n{"reply":"hi","messages":[]}\n```'), ('hi', []))
        with self.assertRaises(ValueError):
            bridge.envelope('{"reply":"hi","messages":[{"to":"shell","body":"oops"}]}')

    def test_routing_loop_is_bounded(self):
        self.cfg['max_turns'] = 2
        first = self.store.enqueue('user', 'gemini', 'start')
        worker = bridge.Worker(self.home, self.cfg, self.store)
        worker.run_provider = lambda *_: json.dumps({'reply': 'ok', 'messages': [{'to': 'claude', 'body': 'continue'}]})
        thread = threading.Thread(target=worker.run)
        thread.start()
        try:
            for _ in range(100):
                rows = self.store.list(first['conversation'])
                if len(rows) == 2 and rows[-1]['state'] == 'done':
                    break
                time.sleep(.01)
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[-1]['state'], 'done')
            self.assertIn('budget', rows[-1]['error'])
            self.assertIsNone(self.store.claim())
        finally:
            worker.stopping.set()
            thread.join(timeout=3)

    def test_antigravity_stream_protocol(self):
        script = self.home / 'agy_fake.py'
        script.write_text("import json,sys\np=json.loads(sys.stdin.read())\nassert p['event']=='user'\nprint(json.dumps({'event':'init'}))\nprint(json.dumps({'event':'result','result':{'status':'SUCCESS','response':p['message']['content']}}))\n")
        self.cfg['agents']['gemini'] = {'command': [sys.executable, str(script)], 'format': 'antigravity', 'input_format': 'antigravity'}
        worker = bridge.Worker(self.home, self.cfg, self.store)
        self.assertEqual(worker.run_provider('gemini', 'hello\nworld'), 'hello\nworld')
        for status in ('ERROR', 'WAITING', 'CANCELED', 'RUNNING'):
            raw = json.dumps({'event': 'result', 'result': {'status': status, 'response': 'partial'}})
            with self.assertRaises(RuntimeError):
                bridge.parse_output('antigravity', raw)
        with self.assertRaises(ValueError):
            bridge.parse_output('antigravity', '{"event":"init"}')
        with self.assertRaises(ValueError):
            bridge.parse_output('antigravity', '{"event":"result","result":{"status":"SUCCESS","response":""}}')

    def test_operation_logs_are_metadata_only(self):
        log = bridge.EventLog(self.home)
        store = bridge.Store(self.home, self.cfg, log)
        self.cfg['agents']['claude'] = {'command': [sys.executable, '-c', "import sys; sys.stdin.read(); print('private response')"], 'format': 'text'}
        worker = bridge.Worker(self.home, self.cfg, store)
        try:
            store.enqueue('user', 'claude', 'secret message')
            self.assertEqual(worker.run_provider('claude', 'secret prompt'), 'private response')
        finally:
            log.close()
        raw = (self.home / 'operations.jsonl').read_text()
        events = [json.loads(line) for line in raw.splitlines()]
        self.assertEqual([e['event'] for e in events], ['message_queued', 'provider_started', 'provider_exited', 'provider_response'])
        self.assertEqual(events[0]['recipient'], 'claude')
        self.assertEqual(events[2]['exit_code'], 0)
        self.assertEqual(events[3]['response_chars'], len('private response'))
        for text in ('secret message', 'secret prompt', 'private response'):
            self.assertNotIn(text, raw)
        self.assertEqual((self.home / 'operations.jsonl').stat().st_mode & 0o777, 0o600)

    def test_timeout(self):
        self.cfg['timeout'] = 0.1
        self.cfg['agents']['claude'] = {'command': [sys.executable, '-c', 'import time; time.sleep(10)'], 'format': 'text'}
        worker = bridge.Worker(self.home, self.cfg, self.store)
        started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            worker.run_provider('claude', 'hi')
        self.assertLess(time.monotonic() - started, 3)


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve() / 'state'
        self.folder = Path(self.tmp.name).resolve() / 'workspace'

    def tearDown(self):
        self.tmp.cleanup()

    def test_named_profiles_are_isolated_and_folder_binding_persists(self):
        home, cfg = bridge.select_project(self.base, 'my app', self.folder)
        other, other_cfg = bridge.select_project(self.base, 'another')
        self.assertNotEqual(home, other)
        self.assertNotEqual((home / 'token').read_text(), (other / 'token').read_text())
        selected, saved = bridge.select_project(self.base, 'my app')
        self.assertEqual(selected, home)
        self.assertEqual(saved['project']['folder'], str(self.folder))
        with self.assertRaises(ValueError):
            bridge.select_project(self.base, 'my app', self.folder / 'different')
        bridge.Store(home, cfg).enqueue('user', 'claude', 'hi')
        self.assertEqual(bridge.Store(other, other_cfg).list(), [])

    def test_native_project_creation_and_reuse(self):
        home, cfg = bridge.select_project(self.base, 'custom name', self.folder)
        registry = Path(self.tmp.name) / 'native-projects'
        cfg['agents']['gemini']['projects_directory'] = str(registry)
        def create(command, **kwargs):
            self.assertIn('--new-project', command)
            self.assertEqual(kwargs['input'], '')
            registry.mkdir()
            (registry / 'native-id.json').write_text(json.dumps({'id':'native-id','name':'workspace','projectResources':{'resources':[{'folderUri':self.folder.as_uri()}]}}))
            return subprocess.CompletedProcess(command, 0)
        with mock.patch.object(bridge.subprocess, 'run', side_effect=create) as call:
            bridge.ensure_project(home, cfg, bridge.NullLog())
            self.assertEqual(call.call_count, 1)
        self.assertEqual(cfg['project']['antigravity_project_id'], 'native-id')
        self.assertEqual(json.loads((registry / 'native-id.json').read_text())['name'], 'custom name')
        with mock.patch.object(bridge.subprocess, 'run') as call:
            bridge.ensure_project(home, cfg, bridge.NullLog())
            call.assert_not_called()
        # Provider subprocesses run in the selected folder.
        cfg['agents']['claude'] = {'command':[sys.executable, '-c', 'import os; print(os.getcwd())'], 'format':'text'}
        worker = bridge.Worker(home, cfg, bridge.Store(home, cfg))
        self.assertEqual(worker.run_provider('claude', 'hi'), str(self.folder))

    def test_antigravity_uses_bound_native_project(self):
        home, cfg = bridge.select_project(self.base, 'app', self.folder)
        self.folder.mkdir()
        cfg['project']['antigravity_project_id'] = 'native-id'
        script = Path(self.tmp.name) / 'fake.py'
        script.write_text("import json,sys,os\nsys.stdin.read()\nassert sys.argv[sys.argv.index('--project')+1]=='native-id'\nassert sys.argv[sys.argv.index('--add-dir')+1]==os.getcwd()\nprint(json.dumps({'event':'result','result':{'status':'SUCCESS','response':'BOUND'}}))\n")
        cfg['agents']['gemini']['command'] = [sys.executable, str(script)]
        worker = bridge.Worker(home, cfg, bridge.Store(home, cfg))
        self.assertEqual(worker.run_provider('gemini', 'hi'), 'BOUND')



class StartupTests(unittest.TestCase):
    def test_flag_modes_do_not_prompt(self):
        import manage
        with tempfile.TemporaryDirectory() as temporary:
            args = [bridge.__file__, '--home', temporary, '--project', 'demo', '--foreground']
            with mock.patch.object(sys, 'argv', args), mock.patch.object(bridge, 'interactive_options', side_effect=AssertionError('unexpected prompt')), mock.patch.object(bridge, 'serve') as serve:
                bridge.main()
                self.assertEqual(serve.call_args.args[1]['project']['name'], 'demo')
            args[-1] = '--background'
            with mock.patch.object(sys, 'argv', args), mock.patch.object(bridge, 'interactive_options', side_effect=AssertionError('unexpected prompt')), mock.patch.object(manage, 'execute') as start:
                bridge.main()
                self.assertEqual(start.call_args.args[-1], 'start')

    def test_interactive_startup(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = str(Path(temporary).resolve() / 'app')
            with mock.patch.object(sys, 'argv', [bridge.__file__, '--home', temporary]), mock.patch.object(sys.stdin, 'isatty', return_value=True), mock.patch('builtins.input', side_effect=['demo', folder, '8770']), mock.patch.object(bridge, 'serve') as serve:
                bridge.main()
                cfg = serve.call_args.args[1]
                self.assertEqual(cfg['project']['name'], 'demo')
                self.assertEqual(cfg['project']['folder'], folder)
                self.assertEqual(cfg['port'], 8770)
        with mock.patch.object(sys.stdin, 'isatty', return_value=False):
            with self.assertRaisesRegex(ValueError, 'needs a terminal'):
                bridge.interactive_options()



class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        cfg = bridge.initialize(self.home)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            cfg['port'] = sock.getsockname()[1]
        fake = self.home / 'fake.py'
        fake.write_text('''import json, sys
name = sys.argv[1]
prompt = sys.stdin.read()
next_agent = {'chatgpt':'gemini','gemini':'claude'}.get(name)
reply = {'reply': name + ' received', 'messages': ([{'to':next_agent,'body':'Continue'}] if next_agent else [])}
print(json.dumps(reply))
''')
        for name in bridge.AGENTS:
            cfg['agents'][name] = {'command': [sys.executable, str(fake), name], 'format': 'text'}
        (self.home / 'config.json').write_text(json.dumps(cfg))
        self.cfg = cfg
        self.proc = subprocess.Popen([sys.executable, bridge.__file__, '--home', str(self.home), 'serve'], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            try:
                bridge.request(self.home, cfg, '/health')
                break
            except OSError:
                time.sleep(0.03)
        else:
            self.fail('daemon did not start')

    def tearDown(self):
        self.proc.terminate()
        self.proc.communicate(timeout=10)
        self.tmp.cleanup()

    def test_three_way_routing_auth_mcp_and_restart(self):
        queued = bridge.request(self.home, self.cfg, '/messages', {'to': 'chatgpt', 'body': 'Hello'})
        for _ in range(100):
            rows = bridge.request(self.home, self.cfg, '/messages')
            if len(rows) == 3 and rows[-1]['state'] == 'done':
                break
            time.sleep(.03)
        self.assertEqual([row['recipient'] for row in rows], list(bridge.AGENTS))
        self.assertTrue(all(row['state'] == 'done' for row in rows))
        self.assertEqual(rows[1]['sender'], 'chatgpt')
        self.assertEqual(rows[2]['sender'], 'gemini')
        self.assertTrue(all(row['conversation'] == queued['conversation'] for row in rows))
        self.assertEqual(rows[2]['parent'], rows[1]['id'])
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f'http://127.0.0.1:{self.cfg["port"]}/health')
        self.assertEqual(error.exception.code, 401)
        records = [json.loads(line) for line in (self.home / 'operations.jsonl').read_text().splitlines()]
        self.assertTrue(any(e['event'] == 'http_response' and e['status_code'] == 401 for e in records))
        completed = [e for e in records if e['event'] == 'delivery_completed']
        self.assertEqual([e['recipient'] for e in completed], list(bridge.AGENTS))
        self.assertEqual(completed[-1]['response_chars'], len('claude received'))
        error.exception.close()
        payload = '\n'.join(json.dumps(x) for x in [
            {'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-06-18'}},
            {'jsonrpc':'2.0','method':'notifications/initialized'},
            {'jsonrpc':'2.0','id':2,'method':'tools/list'},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'bridge_messages','arguments':{'conversation':queued['conversation']}}},
            {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'bridge_send','arguments':{'to':'claude','body':'Hi from MCP'}}},
        ]) + '\n'
        result = subprocess.run([sys.executable, bridge.__file__, '--home', str(self.home), 'mcp', '--identity', 'gemini'], input=payload, text=True, capture_output=True, timeout=10)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(len(replies), 4)
        self.assertEqual(len(replies[1]['result']['tools']), 7)
        self.assertEqual(len(json.loads(replies[2]['result']['content'][0]['text'])), 3)
        self.assertFalse(replies[3]['result'].get('isError', False))
        rows = bridge.request(self.home, self.cfg, '/messages')
        self.assertEqual(rows[-1]['sender'], 'gemini')
        self.assertEqual(len(bridge.Store(self.home, self.cfg).list()), 4)
        stopped = bridge.request(self.home, self.cfg, '/stop', {'conversation': queued['conversation']})
        self.assertTrue(stopped['stopped'])
        with self.assertRaises(ValueError):
            bridge.request(self.home, self.cfg, '/messages', {'to':'claude','body':'again','conversation':queued['conversation']})

    def test_context_api_and_mcp(self):
        record = bridge.request(self.home, self.cfg, '/context/file', {'path':'brief.md','content':'context content','expected_version':0,'writer':'chatgpt'})
        self.assertEqual(record['version'], 1)
        with self.assertRaisesRegex(ValueError, 'context_conflict'):
            bridge.request(self.home, self.cfg, '/context/file', {'path':'brief.md','content':'stale','expected_version':0})
        calls = [
            {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'bridge_context_read','arguments':{'path':'brief.md'}}},
            {'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'bridge_context_write','arguments':{'path':'brief.md','content':'updated context','expected_version':1}}},
            {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'bridge_context_list','arguments':{}}},
            {'jsonrpc':'2.0','id':4,'method':'tools/call','params':{'name':'bridge_send','arguments':{'to':'claude','body':'read brief','context_files':['brief.md']}}},
        ]
        result = subprocess.run([sys.executable, bridge.__file__, '--home', str(self.home), 'mcp', '--identity', 'gemini'], input=''.join(json.dumps(call)+'\n' for call in calls), text=True, capture_output=True, timeout=10)
        replies = [json.loads(line)['result'] for line in result.stdout.splitlines()]
        self.assertTrue(all(not reply.get('isError') for reply in replies), replies)
        read = json.loads(replies[0]['content'][0]['text'])
        written = json.loads(replies[1]['content'][0]['text'])
        self.assertEqual(read['content'], 'context content')
        self.assertEqual(written['version'], 2)
        self.assertEqual(written['writer'], 'gemini')
        self.assertEqual(self.store_context_read()['content'], 'updated context')
        self.assertEqual(bridge.request(self.home, self.cfg, '/messages')[-1]['context_files'], ['brief.md'])

    def store_context_read(self):
        return bridge.request(self.home, self.cfg, '/context/file?path=brief.md')

    def test_background_management(self):
        manager = str(Path(bridge.__file__).with_name('manage.py'))
        command = [sys.executable, manager, '--home', str(self.home)]
        self.proc.terminate()
        self.proc.communicate(timeout=10)
        try:
            result = subprocess.run(command + ['start'], text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('background', result.stdout)
            again = subprocess.run(command + ['start'], text=True, capture_output=True, timeout=15)
            self.assertEqual(again.returncode, 0, again.stderr)
            self.assertIn('Already running', again.stdout)
            restarted = subprocess.run(command + ['restart'], text=True, capture_output=True, timeout=20)
            self.assertEqual(restarted.returncode, 0, restarted.stderr)
            state = subprocess.run(command + ['status'], text=True, capture_output=True, timeout=10)
            self.assertEqual(state.returncode, 0, state.stderr)
            self.assertIn('Running', state.stdout)
        finally:
            subprocess.run(command + ['stop'], text=True, capture_output=True, timeout=20)
        self.assertFalse((self.home / 'daemon.pid').exists())
        events = [json.loads(line)['event'] for line in (self.home / 'operations.jsonl').read_text().splitlines()]
        self.assertIn('daemon_started', events)
        self.assertIn('daemon_stopped', events)

    def test_daemon_exclusivity_and_bad_request(self):
        other = subprocess.run([sys.executable, bridge.__file__, '--home', str(self.home), 'serve'], capture_output=True, text=True, timeout=5)
        self.assertNotEqual(other.returncode, 0)
        self.assertIn('already running', other.stderr)
        with self.assertRaises(ValueError):
            bridge.request(self.home, self.cfg, '/messages', {'to': 'unknown', 'body': 'x'})


if __name__ == '__main__':
    unittest.main()
