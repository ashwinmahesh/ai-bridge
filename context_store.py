"""Project context documents: transactional versions plus readable file mirrors."""
import fcntl
import json
import os
import re
import sqlite3
from pathlib import PurePosixPath
import tempfile
import time

MAX_FILES = 10
MAX_DOCUMENT_BYTES = 64000
MAX_SNAPSHOT_BYTES = 128000


class ContextConflict(ValueError):
    pass


def context_path(value):
    if not isinstance(value, str) or not value or len(value) > 240 or '\x00' in value or '\\' in value:
        raise ValueError('Context path must be a relative document name')
    path = PurePosixPath(value)
    if not path.parts or path.is_absolute() or str(path) != value or any(part in ('.', '..') or part.startswith('.') for part in path.parts):
        raise ValueError('Invalid context path; absolute paths, traversal, and hidden names are forbidden')
    return value


def context_paths(values):
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_FILES:
        raise ValueError(f'Select at most {MAX_FILES} context files')
    result = [context_path(value) for value in values]
    if len(set(result)) != len(result):
        raise ValueError('Duplicate context file selection')
    return result


class ContextStore:
    def __init__(self, store, home):
        self.store, self.home = store, home
        self.directory = home / 'context'
        if self.directory.is_symlink():
            raise ValueError('Context directory cannot be a symlink')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        with store.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS context_documents (
                    path TEXT PRIMARY KEY, content TEXT NOT NULL,
                    version INTEGER NOT NULL, updated REAL NOT NULL, writer TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS context_revisions (
                    path TEXT NOT NULL, version INTEGER NOT NULL, content TEXT NOT NULL,
                    updated REAL NOT NULL, writer TEXT NOT NULL,
                    PRIMARY KEY(path,version));
            ''')
        self.publish([], repair=True)

    @staticmethod
    def record(row, path):
        if row is None:
            return {'path': path, 'version': 0, 'content': '', 'exists': False, 'updated': None, 'writer': None}
        return {**dict(row), 'exists': True}

    def _read(self, db, path, version=None):
        context_path(path)
        if version is None:
            row = db.execute('SELECT * FROM context_documents WHERE path=?', (path,)).fetchone()
        else:
            if type(version) is not int or version < 0:
                raise ValueError('Invalid context version')
            row = db.execute('SELECT * FROM context_revisions WHERE path=? AND version=?', (path, version)).fetchone()
            if row is None and version != 0:
                raise ValueError(f'Context version not found: {path}@{version}')
        return self.record(row, path)

    def read(self, path, version=None):
        with self.store.db() as db:
            return self._read(db, path, version)

    def list(self):
        with self.store.db() as db:
            return [{'path': row['path'], 'version': row['version'], 'updated': row['updated'], 'writer': row['writer'],
                     'chars': len(row['content']), 'bytes': len(row['content'].encode())}
                    for row in db.execute('SELECT * FROM context_documents ORDER BY path')]

    def versions(self, db, paths):
        return {path: self._read(db, path)['version'] for path in context_paths(paths)}

    def snapshot(self, versions):
        with self.store.db() as db:
            # Immutable revisions make this snapshot stable even if the orchestrator edits later.
            result = [self._read(db, path, version) for path, version in versions.items()]
        if sum(len(item['content'].encode()) for item in result) > MAX_SNAPSHOT_BYTES:
            raise ValueError('Selected context exceeds the 128000-byte snapshot budget')
        return result

    def apply(self, db, updates, writer, allowed=None, versions=None):
        if not isinstance(updates, list) or len(updates) > MAX_FILES:
            raise ValueError(f'At most {MAX_FILES} context updates per turn')
        changed, seen = [], set()
        for update in updates:
            if not isinstance(update, dict):
                raise ValueError('Invalid context update')
            path = context_path(update.get('path'))
            self.physical_path(path)
            if path in seen:
                raise ValueError('Duplicate context update')
            seen.add(path)
            content = update.get('content')
            if not isinstance(content, str) or len(content) > 32000 or len(content.encode()) > MAX_DOCUMENT_BYTES:
                raise ValueError('Context content must be text, at most 32000 characters / 64000 bytes')
            expected = update.get('expected_version')
            if expected is not None and (type(expected) is not int or expected < 0):
                raise ValueError('expected_version must be a nonnegative integer')
            if allowed is not None:
                if path not in allowed:
                    raise ValueError(f'Context update was not selected for this turn: {path}')
                if expected is None or expected != versions.get(path):
                    raise ContextConflict(f'Context update must cite delivered version for {path}')
            current = self._read(db, path)
            if expected is not None and expected != current['version']:
                raise ContextConflict(f'Context changed: {path}; expected version {expected}, current version {current["version"]}')
            version, updated = current['version'] + 1, time.time()
            db.execute('INSERT INTO context_documents(path,content,version,updated,writer) VALUES (?,?,?,?,?) '
                       'ON CONFLICT(path) DO UPDATE SET content=excluded.content,version=excluded.version,updated=excluded.updated,writer=excluded.writer',
                       (path, content, version, updated, writer))
            db.execute('INSERT INTO context_revisions(path,version,content,updated,writer) VALUES (?,?,?,?,?)',
                       (path, version, content, updated, writer))
            changed.append({'path': path, 'content': content, 'version': version, 'updated': updated, 'writer': writer, 'exists': True})
        return changed

    def write(self, path, content, expected_version=None, writer='user'):
        if writer not in ('user', 'chatgpt', 'gemini', 'claude'):
            raise ValueError('Invalid context writer')
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = self.apply(db, [{'path': path, 'content': content, 'expected_version': expected_version}], writer)
        self.publish(changed)
        return changed[0]

    def scaffold(self, task_id=None, writer='user'):
        """Create the standard layout atomically; preserve existing versions/content."""
        if writer not in ('user', 'chatgpt', 'gemini', 'claude'):
            raise ValueError('Invalid context writer')
        if task_id is not None and (not isinstance(task_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', task_id)):
            raise ValueError('Task ID must contain 1–64 letters, digits, underscores, or hyphens')
        templates = {
            'project/brief.md': '# Project brief\n\n## Goal\nNot recorded yet.\n\n## Scope\nNot recorded yet.\n\n## Constraints\nNot recorded yet.\n',
            'project/decisions.md': '# Project decisions\n\nRecord accepted decisions with rationale and source. Keep proposals separate from accepted decisions.\n\nNo decisions recorded yet.\n',
        }
        if task_id is not None:
            templates.update({
                f'tasks/{task_id}/requirements.md': f'# Task {task_id}: requirements\n\n## Objective\nNot recorded yet.\n\n## Acceptance criteria\nNot recorded yet.\n\n## Constraints and context sources\nNot recorded yet.\n',
                f'tasks/{task_id}/findings.md': f'# Task {task_id}: findings\n\nRecord verified findings, evidence, agent attribution, and unresolved questions.\n\nNo findings recorded yet.\n',
            })
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            updates, existing = [], []
            for path, content in templates.items():
                current = self._read(db, path)
                if current['exists']:
                    existing.append({'path': path, 'version': current['version']})
                else:
                    updates.append({'path': path, 'content': content, 'expected_version': 0})
            changed = self.apply(db, updates, writer)
        self.publish(changed)
        self.store.log.emit('context_scaffolded', task_id=task_id, created=[item['path'] for item in changed], existing=[item['path'] for item in existing], writer=writer)
        return {'created': [{'path': item['path'], 'version': item['version']} for item in changed],
                'existing': existing, 'context_files': list(templates)}

    def physical_path(self, path):
        context_path(path)
        cursor = self.directory
        if cursor.is_symlink():
            raise ValueError('Context directory cannot be a symlink')
        for part in PurePosixPath(path).parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ValueError('Context file paths cannot traverse symlinks')
        return cursor

    def publish(self, changed, repair=False, **metadata):
        try:
            self._publish(changed, repair, **metadata)
        except (OSError, ValueError, sqlite3.Error) as exc:
            self.store.log.emit('context_mirror_failed', error_type=type(exc).__name__)

    def _publish(self, changed, repair=False, **metadata):
        # SQLite is authoritative. Serialize mirror generation and reread the newest
        # version inside the lock, so concurrent writers cannot publish stale content.
        paths = [item['path'] for item in self.list()] if repair else [item['path'] for item in changed]
        if paths:
            with (self.home / 'context-mirror.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                for path in paths:
                    try:
                        target = self.physical_path(path)
                        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                        value = self.read(path)
                        temporary = None
                        try:
                            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent, delete=False) as output:
                                temporary = output.name
                                output.write(value['content'])
                                output.flush()
                                os.fsync(output.fileno())
                            os.replace(temporary, target)
                        finally:
                            if temporary and os.path.exists(temporary):
                                os.unlink(temporary)
                    except (OSError, ValueError) as exc:
                        self.store.log.emit('context_mirror_failed', path=path, error_type=type(exc).__name__)
        for item in changed:
            self.store.log.emit('context_updated', path=item['path'], version=item['version'], writer=item['writer'],
                                context_chars=len(item['content']), context_bytes=len(item['content'].encode()), **metadata)
