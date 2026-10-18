"""Multi-project, authenticated local HTTP service; no third-party runtime dependencies."""
import collections
import copy
import fcntl
import hmac
import json
import os
from pathlib import Path
import queue
import shutil
import signal
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bridge
from context_store import ContextConflict
from provider_options import validate_options

VERSION = '2.0.0'
VERSIONS = ('2025-03-26', '2025-06-18')


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(value, handle, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Events:
    def __init__(self, home, console):
        self.log = bridge.EventLog(home, console=console)
        self.lock = threading.Lock()
        self.sequence = 0
        self.history = collections.deque(maxlen=1000)
        self.subscribers = set()

    def emit(self, event, **fields):
        self.log.emit(event, **fields)
        with self.lock:
            self.sequence += 1
            value = dict(id=self.sequence, timestamp=time.time(), event=event, **fields)
            self.history.append(value)
            for subscriber in self.subscribers:
                try:
                    subscriber.put_nowait(value)
                except queue.Full:
                    # Close slow consumers so loss cannot appear as a complete stream.
                    while not subscriber.empty():
                        subscriber.get_nowait()
                    subscriber.put_nowait(None)

    def subscribe(self, after):
        channel = queue.Queue(maxsize=1024)
        with self.lock:
            for value in self.history:
                if value['id'] > after:
                    channel.put_nowait(value)
            self.subscribers.add(channel)
        return channel


class ProjectLog:
    def __init__(self, events, name):
        self.events, self.name = events, name

    def emit(self, event, **fields):
        self.events.emit(event, project=self.name, **fields)


class Runtime:
    def __init__(self, home, cfg, events):
        self.home, self.cfg = home, cfg
        self.lock = (home / 'daemon.lock').open('a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            log = ProjectLog(events, cfg['project']['name'])
            bridge.ensure_project(home, cfg, log)
            self.store = bridge.Store(home, cfg, log)
            self.store.recover()
            self.worker = bridge.Worker(home, cfg, self.store)
            self.thread = threading.Thread(target=self.worker.run, daemon=True)
            self.thread.start()
        except BaseException:
            self.lock.close()
            raise

    def close(self):
        self.worker.stopping.set()
        self.worker.kill()
        self.thread.join(timeout=10)
        self.lock.close()


def providers(value):
    bridge.require(isinstance(value, dict) and set(value) == set(bridge.AGENTS), 'agents must define chatgpt, gemini, claude')
    for name, agent in value.items():
        bridge.require(isinstance(agent, dict), 'agent must be an object')
        command = agent.get('command')
        bridge.require(isinstance(command, list) and command and all(isinstance(x, str) and '\x00' not in x for x in command) and command[0], 'command must be a nonempty argv array')
        bridge.require(agent.get('format', 'text') in ('text', 'codex', 'claude', 'gemini', 'antigravity'), 'Unknown output format')
        bridge.require(agent.get('input_format') in (None, 'antigravity'), 'Unknown input format')
        validate_options(name, agent.get('model'), agent.get('effort'))
    return copy.deepcopy(value)


class Service:
    def __init__(self, home, console=True):
        self.home = home.expanduser().resolve()
        self.cfg = bridge.initialize(self.home)
        self.events = Events(self.home, console)
        self.mutex = threading.RLock()
        self.runtimes = {}
        self.stopping = threading.Event()

    def projects(self):
        return [dict(id=p.parent.name, **json.loads(p.read_text()))
                for p in sorted((self.home / 'projects').glob('*/project.json'))]

    def project(self, identifier):
        bridge.require(isinstance(identifier, str) and identifier and '/' not in identifier and identifier not in ('.', '..'), 'Invalid project ID')
        with self.mutex:
            if identifier not in self.runtimes:
                home = self.home / 'projects' / identifier
                if not (home / 'project.json').is_file():
                    raise LookupError('Project not found')
                self.runtimes[identifier] = Runtime(home, bridge.initialize(home), self.events)
            return self.runtimes[identifier]

    def create(self, body):
        bridge.require(isinstance(body.get('name'), str) and body['name'].strip(), 'name is required')
        bridge.require('/' not in body['name'] and not body['name'].startswith(('.', '~')), 'Use name plus optional absolute folder')
        folder = body.get('folder')
        bridge.require(folder is None or isinstance(folder, str) and Path(folder).is_absolute(), 'folder must be absolute')
        with self.mutex:
            home, _ = bridge.select_project(self.home, body['name'], folder)
            runtime = self.project(home.name)
            return dict(id=home.name, **runtime.cfg['project'])

    def config(self, body=None, identifier=None):
        with self.mutex:
            runtime = self.project(identifier) if identifier else None
            cfg = runtime.cfg if runtime else self.cfg
            if body is not None:
                bridge.require(set(body) == {'agents'}, 'Provide agents only; timeout/budget are currently file-configured')
                agents = providers(body['agents'])
                updated = dict(cfg, agents=agents)
                atomic_json((runtime.home if runtime else self.home) / 'config.json', {k: v for k, v in updated.items() if k != 'project'})
                if runtime:
                    # Replace the mapping atomically. An active provider already captured
                    # its argv; the next provider invocation observes the new defaults.
                    runtime.cfg = updated
                    runtime.worker.cfg = updated
                    runtime.store.cfg = updated
                else:
                    self.cfg = updated
                self.events.emit('configuration_updated', project=identifier)
            cfg = runtime.cfg if runtime else self.cfg
            return {'agents': cfg['agents'], 'available': {n: bool(shutil.which(a['command'][0])) for n, a in cfg['agents'].items()}}

    def dispatch(self, method, path, query, body):
        if path in ('/health', '/v1/health') and method == 'GET':
            return {'ok': True, 'version': VERSION, 'pid': os.getpid(), 'capabilities': {'multi_project': True, 'http_mcp': True, 'model_effort_selection': True}, 'projects': len(self.projects())}
        if path == '/v1/projects':
            if method == 'GET':
                return self.projects()
            if method == 'POST':
                return self.create(body)
        if path == '/v1/providers' and method in ('GET', 'PUT'):
            return self.config(body if method == 'PUT' else None)
        if path == '/v1/logs' and method == 'GET':
            limit = min(1000, max(1, int(query.get('limit', ['100'])[0])))
            with (self.home / 'operations.jsonl').open() as handle:
                return [json.loads(line) for line in collections.deque(handle, maxlen=limit)]
        parts = path.strip('/').split('/')
        if len(parts) >= 3 and parts[:2] == ['v1', 'projects']:
            identifier = parts[2]
            runtime = self.project(identifier)
            suffix = '/'.join(parts[3:])
            if suffix == '' and method == 'GET':
                return dict(id=identifier, **runtime.cfg['project'])
            if suffix == 'providers' and method in ('GET', 'PUT'):
                return self.config(body if method == 'PUT' else None, identifier)
            store = runtime.store
            if method == 'GET':
                if suffix == 'messages':
                    return store.list(query.get('conversation', [None])[0], query.get('after', ['0'])[0])
                if suffix == 'context':
                    return store.context.list()
                if suffix == 'context/file':
                    version = query.get('version', [None])[0]
                    return store.context.read(query.get('path', [None])[0], int(version) if version is not None else None)
            if method == 'POST':
                if suffix == 'messages':
                    with self.mutex:
                        return store.enqueue(body.get('sender', 'user'), body.get('to'), body.get('body'), body.get('conversation'), context_files=body.get('context_files'), model=body.get('model'), effort=body.get('effort'))
                if suffix == 'stop':
                    return store.stop(body.get('conversation'))
                if suffix == 'context/scaffold':
                    return store.context.scaffold(body.get('task_id'), body.get('writer', 'user'))
                if suffix == 'context/file':
                    return store.context.write(body.get('path'), body.get('content'), body.get('expected_version'), body.get('writer', 'user'))
        raise LookupError('Endpoint not found')

    def rpc(self, identifier, identity, message):
        bridge.require(isinstance(message, dict) and message.get('jsonrpc') == '2.0', 'Expected JSON-RPC 2.0 object')
        if 'id' not in message:
            return None
        method, params = message.get('method'), message.get('params', {})
        bridge.require(isinstance(params, dict), 'params must be an object')
        result = None
        if method == 'initialize':
            version = params.get('protocolVersion')
            result = {'protocolVersion': version if version in VERSIONS else VERSIONS[-1], 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'ai-bridge', 'version': VERSION}}
        elif method == 'ping':
            result = {}
        elif method == 'tools/list':
            result = {'tools': bridge.TOOLS}
        elif method == 'tools/call':
            try:
                name, args = params.get('name'), params.get('arguments', {})
                schema = next((t['inputSchema'] for t in bridge.TOOLS if t['name'] == name), None)
                bridge.require(schema is not None and isinstance(args, dict), 'Unknown tool or invalid arguments')
                bridge.require(not (set(args) - set(schema['properties'])) and set(schema.get('required', [])) <= set(args), 'Invalid tool arguments')
                endpoints = {'bridge_send': ('POST', 'messages'), 'bridge_messages': ('GET', 'messages'), 'bridge_stop': ('POST', 'stop'), 'bridge_context_scaffold': ('POST', 'context/scaffold'), 'bridge_context_list': ('GET', 'context'), 'bridge_context_read': ('GET', 'context/file'), 'bridge_context_write': ('POST', 'context/file')}
                if name == 'bridge_status':
                    value = self.dispatch('GET', '/health', {}, None)
                else:
                    verb, endpoint = endpoints[name]
                    body = dict(args)
                    if name == 'bridge_send':
                        body['sender'] = identity
                    if name in ('bridge_context_scaffold', 'bridge_context_write'):
                        body['writer'] = identity
                    query = {k: [v] for k, v in args.items()} if verb == 'GET' else {}
                    value = self.dispatch(verb, f'/v1/projects/{identifier}/{endpoint}', query, body)
                result = {'content': [{'type': 'text', 'text': json.dumps(value)}]}
            except (ValueError, TypeError, LookupError) as exc:
                result = {'isError': True, 'content': [{'type': 'text', 'text': str(exc)}]}
        else:
            return {'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Method not found'}}
        return {'jsonrpc': '2.0', 'id': message['id'], 'result': result}

    def close(self):
        self.stopping.set()
        for runtime in self.runtimes.values():
            runtime.close()
        self.events.emit('service_stopped')
        self.events.log.close()


def serve(home, port=8765, console=True):
    home = home.expanduser().resolve()
    bridge.initialize(home)
    with (home / 'service.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Service already running for this home') from None
        service = Service(home, console)
        token = (home / 'token').read_text().strip()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def respond(self, status, value=None):
                raw = b'' if value is None else json.dumps(value).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(raw)
                service.events.emit('http_response', method=self.command, path=urllib.parse.urlsplit(self.path).path, status_code=status, response_bytes=len(raw), duration_ms=round((time.monotonic()-self.started)*1000))

            def handle_request(self):
                self.started = time.monotonic()
                self.connection.settimeout(15)
                try:
                    if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                        return self.respond(401, {'error': 'Unauthorized'})
                    origin = self.headers.get('Origin')
                    allowed = {f'http://127.0.0.1:{server.server_port}', f'http://localhost:{server.server_port}'}
                    if origin is not None and origin not in allowed:
                        return self.respond(403, {'error': 'Origin rejected'})
                    if self.headers.get('Host') not in {f'127.0.0.1:{server.server_port}', f'localhost:{server.server_port}'}:
                        return self.respond(403, {'error': 'Host rejected'})
                    url = urllib.parse.urlsplit(self.path)
                    query = urllib.parse.parse_qs(url.query)
                    body = None
                    if self.command in ('POST', 'PUT'):
                        bridge.require(self.headers.get_content_type() == 'application/json', 'Expected application/json')
                        length = int(self.headers.get('Content-Length', '0'))
                        bridge.require(0 < length <= bridge.MAX_BODY, 'Invalid request size')
                        body = json.loads(self.rfile.read(length))
                        bridge.require(isinstance(body, dict), 'Expected JSON object')
                    if url.path.endswith('/mcp'):
                        parts = url.path.strip('/').split('/')
                        if len(parts) != 4 or parts[:2] != ['v1', 'projects']:
                            raise LookupError('Use /v1/projects/ID/mcp')
                        service.project(parts[2])
                        if self.command != 'POST':
                            return self.respond(405, {'error': 'Use POST; MCP server SSE is not offered'})
                        version = self.headers.get('MCP-Protocol-Version', VERSIONS[0])
                        bridge.require(version in VERSIONS, 'Unsupported MCP-Protocol-Version')
                        accept = self.headers.get('Accept', '')
                        bridge.require('application/json' in accept and 'text/event-stream' in accept, 'Accept must include application/json and text/event-stream')
                        identity = query.get('identity', ['chatgpt'])[0]
                        bridge.require(identity in (*bridge.AGENTS, 'user'), 'Invalid identity')
                        value = service.rpc(parts[2], identity, body)
                        return self.respond(202 if value is None else 200, value)
                    if url.path == '/v1/events' and self.command == 'GET':
                        after = int(query.get('after', [self.headers.get('Last-Event-ID', '0')])[0])
                        channel = service.events.subscribe(after)
                        try:
                            self.send_response(200)
                            self.send_header('Content-Type', 'text/event-stream')
                            self.send_header('Cache-Control', 'no-store')
                            self.end_headers()
                            self.wfile.write(b': connected\n\n')
                            self.wfile.flush()
                            while not service.stopping.is_set():
                                try:
                                    event = channel.get(timeout=2)
                                except queue.Empty:
                                    self.wfile.write(b': heartbeat\n\n')
                                else:
                                    if event is None:
                                        break
                                    self.wfile.write(f'id: {event["id"]}\ndata: {json.dumps(event)}\n\n'.encode())
                                self.wfile.flush()
                        finally:
                            with service.events.lock:
                                service.events.subscribers.discard(channel)
                        return
                    if url.path == '/v1/shutdown' and self.command == 'POST':
                        self.respond(200, {'stopping': True})
                        threading.Thread(target=server.shutdown, daemon=True).start()
                        return
                    return self.respond(200, service.dispatch(self.command, url.path, query, body))
                except ContextConflict as exc:
                    self.respond(409, {'error': str(exc), 'type': 'context_conflict'})
                except LookupError as exc:
                    self.respond(404, {'error': str(exc)})
                except (ValueError, TypeError) as exc:
                    self.respond(400, {'error': str(exc)})
                except (BrokenPipeError, ConnectionResetError, TimeoutError):
                    pass
                except Exception as exc:
                    service.events.emit('http_failed', error_type=type(exc).__name__)
                    self.respond(500, {'error': 'Internal error; inspect local state'})

            do_GET = do_POST = do_PUT = do_DELETE = handle_request

        server = None
        try:
            server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
            server.daemon_threads = True
            # Recover persisted queues without requiring another API call after login.
            for project in service.projects():
                try:
                    service.project(project['id'])
                except Exception as exc:
                    service.events.emit('project_start_failed', project=project['id'], error_type=type(exc).__name__)
            atomic_json(home / 'service.json', {'url': f'http://127.0.0.1:{server.server_port}', 'token_file': str(home / 'token'), 'pid': os.getpid(), 'version': VERSION})
            service.events.emit('service_started', port=server.server_port)
            print(f'AI Bridge API: http://127.0.0.1:{server.server_port}\nDiscovery: {home / "service.json"}', flush=True)
            def shutdown(*_):
                threading.Thread(target=server.shutdown, daemon=True).start()
            signal.signal(signal.SIGTERM, shutdown)
            signal.signal(signal.SIGINT, shutdown)
            server.serve_forever(poll_interval=.2)
        finally:
            service.close()
            if server:
                server.server_close()
            (home / 'service.json').unlink(missing_ok=True)
