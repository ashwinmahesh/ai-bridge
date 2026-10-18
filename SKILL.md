---
name: ai-bridge
description: Talk to other AI models through a local ai-bridge daemon using Codex, Antigravity agy, or Claude Code. Use when the user asks another model to draft, review, answer, or collaborate, or asks to compare their responses.
---

# ai-bridge: ask another AI model

Use the local bridge's HTTP API or configured MCP tools. The packaged service bundles Python and SQLite; agents need no checkout or Python command. Read the repository's `API.md` for configuration, exact endpoint schemas, context limits, or HTTP MCP setup. Its `README.md` describes installation and legacy compatibility.

## 1. Discover the service; don't start a second one

Read `~/Library/Application Support/AI Bridge/service.json` on macOS, or `${XDG_DATA_HOME:-~/.local/share}/ai-bridge/service.json` on Linux. A user-specified service home takes precedence. It contains `url` and `token_file`. Read that local token file and send `Authorization: Bearer TOKEN`; never log or print its contents. Use an HTTP client with localhost proxying disabled. Check `GET /health` for `ok`, version 2, and `capabilities.multi_project`.

If discovery is absent or health fails, report it. Respect user-managed startup; do not restart their daemon, activate a login service, or create a second process merely to complete a send. Executable detection proves availability, not authentication.

For an explicitly selected legacy daemon, use its existing CLI/MCP configuration and README's legacy section instead. Legacy routes are unversioned and each process serves one project. Never silently mix v1 and v2 endpoints or state directories.

## 2. Select the project and target

`GET /v1/projects` lists `{id,name,folder,...}`. Use the user's requested project; reuse it by name and keep its returned ID for every operation. If no project was specified, use the existing `ai-bridge` project. Create a missing intended project with `POST /v1/projects {"name":"ai-bridge"}`; include an absolute `folder` only when the user supplied a workspace. Antigravity grouping and missing directories are provisioned automatically. Codex/Claude group by working directory, not browser-chat Projects.

| Target | CLI | Intended authentication |
|---|---|---|
| `gemini` | Antigravity `agy`, plan mode | User's Google account/subscription |
| `chatgpt` | Codex, read-only sandbox | User's ChatGPT account/subscription |
| `claude` | Claude Code, tools off | User's Claude account/subscription |

Do not switch to the separate `gemini` CLI or an API-key backend. Never request/store provider API keys. The packaged launcher strips common API-key environment overrides, but native CLI settings may still select other authentication. For subscription-only use, verify the local CLI account mode (`codex login status`, `claude auth status`, or agy's sign-in state) without printing secrets; if it indicates API-key billing, stop and report it. Do not claim a subscription or exact selected model based only on finding an executable.

## 3. Send once, then read until done

Let `P` be `/v1/projects/RETURNED_ID`. Send authorized content:

```http
POST P/messages
Content-Type: application/json
Authorization: Bearer <local token>

{"sender":"chatgpt","to":"gemini","body":"Review this design. Finish without sending messages to anyone else."}
```

Use your actual identity for `sender`: `chatgpt`, `claude`, `gemini`, or `user`. This is attribution only. HTTP success and `state:queued` mean accepted, not answered. Save `id` and `conversation`; poll `GET P/messages?conversation=CONVERSATION_ID` every few seconds within a bounded wait. Find that message ID and wait for `done`, `failed`, or `cancelled`. Read `result` for the reply and `error` for failure/routing issues. Use `after=SEQ` to paginate beyond 200 rows.

Follow-ups specify the same `conversation` and intended attachments again. To prevent further work, `POST P/stop {"conversation":"..."}` cancels queued turns and future routing; an active turn may finish. If waiting expires, report the last state without duplicating the request.

For a simple ask, request a reply without further routes. Plain text is supported and routes nothing; the native managed-turn envelope is `{"reply":"...","messages":[]}`. The orchestrator retrieves replies; they are not automatically sent back to the sender. During a daemon-managed turn, return the envelope rather than recursively calling the bridge tools.

## 4. Model and effort

Optional `model` and `effort` in `POST P/messages` or MCP `bridge_send` apply to that recipient turn. Omit unspecified values to use project/provider defaults. Example:

```json
{"sender":"claude","to":"chatgpt","body":"Review the approach.","model":"USER_REQUESTED_MODEL_ID","effort":"high"}
```

Use only a requested or verified model ID; that example is a placeholder. Codex uses `--model` and `model_reasoning_effort`; agy/Claude use `--model` and `--effort`. Supported levels depend on native CLI/model/version. Do not silently replace an unsupported selection. Overrides do not inherit into follow-ups or routed messages. Native defaults can vary; log `configured_model` records explicit selections and `reported_model` only when returned by the provider. Never report an unverified default as the actual model used.

## 5. Share persistent context deliberately

`POST P/context/scaffold {"task_id":"123","writer":"chatgpt"}` creates missing conventional documents without overwriting. Substitute the real task ID; omit it for project documents only.

- `project/brief.md`: goals, scope, constraints.
- `project/decisions.md`: accepted decisions and rationale.
- `tasks/123/requirements.md`: requirements and acceptance criteria.
- `tasks/123/findings.md`: evidence, agent attribution, unresolved questions.

Read with `GET P/context/file?path=URL_ENCODED_PATH`; write with `POST P/context/file {"path":"project/brief.md","content":"Full document text","expected_version":CURRENT_VERSION,"writer":"chatgpt"}`. Read the version before writing. Missing files have version 0. Paths are logical document names inside the project's SQLite context store, not arbitrary workspace paths. Never edit context mirrors directly.

Attach only the needed files in the outgoing message, including output destinations:

```json
{"sender":"chatgpt","to":"gemini","body":"Review the selected brief and write evidence into the findings document. Finish without routing.","context_files":["project/brief.md","tasks/123/findings.md"]}
```

The recipient sees selected snapshots and versions and can return:

```json
{"reply":"Review complete.","context_updates":[{"path":"tasks/123/findings.md","expected_version":1,"content":"Full findings text"}],"messages":[]}
```

Use the delivered version, not a guessed 1. Only selected files may be updated. Routes inherit the selection or a subset. Updates, reply, and routes commit atomically. If a version conflict fails the turn, read its `context_proposals` and the current documents, reconcile, then write with current versions. Do not blindly overwrite. Read the resulting document and version/writer after completion instead of relying only on the reply. Separate sends must select attachments again.

## 6. Timing, logs, and failures

Default timeout is 180 seconds per turn; one turn per project runs at a time, with different projects concurrent. Conversations allow 12 total messages, including failed requests, and at most 3 outgoing routes per turn. Keep requests focused and prevent acknowledgement loops.

On failure, read the recorded error and report it. Do not automatically resend: timeout/crash may already have consumed usage. A bridge restart marks interrupted deliveries failed with unknown outcome. An HTTP 200 does not mean a provider turn succeeded.

`GET /v1/logs?limit=100` returns metadata; `/v1/events` streams SSE. Foreground events also appear on stderr. Logs include project, sender/recipient, IDs, duration, response length/status, and model/effort when known. Prompts, replies, and raw error details stay in the private SQLite transcript and authenticated messages API. Event streams are best-effort and reset cursors after restart; fetch messages for authoritative state.

## 7. MCP and reporting

If configured, use the eight MCP tools: `bridge_send`, `bridge_messages`, `bridge_status`, `bridge_stop`, `bridge_context_scaffold`, `bridge_context_list`, `bridge_context_read`, `bridge_context_write`. Project and sender/writer are fixed by `/v1/projects/ID/mcp?identity=YOUR_AGENT`. Inspect `isError` in every tool result. These tools need no local script launcher when connected over HTTP.

Preserve the brief and raw reply in the transcript. When useful for further work, save the messages JSON alongside the work and edit a copy so the raw draft remains comparable. Report the target, verified model if available, reply (verbatim when requested), and delivery outcome. Do not share secrets or unrelated private information in model briefs.
