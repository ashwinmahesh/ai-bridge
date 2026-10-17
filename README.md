# ai-bridge

Local message bridge between **ChatGPT through Codex CLI**, **Gemini through Antigravity `agy`**, and **Claude Code**. This README is an operating guide for both humans and AI agents. It is self-contained; no prior chat context is required.

The implementation uses Python 3.11+ and its standard-library SQLite driver. The durable message queue and transcript live in a local SQLite file; no database server or extra Python packages are required. Supported systems: macOS and Linux.

## Agent quickstart

Use this procedure when an AI agent is asked to use the bridge:

1. Work from `~/Documents/ai-bridge`. No build or package installation is required. Verify Python with `python3 --version`.
2. Select one project name. Use exactly the same project name for startup, sends, reads, and cancellation. The default is `ai-bridge`.
3. Check `python3 manage.py --project NAME status`. If it reports `Not running`, start with `python3 bridge.py --background --project NAME --folder /absolute/workspace`. Background startup never asks questions.
4. Send a message using the CLI or MCP tools below. Save the returned message ID and conversation ID.
5. Read messages for that conversation. A queued response is not a completed response. Poll at reasonable intervals, such as 2 seconds, until the target message has state `done`, `failed`, or `cancelled`.
6. Read the `result` of a completed message. If the response routed more messages, inspect those messages too. A conversation is idle when it has no `queued` or `running` messages. Use the `seq` cursor if more than 200 messages are present.
7. Inspect `error` for failed deliveries or blocked routing. Do not automatically resend failed messages: a provider call may already have consumed usage.

Example:

```sh
cd ~/Documents/ai-bridge
python3 bridge.py --background --project my-app --folder /absolute/path/to/my-app
python3 bridge.py --project my-app send --sender chatgpt gemini 'Review this architecture and send your findings to chatgpt. Architecture: ...'
```

A successful `send` prints JSON:

```json
{"id":"MESSAGE_ID","conversation":"CONVERSATION_ID","state":"queued"}
```

Then read the conversation:

```sh
python3 bridge.py --project my-app messages --conversation CONVERSATION_ID
```

All global flags go **before the command**. Command flags go **before positional arguments**:

```sh
python3 bridge.py --project my-app send --conversation CONVERSATION_ID --sender chatgpt claude 'Review the recommendation in this conversation.'
```

Do not place `--project` after `send`, or `--conversation` after the message body. Quote the message as a single argument. Prompts are passed to the provider through stdin without shell interpolation.

## Startup modes

Interactive setup always runs in the foreground:

```sh
python3 bridge.py
python3 bridge.py --interactive
make interactive
```

The prompts ask for project name or path, workspace folder, and port. Blank answers reuse saved/default settings. Ctrl-C stops the daemon. Interactive setup needs a terminal.

For unattended operation, use flags only:

```sh
python3 bridge.py --foreground --project my-app --folder /path/to/my-app
python3 bridge.py --background --project my-app --folder /path/to/my-app
```

`--foreground` and `--background` bypass prompts. `--background` detaches from the terminal. The mode flags are mutually exclusive. If `--project` is omitted with an explicit mode flag, it defaults to `ai-bridge`.

The Makefile provides the same controls:

```sh
make run PROJECT=my-app FOLDER=/path/to/my-app      # foreground
make start PROJECT=my-app FOLDER=/path/to/my-app    # background
make status PROJECT=my-app
make logs PROJECT=my-app
make stop PROJECT=my-app
make restart PROJECT=my-app                       # background
```

`make foreground` aliases `make run`; `make background` aliases `make start`. All default to `PROJECT=ai-bridge`. `make start` is idempotent. Background mode survives terminal closure but does not install a login service.

## Projects and conversation grouping

`--project NAME` chooses a named bridge profile. `--folder PATH` selects the workspace. Missing folders are created automatically. If the folder flag is omitted, the saved binding is reused, or a private workspace is created for a new project.

You can pass a folder path directly:

```sh
python3 bridge.py --background --project /path/to/my-app
```

Its basename becomes the profile name. Names are unique within the base state directory; two folders with the same basename should use distinct explicit names and `--folder`.

Every provider runs in the selected workspace:

| Participant | Executable | Native grouping |
| --- | --- | --- |
| `chatgpt` | `codex exec` | Workspace folder; sessions appear when invoked |
| `gemini` | `agy` | Explicit Antigravity project ID and workspace folder |
| `claude` | `claude -p` | Workspace folder; sessions appear when invoked |

At startup the bridge reuses a matching Antigravity native project by ID/name. If it does not exist, the bridge creates one with `agy --new-project` and an empty input stream, identifies the new project by its workspace folder, records its ID, and gives the newly created project your chosen name. This setup sends no model prompt. Existing native settings are preserved. Ambiguous names or provisioning failures produce a startup error rather than silently using the default project.

A saved folder binding cannot be changed silently. Choose a new project name to use another folder. A native project deleted outside the bridge is recreated on the next startup.

These are local CLI groupings; ChatGPT web Projects and Claude web Projects are separate product features. The bridge does not read, modify, or attach to existing browser chats.

Each profile has an isolated token, state file, log, configuration, and native project binding in `.bridge/projects/NAME-HASH/`. `python3 bridge.py --project NAME init` prints the exact state directory as JSON without starting a daemon or creating a native project.

One daemon serves one project. To run several simultaneously, give them distinct ports:

```sh
make start PROJECT=frontend FOLDER=/path/to/frontend PORT=8765
make start PROJECT=backend FOLDER=/path/to/backend PORT=8766
```

The port is saved in that profile. Stop a running profile before changing its port. A port conflict cannot stop or impersonate another profile's daemon.

## Persistent project context

Each project has a versioned context store shared by the orchestrator and downstream agents. Suggested logical structure:

```text
context/
  project/brief.md          # stable goals and constraints
  project/decisions.md      # accepted decisions
  tasks/TASK/requirements.md
  tasks/TASK/findings.md    # downstream findings / handoff
```

SQLite holds the authoritative content and immutable revisions. Readable mirrors live under the selected profile's `context/` directory. The orchestrator chooses documents **per message**; documents are not attached automatically just because they belong to the same project.

Create/read context through the CLI while the daemon is running:

```sh
python3 bridge.py --project my-app context write project/brief.md --text 'Goals and constraints' --expected-version 0 --writer chatgpt
python3 bridge.py --project my-app context write tasks/123/requirements.md --file /path/to/requirements.md --expected-version 0
python3 bridge.py --project my-app context list
python3 bridge.py --project my-app context read project/brief.md
python3 bridge.py --project my-app context read project/brief.md --version 1
```

`write` creates or replaces the complete document. `--expected-version 0` creates only if absent. For an existing file, read its version and supply that version when writing. Omitting the option makes an unconditional orchestrator write. Stale writes are rejected with HTTP 409. A missing document reads as `exists: false`, version 0, and empty content, so an orchestrator can reserve an output name for a downstream agent to create.

Select documents for a downstream message by repeating `--context`:

```sh
python3 bridge.py --project my-app send --sender chatgpt --context project/brief.md --context tasks/123/findings.md gemini 'Review the brief and record your findings in the selected findings file.'
```

Each turn receives a snapshot of **only its selected documents**, with content and version numbers. Snapshots are taken when delivery begins; the transcript records the selected paths and delivered versions in `context_files` and `context_versions`. If another writer changes a document during the turn, the delivered snapshot stays stable and a stale update will fail.

Downstream agents write back through the response envelope:

```json
{
  "reply": "I recorded my findings.",
  "messages": [{"to": "claude", "body": "Review the findings."}],
  "context_updates": [
    {"path": "tasks/123/findings.md", "expected_version": 0, "content": "Full findings text"}
  ]
}
```

Updates must target a selected document and cite its delivered version. By default, routed messages inherit the selected file list and see committed updates. A routed message can include `"context_files": ["tasks/123/findings.md"]` to pass a smaller subset, or `[]` to pass no documents; it cannot add files that were not selected for the current turn. The orchestrator can select a different set in any later `send`.

Context updates, the reply, and outgoing messages commit in one SQLite transaction. Conflicting or invalid updates apply none of the changes and route no children. The message becomes `failed`; its reply and `context_proposals` remain available for the orchestrator to reconcile. No model call is automatically retried. A stopped conversation cannot publish new context updates.

Mirrors are for direct reading. To edit an existing mirror, import the changed text with `context write --file ... --expected-version N`; direct file edits do not update SQLite and are overwritten on mirror repair. Mirrors are regenerated from SQLite after restart. Safe relative names are required; traversal, absolute paths, hidden names, and symlink writes are rejected. Limits: 10 selected files/updates per turn, 32000 characters / 64000 UTF-8 bytes per document, and 128000 bytes per delivered snapshot.

Selection controls what the bridge attaches to a turn, not OS-level access by an adversarial CLI. Previously exposed content may also remain in that conversation's transcript. Reuse project context across conversations, and start a fresh conversation when you want a fresh transcript.

## AI-to-AI routing contract

Interactive CLI sessions can use MCP tools to send messages. Managed daemon turns use this exact JSON response contract:

```json
{
  "reply": "My answer for the transcript.",
  "messages": [
    {"to": "claude", "body": "Please review this tradeoff and send your answer to chatgpt."}
  ]
}
```

- `reply` is text recorded for the user.
- `messages` contains zero to three explicitly addressed outgoing messages.
- Valid recipients are exactly `chatgpt`, `gemini`, and `claude`.
- Each outgoing body contains 1–16000 Unicode characters.
- Return `"messages": []` to finish.
- Replies are **not automatically sent back to the previous sender**. Address the intended recipient explicitly.
- Each recipient sees recent shared conversation history with sender attribution. Older context is truncated to the configured budget. The current message is always included.
- Do not call the bridge MCP tools from inside a daemon-managed turn; use the JSON envelope there. This keeps routing and conversation budgets consistent.
- Plain text responses are accepted, recorded, and do not route anything. Malformed explicit envelopes fail delivery.

The daemon processes one message at a time and returns immediately from `send`. Asking another agent a question must not block waiting for that agent inside the same managed turn; return the outgoing message instead.

The default limit is **12 total messages per conversation**, including initial and failed messages. If a turn's outgoing messages would exceed the budget, all of that turn's outgoing messages are blocked and the transcript explains why. Start a new conversation to obtain a new budget. Avoid acknowledgment loops.

## Message states and recovery

| State | Meaning | Agent action |
| --- | --- | --- |
| `queued` | Awaiting a provider turn | Read again later |
| `running` | CLI invocation in progress | Read again later |
| `done` | Reply persisted in `result` | Read reply; inspect any routed children |
| `failed` | Provider, parse, timeout, or restart failure | Read `error`; decide whether to resend |
| `cancelled` | Queued message cancelled | Do not expect a reply |

To prevent further routing in a conversation:

```sh
python3 bridge.py --project my-app stop CONVERSATION_ID
```

This cancels queued messages and blocks further routing. An already running turn may finish. To interrupt the running provider process too:

```sh
python3 manage.py --project my-app stop
```

Shutdown kills the active provider process group and waits for the worker. On an unexpected restart, previously running messages become `failed` with an unknown-outcome error; they are not automatically retried. Other queued messages continue. Results and outgoing messages are persisted together. A file lock prevents two daemons from delivering the same profile's queue.

## Codex skill

A reusable `ai-bridge` skill is included in `skills/ai-bridge/` and installed locally at `~/.codex/skills/ai-bridge/`. It teaches Codex/ChatGPT through the local Codex environment how to select a project, start the daemon, send to Gemini/Claude, poll for replies, and handle errors.

Example invocation in a skill-enabled Codex chat:

```text
$ai-bridge Ask Gemini and Claude to review this architecture, and bring back their replies.
```

The skill adds operating instructions; the daemon and CLI/MCP interface perform the actual work. Start a fresh chat or reload skills if the newly installed skill is not visible. Local Codex skills are separate from plugins/skills in a regular ChatGPT browser chat. The skill's installed project path must be updated if you move the project.

## MCP tools for interactive agents

Start the daemon first. The stdio MCP process is a client of that daemon and does not start another daemon.

| Tool | Arguments | Result |
| --- | --- | --- |
| `bridge_send` | `to`, `body`, optional `conversation`, `context_files` | Queued message ID and conversation ID |
| `bridge_messages` | optional `conversation`, `after` | Up to 200 transcript rows and states |
| `bridge_status` | none | Daemon health, project, and executable availability |
| `bridge_stop` | `conversation` | Cancel queued work and further routing |
| `bridge_context_list` | none | List project context paths and versions |
| `bridge_context_read` | `path`, optional `version` | Read current or historical context |
| `bridge_context_write` | `path`, `content`, optional `expected_version` | Create/update persistent project context |

The MCP client's `--identity` fixes its sender attribution. This identity is not inferred from text. For example, Codex's MCP configuration uses `chatgpt`.

Ready-to-merge snippets in `mcp-configs/` target the installed path and default `ai-bridge` profile. Change their `--project` argument to select another project. Merge the server entry into existing settings rather than overwriting unrelated servers. Restart/reload the client if required.

Generic Codex configuration:

```toml
[mcp_servers.ai_bridge]
command = "/absolute/path/to/python3"
args = ["/Users/YOU/Documents/ai-bridge/bridge.py", "--project", "ai-bridge", "mcp", "--identity", "chatgpt"]
```

Antigravity (`~/.gemini/config/mcp_config.json` or workspace `.agents/mcp_config.json`):

```json
{
  "mcpServers": {
    "ai_bridge": {
      "command": "/absolute/path/to/python3",
      "args": ["/Users/YOU/Documents/ai-bridge/bridge.py", "--project", "ai-bridge", "mcp", "--identity", "gemini"]
    }
  }
}
```

Claude Code uses the same JSON shape in its MCP config (for example a project `.mcp.json`), with identity `claude`.

The MCP transport is newline-delimited JSON-RPC over stdin/stdout. Diagnostic output stays off stdout. The daemon requires a token, read automatically by the MCP process from the selected profile. No token should be embedded in the MCP snippets.

## Operation logs

Follow logs with `make logs PROJECT=NAME`. Every line of `operations.jsonl` is structured JSON. It rotates at 5 MB, retaining three backup files, approximately 20 MB total. The profile directory is mode 0700; log/state/token files are mode 0600.

| Field | Meaning |
| --- | --- |
| `timestamp`, `event`, `pid`, `project` | UTC time, operation, daemon process, selected project |
| `sender`, `recipient`, `agent` | Communication direction and provider executing the turn |
| `message_id`, `conversation_id`, `parent_id` | Trace a route across the three agents |
| `state`, `provider_status`, `error_type` | Delivery state, provider completion status, failure category |
| `status_code` | HTTP response code of the local bridge |
| `exit_code` | Provider CLI process exit code |
| `request_chars`, `request_bytes` | Message length in Unicode characters / UTF-8 bytes |
| `response_chars`, `response_bytes` | Provider answer, delivery reply, or HTTP payload length, identified by event |
| `stdout_bytes`, `stderr_bytes` | Captured provider output lengths |
| `duration_ms`, `timeout_seconds` | Elapsed time and timeout ceiling |
| `configured_model`, `reported_model` | Explicit command model or provider-reported model; empty when unknown |
| `routed_messages`, `routing_blocked` | Number of outgoing messages and whether routing was blocked |

Model versions are never guessed. The participant name `gemini` does not imply a fixed Gemini version. Provider network HTTP codes are not consistently exposed by CLIs, so the bridge records local HTTP codes, CLI exit codes, and available provider completion statuses separately.

Events also include `context_attached` (paths, versions, byte counts) and `context_updated` (path, version, writer, lengths), with no context content in the log. Events cover project creation/reuse, daemon startup/shutdown, HTTP responses, message queueing, delivery, provider process exit, timeout, completion, cancellation, and crash recovery.

Logs omit message bodies, prompts, replies, raw stderr, authorization headers, and token values. Full transcript content and failure details remain in `messages.sqlite3`, accessible through `messages`. Background startup/crash diagnostics are in `console.log`; foreground diagnostics appear in the terminal. Operation log rotation does not truncate history.

## Configuration and persistence

The first run creates `.bridge/config.json`. New named profiles inherit those settings once, then keep independent configuration in their own directory. Restart a daemon to load edits. `--home PATH` or `AI_BRIDGE_HOME` selects a different base state directory. Use the same base and project for every client.

| Setting | Default | Purpose |
| --- | --- | --- |
| `port` | 8765 | Loopback HTTP port |
| `timeout` | 180 | Maximum seconds per provider invocation |
| `max_turns` | 12 | Total message budget per conversation |
| `history_chars` | 24000 | Approximate recent history budget, counted as serialized bytes |
| `agents.NAME.command` | CLI argv array | Executable and arguments; no shell expansion |
| `agents.NAME.format` | Provider-specific | `codex`, `antigravity`, `claude`, `gemini` (legacy), or `text` |
| `agents.NAME.input_format` | Provider-specific | `antigravity` sends an NDJSON user event; otherwise plain stdin |

Executable paths are resolved through PATH and `~/.local/bin` when configuration is created. The `gemini` adapter defaults to `agy`. Each CLI uses its existing local login and account allowance. Authenticate interactively once if necessary. Executable availability in health checks does not prove authentication or model access.

Codex defaults to a read-only sandbox, Antigravity to plan mode, and Claude to built-in tools disabled. No permission-bypass flags are enabled. CLIs can load their own settings, hooks, plugins, and MCP servers; the bridge is not an adversarial-agent sandbox.

State is stored in `messages.sqlite3` with SQLite WAL journaling. Queue claims and conversation budgets use immediate transactions. Replies and routed child messages commit atomically. A daemon file lock prevents competing processes from delivering the same queue. Each operation opens a short-lived connection, while the SQLite transaction handles concurrency between the HTTP handlers and worker. No ORM or third-party database driver is used.

Back up a stopped profile directory to preserve its history, configuration, token, and native project binding. If backing up a running profile, use SQLite's backup API rather than copying only the database file while ignoring its WAL. Do not share or commit the profile directory.

## Verify

Requires Python 3.11+ with its standard-library `sqlite3` module (available in the installed Python on this machine). No `pip install` step is needed.

```sh
make test
python3 -m py_compile bridge.py manage.py context_store.py
```

Run an explicit live check only when you want to make real provider requests:

```sh
python3 tests/live_e2e.py --project ai-bridge
```

This sends one short request each to Antigravity `agy` and Claude through the running daemon, retrieves both replies, and checks unique run-specific markers. It prints queue IDs, delivery states, replies, and a final JSON `ok` value. The command exits nonzero if either test fails or the overall timeout expires. It uses the logged-in CLI accounts and can consume their usage allowance.

For an explicit shared-context test:

```sh
python3 tests/live_context_e2e.py --project ai-bridge
```

The orchestrator creates a unique brief, Gemini reads its token and writes a selected findings file, Claude reads Gemini's persisted file and updates it, and the orchestrator verifies both revisions and replies.

Tests use deterministic fake providers and real localhost daemons. They cover routing, SQLite persistence, crash recovery, cancellation, budgets, concurrent claims, parser failures, native project provisioning/reuse, workspace bindings, operation log metadata/content exclusion, context snapshots, version conflicts, atomic context updates, routing inheritance, path isolation, MCP calls, authentication, subprocess timeout, background lifecycle, and interactive/flag-only startup.

The daemon listens only on `127.0.0.1`. Every HTTP request requires a bearer token; clients disable inherited HTTP proxies for localhost requests. Any local process that can read a profile token can act as any sender. Sender identity is attribution, not a separate security boundary. Sending content to another agent shares it with that provider.

## HTTP API

All endpoints require `Authorization: Bearer TOKEN`, read from the selected profile's `token` file. Avoid printing tokens in logs or prompts.

| Method | Path | Body / response |
| --- | --- | --- |
| GET | `/context` | List context paths and versions |
| GET | `/context/file?path=NAME&version=N` | Read a context document (version optional) |
| POST | `/context/file` | `{ "path":"notes.md", "content":"...", "expected_version":1, "writer":"chatgpt" }` |
| GET | `/health` | Daemon PID, selected project, executable availability |
| POST | `/messages` | `{ "sender":"user", "to":"claude", "body":"...", "conversation":"optional" }` |
| GET | `/messages?conversation=ID&after=SEQ` | Up to 200 message records |
| POST | `/stop` | `{ "conversation":"ID" }` |

Stale context updates return 409. Invalid requests return 400, unauthorized requests 401, and unknown routes 404. CLI errors go to stderr with a nonzero exit code. `python3 bridge.py --project NAME status` prints machine-readable health JSON; `python3 manage.py --project NAME status` prints a human-readable running/stopped status. Conversation messages returned by the CLI are JSON, suitable for agents to parse.

Storage reference: [Python sqlite3 documentation](https://docs.python.org/3/library/sqlite3.html).

CLI references: [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive), [Antigravity headless mode](https://antigravity.google/docs/cli/headless/), [Antigravity projects](https://antigravity.google/docs/projects?tab=cli), [Claude CLI reference](https://code.claude.com/docs/en/cli-reference).


## Optional macOS login service

Background mode does not install anything at login. To review an optional launchd definition, run:

```sh
python3 bridge.py --project ai-bridge launchd > local.ai-bridge.plist
```

After stopping the foreground/background daemon, install the reviewed plist in `~/Library/LaunchAgents/` and load it with `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.ai-bridge.plist`. To stop that service, use `launchctl bootout gui/$(id -u)/local.ai-bridge`. The plist generator only prints a definition; the bridge does not install it automatically.
