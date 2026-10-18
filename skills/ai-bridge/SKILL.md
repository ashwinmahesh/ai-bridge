---
name: ai-bridge
description: Use the local ai-bridge daemon to send a user-requested message to Gemini through Antigravity agy or Claude Code, retrieve their replies, or operate and troubleshoot the bridge. Applies to cross-AI collaboration through the installed local CLIs.
---

# AI Bridge

Use the local project at `/Users/ashwinmahesh/Documents/ai-bridge`. It is Python 3.11+ with a standard-library SQLite queue. Read its [README](/Users/ashwinmahesh/Documents/ai-bridge/README.md) for configuration, project provisioning, logging, or advanced workflows. No build or package installation is needed.

## Send and retrieve

Choose the user's project name and optional workspace folder. If neither is specified, use the existing `ai-bridge` project; do not invent another project. Reuse the same project for every command in a conversation.

From the project directory:

```sh
python3 manage.py --project PROJECT status
python3 bridge.py --background --project PROJECT
```

The second command starts the daemon if needed and is idempotent. If the user asked to leave it stopped or said they will manage startup themselves, check status but do not run the start command; report that a running daemon is required. To bind a new project to a specific workspace, add `--folder /absolute/path` at startup. Missing folders and Antigravity native projects are created automatically. Codex and Claude Code group sessions by that workspace. These are local CLI groups, not browser-chat Projects.

Send only the content and request authorized by the user:

```sh
python3 bridge.py --project PROJECT send --sender chatgpt gemini 'The request for Gemini'
python3 bridge.py --project PROJECT send --sender chatgpt claude 'The request for Claude'
```

Valid recipients: `chatgpt`, `gemini`, `claude`. The Gemini recipient uses `agy`. `chatgpt` uses the Codex CLI. Identity is sender attribution, not authentication.

`send` returns JSON with `id`, `conversation`, and initial `state`. Save both IDs, then read:

```sh
python3 bridge.py --project PROJECT messages --conversation CONVERSATION_ID
```

Use a bounded wait appropriate to the task, polling at reasonable intervals (for example 2 seconds) until the target message is `done`, `failed`, or `cancelled`. If the wait budget expires, report the last state without resending. A queued/running message is not a reply. Read `result` for a completed reply and `error` for a failure or blocked route. If more messages were routed, inspect their states too. Use `--after SEQ` to paginate beyond 200 rows. Do not automatically resend a failed/interrupted request: the provider may have consumed usage already.

For another message in the same shared context:

```sh
python3 bridge.py --project PROJECT send --conversation CONVERSATION_ID --sender chatgpt claude 'Follow-up request'
```

Global flags precede the command. Command flags can precede positional arguments as in these examples. Quote message bodies as one argument; avoid unsafe shell interpolation when constructing them dynamically.

## Persisted project context

Use named context documents for durable knowledge shared across conversations in the same project. Initialize the conventional documents for the actual task, then choose which files to attach. Run `python3 bridge.py --project PROJECT context scaffold --task TASK_ID --writer chatgpt` (or MCP `bridge_context_scaffold` with `task_id`). Omit `--task` for project documents only. Scaffolding creates missing templates and never overwrites existing content or versions. Replace TASK_ID with the real task identifier; `123` in examples is not a universal default.

Use `project/brief.md` for goals/scope/constraints, `project/decisions.md` for accepted decisions and rationale, `tasks/TASK_ID/requirements.md` for objectives/acceptance criteria, and `tasks/TASK_ID/findings.md` for evidence, agent attribution, and unresolved questions. Custom paths are supported.

The orchestrator chooses the files attached to each message:

```sh
python3 bridge.py --project PROJECT context read project/brief.md
python3 bridge.py --project PROJECT context write project/brief.md --text 'Goals and constraints' --expected-version 1 --writer chatgpt
python3 bridge.py --project PROJECT send --sender chatgpt --context project/brief.md --context tasks/TASK_ID/findings.md gemini 'Review the brief and write findings into the selected findings file.'
```

Version 1 in the example assumes a newly scaffolded file. For an existing document, read and use its current version. Repeat `--context` for each selected file. No files are attached by default. Missing files have version 0 and can be selected as output destinations. Use paths such as `project/brief.md`, `project/decisions.md`, `tasks/TASK_ID/requirements.md`, and `tasks/TASK_ID/findings.md`.

Recipients receive only the selected context snapshots, with version numbers. They can return `context_updates: [{"path":"tasks/TASK_ID/findings.md","expected_version":1,"content":"full document text"}]` in their response envelope. Updates must target selected files and cite the delivered version. Routed messages inherit the selection, or can pass a subset using their own `context_files` field. Context updates and routed messages commit atomically.

Read the persisted result with `context read PATH`; historical versions use `--version N`. The MCP equivalents are `bridge_context_list`, `bridge_context_read`, and `bridge_context_write`; `bridge_send` accepts a `context_files` array.

On a conflict, read the current version and the failed message's `context_proposals` before reconciling; do not overwrite blindly. The SQLite store is authoritative; readable file mirrors are in the profile's `context/` directory. Find the exact profile directory with `python3 bridge.py --project PROJECT init` and append `/context/` to its returned `home`. `--context` names are relative to that store, not paths in the provider workspace. Include an output document in the selection if the downstream agent should write it. After completion, read that document and check its version/writer instead of relying only on the reply. A later direct send must select attachments again; it does not inherit them automatically. Use CLI/MCP writes instead of directly editing those mirrors. Read the README's persistent-context section for limits and detailed semantics.

## Routing and MCP

If the `bridge_send`, `bridge_messages`, `bridge_status`, and `bridge_stop` MCP tools are available, use them for the same operations. Their project and sender are fixed by the configured MCP command.

The daemon prompts each recipient to return `{"reply":"...","messages":[{"to":"agent","body":"..."}]}`. `reply` is recorded in the transcript; it is not automatically sent back to the prior sender. Use `messages: []` to finish, or explicitly address another agent. The default budget is 12 total messages per conversation, with up to 3 outgoing messages per turn. Avoid acknowledgment loops.

During a daemon-managed turn, use the JSON routing envelope instead of recursively calling MCP or starting another daemon.

## Operations

- `python3 bridge.py --project PROJECT stop CONVERSATION_ID` cancels queued work and future routing for that conversation. A running turn may finish.
- `python3 manage.py --project PROJECT stop` stops the daemon and active provider process group.
- `python3 manage.py --project PROJECT logs` follows rotating JSON operation logs. Logs contain metadata; full replies and failure details are in SQLite and `messages` output.
- `python3 tests/live_e2e.py --project PROJECT` explicitly sends two short real provider requests and verifies the replies. Run only when a live connectivity check is requested.

Report the received replies and delivery outcome. If a provider fails, report its recorded error without exposing authentication secrets. Consult the README before changing login, MCP, or service configuration.
