import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
from context_store import ContextConflict, context_path


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cfg = bridge.initialize(self.home)
        self.store = bridge.Store(self.home, self.cfg)

    def tearDown(self):
        self.tmp.cleanup()

    def run_worker(self, provider, expected_count=1):
        worker = bridge.Worker(self.home, self.cfg, self.store)
        worker.run_provider = provider
        thread = threading.Thread(target=worker.run)
        thread.start()
        try:
            for _ in range(200):
                rows = self.store.list()
                if len(rows) >= expected_count and all(row['state'] in ('done', 'failed', 'cancelled') for row in rows):
                    return rows
                time.sleep(.01)
            self.fail('Worker did not finish')
        finally:
            worker.stopping.set()
            thread.join(timeout=3)

    def test_versions_mirrors_and_restart(self):
        self.assertEqual(self.store.context.read('brief.md')['version'], 0)
        first = self.store.context.write('brief.md', 'one', 0, 'chatgpt')
        self.assertEqual(first['version'], 1)
        second = self.store.context.write('brief.md', 'two', 1, 'gemini')
        self.assertEqual(second['version'], 2)
        self.assertEqual((self.home / 'context/brief.md').read_text(), 'two')
        with self.assertRaises(ContextConflict):
            self.store.context.write('brief.md', 'stale', 1, 'claude')
        reopened = bridge.Store(self.home, self.cfg)
        self.assertEqual(reopened.context.read('brief.md')['content'], 'two')
        self.assertEqual(reopened.context.read('brief.md', 1)['content'], 'one')
        self.assertEqual(reopened.context.list()[0]['writer'], 'gemini')
        self.assertEqual((self.home / 'context/brief.md').stat().st_mode & 0o777, 0o600)

    def test_paths_and_symlinks(self):
        for path in ('.', '../token', '/tmp/context', 'a/../b', 'a//b', '.hidden', 'nested/.secret', 'a\\b'):
            with self.assertRaises(ValueError, msg=path):
                context_path(path)
        outside = self.home / 'outside'
        outside.mkdir()
        (self.home / 'context').mkdir(exist_ok=True)
        (self.home / 'context/link').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.context.write('link/file.md', 'escape', 0)
        self.assertFalse((outside / 'file.md').exists())

    def test_selected_context_and_inherited_updates(self):
        self.store.context.write('brief.md', 'SELECTED_BRIEF', 0)
        self.store.context.write('internal.md', 'HIDDEN_CONTEXT', 0)
        self.store.enqueue('chatgpt', 'gemini', 'Review', context_files=['brief.md', 'notes.md'])
        def provider(name, prompt):
            self.assertNotIn('HIDDEN_CONTEXT', prompt)
            self.assertIn('SELECTED_BRIEF', prompt)
            if name == 'gemini':
                return json.dumps({'reply': 'Reviewed', 'messages': [{'to': 'claude', 'body': 'Read notes'}],
                                   'context_updates': [{'path': 'notes.md', 'expected_version': 0, 'content': 'Gemini notes'}]})
            self.assertIn('Gemini notes', prompt)
            return json.dumps({'reply': 'Agreed', 'messages': [], 'context_updates': [{'path': 'notes.md', 'expected_version': 1, 'content': 'Gemini and Claude notes'}]})
        rows = self.run_worker(provider, 2)
        self.assertEqual([row['state'] for row in rows], ['done', 'done'])
        self.assertEqual(rows[1]['context_files'], ['brief.md', 'notes.md'])
        self.assertEqual(rows[1]['context_versions'], {'brief.md': 1, 'notes.md': 1})
        self.assertEqual(self.store.context.read('notes.md')['content'], 'Gemini and Claude notes')
        self.assertEqual(self.store.context.read('notes.md')['version'], 2)

    def test_conflict_preserves_reply_and_proposals_without_routing(self):
        self.store.context.write('notes.md', 'original', 0)
        self.store.enqueue('chatgpt', 'gemini', 'Review', context_files=['notes.md'])
        def provider(name, prompt):
            self.store.context.write('notes.md', 'orchestrator changed it', 1, 'chatgpt')
            return json.dumps({'reply': 'Useful reply', 'messages': [{'to': 'claude', 'body': 'continue'}],
                               'context_updates': [{'path': 'notes.md', 'expected_version': 1, 'content': 'stale edit'}]})
        rows = self.run_worker(provider)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['state'], 'failed')
        self.assertIn('Context changed', rows[0]['error'])
        self.assertEqual(rows[0]['result'], 'Useful reply')
        self.assertEqual(rows[0]['context_proposals'][0]['content'], 'stale edit')
        self.assertEqual(self.store.context.read('notes.md')['content'], 'orchestrator changed it')

    def test_unselected_update_and_route_subset_enforced(self):
        self.store.enqueue('chatgpt', 'gemini', 'Review', context_files=['allowed.md'])
        rows = self.run_worker(lambda *_: json.dumps({'reply': 'hi', 'messages': [], 'context_updates': [{'path': 'other.md', 'content': 'not allowed', 'expected_version': 0}]}))
        self.assertEqual(rows[0]['state'], 'failed')
        self.assertFalse(self.store.context.read('other.md')['exists'])
        self.store.enqueue('chatgpt', 'gemini', 'Review', context_files=['allowed.md'])
        rows = self.run_worker(lambda *_: json.dumps({'reply': 'hi', 'messages': [{'to':'claude','body':'continue','context_files':['not-selected.md']}]}), 2)
        self.assertEqual(rows[-1]['state'], 'failed')
        self.assertEqual(len(rows), 2)

    def test_context_batch_is_atomic(self):
        self.store.context.write('a.md', 'a', 0)
        self.store.context.write('b.md', 'b', 0)
        with self.assertRaises(ContextConflict):
            with self.store.db() as db:
                db.execute('BEGIN IMMEDIATE')
                self.store.context.apply(db, [{'path':'a.md','content':'changed','expected_version':1}, {'path':'b.md','content':'stale','expected_version':0}], 'gemini')
        self.assertEqual(self.store.context.read('a.md')['content'], 'a')

    def test_claim_uses_immutable_snapshot(self):
        self.store.context.write('brief.md', 'version one', 0)
        self.store.enqueue('user', 'gemini', 'hi', context_files=['brief.md'])
        message = self.store.claim()
        self.store.context.write('brief.md', 'version two', 1)
        snapshot = self.store.context.snapshot(message['context_versions'])
        self.assertEqual(snapshot[0]['content'], 'version one')


if __name__ == '__main__':
    unittest.main()
