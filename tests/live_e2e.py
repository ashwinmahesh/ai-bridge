#!/usr/bin/env python3
"""Explicit live check: sends one short message to agy and Claude using real logins."""
import argparse
import json
from pathlib import Path
import sys
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path(bridge.__file__).resolve().parent / '.bridge')
    parser.add_argument('--project', default='ai-bridge')
    parser.add_argument('--timeout', type=int, default=400)
    args = parser.parse_args()
    home, cfg = bridge.select_project(args.home, args.project)
    bridge.request(home, cfg, '/health')
    run_id = uuid.uuid4().hex[:12]
    pending = {}
    for recipient in ('gemini', 'claude'):
        marker = f'{recipient.upper()}_E2E_OK_{run_id}'
        body = ('Live AI bridge connectivity test. Do not use any tools. '
                'Return exactly this JSON object, with an empty messages array: '
                + json.dumps({'reply': marker, 'messages': []}))
        sent = bridge.request(home, cfg, '/messages', {'sender': 'chatgpt', 'to': recipient, 'body': body})
        pending[sent['id']] = {'agent': recipient, 'marker': marker, 'conversation': sent['conversation'], 'started': time.monotonic()}
        print(json.dumps({'event': 'queued', 'agent': recipient, **sent}), flush=True)
    results = []
    deadline = time.monotonic() + args.timeout
    while pending and time.monotonic() < deadline:
        for mid, info in list(pending.items()):
            rows = bridge.request(home, cfg, '/messages?' + bridge.urllib.parse.urlencode({'conversation': info['conversation']}))
            row = next((row for row in rows if row['id'] == mid), None)
            if row and row['state'] in ('done', 'failed', 'cancelled'):
                result = {'agent': info['agent'], 'id': mid, 'conversation': info['conversation'],
                          'state': row['state'], 'reply': row.get('result'), 'error': row.get('error'),
                          'elapsed_seconds': round(time.monotonic() - info['started'], 2),
                          'ok': row['state'] == 'done' and row.get('result') == info['marker']}
                results.append(result)
                print(json.dumps({'event': 'response', **result}), flush=True)
                del pending[mid]
        if pending:
            time.sleep(1)
    for mid, info in pending.items():
        results.append({'agent': info['agent'], 'id': mid, 'state': 'test_timeout', 'ok': False})
    final = {'ok': len(results) == 2 and all(result['ok'] for result in results), 'project': args.project, 'results': results}
    print(json.dumps(final, indent=2), flush=True)
    return 0 if final['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
