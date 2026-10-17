#!/usr/bin/env python3
"""Live shared-context test: orchestrator -> agy -> persisted notes -> Claude -> orchestrator."""
import argparse
import json
from pathlib import Path
import sys
import time
import urllib.parse
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge


def wait(home, cfg, sent, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = bridge.request(home, cfg, '/messages?' + urllib.parse.urlencode({'conversation': sent['conversation']}))
        row = next(row for row in rows if row['id'] == sent['id'])
        if row['state'] in ('done', 'failed', 'cancelled'):
            if row['state'] != 'done':
                raise RuntimeError(json.dumps(row))
            return row
        time.sleep(1)
    raise TimeoutError('Context E2E reply timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(bridge.__file__).resolve().parent / '.bridge')
    parser.add_argument('--project', default='ai-bridge')
    parser.add_argument('--timeout', type=int, default=200)
    args = parser.parse_args()
    home, cfg = bridge.select_project(args.home, args.project)
    token = uuid.uuid4().hex[:12]
    brief, notes = f'e2e/{token}/brief.md', f'e2e/{token}/findings.md'
    bridge.request(home, cfg, '/context/file', {'path': brief, 'content': f'PROBE_TOKEN: {token}\nProject brief for shared-context verification.', 'expected_version': 0, 'writer': 'chatgpt'})
    first = bridge.request(home, cfg, '/messages', {
        'sender': 'chatgpt', 'to': 'gemini', 'context_files': [brief, notes],
        'body': f'Read the PROBE_TOKEN from selected context file {brief}. Do not use tools. Reply GEMINI_CONTEXT_OK_ followed by that token. Use context_updates to create {notes} at its supplied version with exactly two lines: PROBE_TOKEN: <the token> and Gemini: OK. Return messages: []. Do not change the brief.'})
    print(json.dumps({'event': 'gemini_queued', **first}), flush=True)
    gemini = wait(home, cfg, first, args.timeout)
    if gemini['result'] != f'GEMINI_CONTEXT_OK_{token}':
        raise AssertionError('Gemini did not read the selected context token')
    shared = bridge.request(home, cfg, '/context/file?' + urllib.parse.urlencode({'path': notes}))
    if shared['version'] != 1 or token not in shared['content'] or 'Gemini: OK' not in shared['content']:
        raise AssertionError('Gemini context write was not persisted')
    print(json.dumps({'event': 'gemini_context_persisted', 'path': notes, 'version': shared['version']}), flush=True)
    second = bridge.request(home, cfg, '/messages', {
        'sender': 'chatgpt', 'to': 'claude', 'context_files': [notes],
        'body': f'Read the PROBE_TOKEN from selected context file {notes}. Do not use tools. Reply CLAUDE_CONTEXT_OK_ followed by that token. Use context_updates to replace {notes} at its supplied version, preserving its content and appending a new line Claude: OK. Return messages: [].'})
    print(json.dumps({'event': 'claude_queued', **second}), flush=True)
    claude = wait(home, cfg, second, args.timeout)
    final = bridge.request(home, cfg, '/context/file?' + urllib.parse.urlencode({'path': notes}))
    ok = (claude['result'] == f'CLAUDE_CONTEXT_OK_{token}' and final['version'] == 2 and token in final['content']
          and 'Gemini: OK' in final['content'] and 'Claude: OK' in final['content'])
    result = {'ok': ok, 'project': args.project, 'gemini_reply': gemini['result'], 'claude_reply': claude['result'],
              'context_file': notes, 'context_version': final['version'], 'context_writer': final['writer'], 'context_content': final['content']}
    print(json.dumps(result, indent=2), flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
