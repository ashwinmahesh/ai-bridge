# AI Bridge v2 API and agent reference

This is the multi-project API service (`ai-bridge` executable or `python3 ai_bridge.py`). The legacy `bridge.py` daemon has different, unversioned routes and one project per process; do not send v2 calls to it. One v2 process serves many projects on one localhost port. CLI targets remain Codex (`chatgpt`), Antigravity `agy` (`gemini`), Claude Code (`claude`); these are local working-directory/session groups, not browser-chat Projects.

## Discover and authenticate

macOS default private state: `~/Library/Application Support/AI Bridge`. Linux foreground state: `${XDG_DATA_HOME:-~/.local/share}/ai-bridge`. Override with `--home /absolute/path`. Read `service.json` in that directory for `url`, `token_file`, `pid`, and `version`. It exists only while the service runs, but an unclean exit can leave stale discovery: always check `/health`. Read `token_file` locally; never paste the token into messages, commit it, log it, or put it in a URL.

All endpoints require `Authorization: Bearer TOKEN`. JSON mutations require `Content-Type: application/json`; body limit 262144 bytes. Bind is exclusively `127.0.0.1`; proxies must not forward credentials. Host must be `127.0.0.1:PORT` or `localhost:PORT`. Browser Origin, when present, must match one of those HTTP origins. There is no CORS allowance. The token grants local control including provider command execution; give it only to trusted local clients. Sender/identity is attribution, not an authorization boundary.

```bash
STATE="$HOME/Library/Application Support/AI Bridge"
BASE=http://127.0.0.1:8765 # use service.json when port was customized
TOKEN="$(cat "$STATE/token")"
curl --noproxy '*' -sS --fail-with-body -H "Authorization: Bearer $TOKEN" "$BASE/health"
```

Examples below assume BASE and TOKEN from discovery. `PROJECT_ID`, `MESSAGE_ID`, and `CONVERSATION_ID` mean returned values, not literal defaults. A missing capability/version means this could be a legacy daemon: do not restart a user-managed process; report the mismatch.

## Routes

All successful JSON operations return HTTP 200, except MCP notifications (202, empty body). Errors have `{"error":"..."}`: 400 invalid input, 401 bad credentials, 403 Origin/Host, 404 missing project/route, 409 context version conflict, 500 internal failure. Unknown projects are never silently created by sends. Queued delivery acceptance does not prove model success; poll the message.

| Method and route | Input / result |
|---|---|
| GET `/health` or `/v1/health` | Version, PID, capability flags, project count |
| GET `/v1/projects` | Array of `{id,name,folder,antigravity_project_id?}` |
| POST `/v1/projects` | `{name,folder?}`; creates or reuses grouping and returns its stable ID. Folder must be absolute; an existing name cannot rebind to a different folder. Missing folder is created. |
| GET `/v1/projects/ID` | Project manifest |
| GET `/v1/providers` | Default `agents` configuration plus executable `available` flags |
| PUT `/v1/providers` | `{agents:{chatgpt:{...},gemini:{...},claude:{...}}}`; defaults for future projects |
| GET/PUT `/v1/projects/ID/providers` | Same schema; changes future turns in this project. An active subprocess retains its current argv. |
| POST `/v1/projects/ID/messages` | `{to,body,sender?,conversation?,context_files?,model?,effort?}`; returns `{id,conversation,state,context_files,model,effort}` |
| GET `/v1/projects/ID/messages?conversation=C&after=SEQ` | Up to 200 messages in sequence order, including body, state, result, error, selection, versions, proposals |
| POST `/v1/projects/ID/stop` | `{conversation}`; cancels queued work and future routes; active turn may finish |
| GET `/v1/projects/ID/context` | Context document metadata |
| GET `/v1/projects/ID/context/file?path=PATH&version=N` | Document; omit version for latest. Missing current file has `exists:false,version:0`. URL-encode query values. |
| POST `/v1/projects/ID/context/scaffold` | `{task_id?,writer?}`; creates standard documents without overwriting |
| POST `/v1/projects/ID/context/file` | `{path,content,expected_version?,writer?}`; full replacement, version check recommended |
| GET `/v1/logs?limit=100` | Last 1–1000 structured metadata events from the current rotating log |
| GET `/v1/events?after=N` | SSE metadata stream, heartbeat every 2s; replay last 1000 in-memory events, cursor via `after` or Last-Event-ID |
| POST `/v1/shutdown` | `{}`; clean exit, kills active subprocess groups; launchd does not restart successful shutdown |
| POST `/v1/projects/ID/mcp?identity=chatgpt` | Stateless Streamable HTTP MCP JSON-RPC; details below |

Provider configuration must include all three agents with a nonempty `command` argv array; preserve other fields from GET when doing PUT. Supported formats: `codex`, `antigravity`, `claude`, `gemini`, `text`. Gemini's default adapter is `antigravity` with `input_format:antigravity`. Set executable paths to absolute paths for login startup. Optional per-agent `model` and `effort` set defaults. Existing projects retain their configurations when global defaults change. Unknown native model IDs or unsupported effort/model combinations fail through the provider; they are never substituted. Timeout, history and turn budget currently remain file configuration, loaded on restart; use defaults unless deliberately administering the service.

Executable availability is not an authentication check. The packaged entry point removes OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, GOOGLE_API_KEY, and ANTHROPIC_AUTH_TOKEN from its process environment before spawning providers. Sign into each CLI using the intended subscription account first. Native CLI configuration can still select other authentication or external credentials; verify it separately if subscription-only usage is required. The bridge never asks for or stores provider API keys.

## Complete orchestrator workflow

1. Check health, list projects, then create/reuse the intended project. Keep its returned ID throughout the task. Native Antigravity grouping is provisioned during creation; Codex and Claude group by workspace and create sessions on first invocation. A provisioning failure leaves a saved manifest for a later retry. If only Claude is installed, configure the unused Gemini adapter as `text` to skip native provisioning; sending to an unavailable target still fails.
2. Scaffold task context, write goals and requirements using current versions, then choose exactly which files the recipient should see and may update.
3. Send once, save message and conversation IDs, poll every few seconds for `done`, `failed`, or `cancelled`. A separate request can reuse the conversation but must select context again.
4. Read final reply and persisted findings/version/writer. On a failed context write, inspect `context_proposals`, read current versions, reconcile rather than blindly overwriting. Do not automatically resend after timeout/crash: provider usage may already have occurred.

```bash
curl --noproxy '*' --fail-with-body -sS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"my-project","folder":"/absolute/workspace"}' "$BASE/v1/projects"
# Set PROJECT_ID to the returned id.
P="$BASE/v1/projects/$PROJECT_ID"
curl --noproxy '*' --fail-with-body -sS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"task_id":"123","writer":"chatgpt"}' "$P/context/scaffold"
curl --noproxy '*' --fail-with-body -sS -H "Authorization: Bearer $TOKEN" "$P/context/file?path=project/brief.md"
# Newly scaffolded documents have version 1; use the actual current version for existing documents.
curl --noproxy '*' --fail-with-body -sS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"path":"project/brief.md","content":"Goal: review our design.","expected_version":1,"writer":"chatgpt"}' "$P/context/file"
curl --noproxy '*' --fail-with-body -sS -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"sender":"chatgpt","to":"gemini","body":"Review the brief. Write evidence and open questions to the selected findings document. Finish without routing further messages.","context_files":["project/brief.md","tasks/123/findings.md"],"effort":"high"}' "$P/messages"
# Save returned conversation/id. Poll, retaining the result JSON alongside your work when useful:
curl --noproxy '*' --fail-with-body -sS -H "Authorization: Bearer $TOKEN" "$P/messages?conversation=$CONVERSATION_ID"
curl --noproxy '*' --fail-with-body -sS -H "Authorization: Bearer $TOKEN" "$P/context/file?path=tasks/123/findings.md"
```

Context layout: `project/brief.md` (scope/goals/constraints), `project/decisions.md` (accepted decisions/rationale), `tasks/123/requirements.md` (acceptance criteria), `tasks/123/findings.md` (evidence/agent attribution/open questions). Replace 123 with a real task ID. Names are logical store-relative paths, not workspace attachments. SQLite is authoritative; mirrors under `HOME/projects/ID/context/` are readable projections, never edit them directly. Up to 10 files are attached; missing selected files have version 0 and can be output destinations. Selected snapshots are delivered at claim time. Full document replacements use compare-and-swap versions and immutable revision history.

A downstream managed turn returns:

```json
{"reply":"Review complete.","context_updates":[{"path":"tasks/123/findings.md","expected_version":1,"content":"Full findings document"}],"messages":[]}
```

Use the delivered version. Updates can target only selected files. Updates, reply, and routes commit atomically. Routes are optional, at most three per turn, with `to`, `body`, optional `context_files` subset, optional `model`/`effort`. Routes inherit context selection but not model/effort. `reply` is recorded for the orchestrator to retrieve, not automatically routed. Plain text yields a recorded reply with no routes. Default conversation budget: 12 total messages including failed requests; default turn timeout: 180s. One worker per project serializes turns; different projects can run concurrently. Do not call bridge tools recursively from a managed turn: return the routing envelope instead.

Optional native overrides: Codex maps `model` to `--model`, `effort` to `-c model_reasoning_effort="LEVEL"`; agy and Claude use `--model`/`--effort`. Supported effort spellings vary by provider; see README's provider-options section. Leave absent options unset to use configured/native defaults. Log `configured_model`/`configured_effort` describe requested/native flag settings; `reported_model` is populated only when the provider supplies it. Do not claim the exact model based solely on a default alias.

## HTTP MCP, no script launcher needed

Configure a client supporting Streamable HTTP with URL `http://127.0.0.1:8765/v1/projects/ID/mcp?identity=AGENT` and Authorization bearer header. Use the returned project ID and actual sender (`chatgpt`, `claude`, `gemini`, `user`). Clients differ in header syntax; inject credentials from their local secret/environment facilities. Never distribute a config containing the live token. A generic connection template is in `mcp-configs/http.json`.

Supported protocol versions: 2025-03-26 and 2025-06-18. POST Accept must list both `application/json, text/event-stream`; subsequent requests carry MCP-Protocol-Version. Initialize returns tools capability. This is stateless JSON-response mode, no session ID; notifications get 202 with no body. GET and DELETE on the MCP endpoint return 405, as no server-initiated MCP SSE/session deletion is offered. `/v1/events` is a separate operational log stream, not an MCP transport.

Eight tools: `bridge_send`, `bridge_messages`, `bridge_status`, `bridge_stop`, `bridge_context_scaffold`, `bridge_context_list`, `bridge_context_read`, `bridge_context_write`. Same arguments as the REST operations; no sender/writer arguments because URL identity supplies attribution. Tool errors return `isError:true`; always inspect it. The project and identity stay fixed by URL. Create/list projects over REST before connecting.

```bash
curl --noproxy '*' --fail-with-body -sS -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-06-18' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"my-agent","version":"1"}}}' \
  "$P/mcp?identity=chatgpt"
```

## Logs and recovery

Foreground JSONL events appear on stderr and in `HOME/operations.jsonl` (5 MB, three backups). `--log-file-only` suppresses stderr events. Metadata includes project, sender/recipient, IDs, native command outcome/status, duration, request/response lengths, context versions and model/effort. Prompts/replies/secrets/raw stderr are excluded; transcripts and failure details remain in each private SQLite database and authenticated messages API. HTTP status 200 only means the API operation succeeded: inspect delivery state and provider exit/status separately.

`GET /v1/logs` reads the current log, not rotated backups. SSE is best-effort observation, not a durable queue: cursors reset at process restart, old entries outside the 1000-event buffer are unavailable, and a slow consumer disconnects. After reconnect/restart, fetch message state and logs; never infer completion from a stream alone.

Persisted project workers resume queues at startup. Previously running deliveries are marked failed with unknown outcome and never automatically retried. Projects whose CLI provisioning fails emit `project_start_failed`; other projects remain available, and a later project request retries startup. A project already held by a legacy daemon cannot be loaded concurrently. API shutdown returns success before exit; active turns may have unknown external effects. launchd KeepAlive restarts abnormal exits, but not clean API shutdown; use `launchctl kickstart gui/$(id -u)/local.ai-bridge.api` or next login to restart a cleanly stopped job.
