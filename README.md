# AI Bridge: packaged multi-project service

Distribute one executable, run it at login, and control projects, messages, provider defaults, context, and logs through HTTP. The runtime needs no installed Python or source checkout. Each user still installs and signs into Codex, Antigravity `agy`, and Claude Code locally. API calls and HTTP MCP use the same persistent SQLite queue/context engine.

Read [API.md](API.md) for the complete v2 API contract and agent workflow. Read [SKILL.md](SKILL.md) for concise instructions an AI agent can use directly. The older per-project Python CLI remains supported and is documented below.

## Install a binary on macOS

Download/extract the macOS artifact matching your architecture from CI, or build it below. The local build has been tested on Apple Silicon; binaries must be built for each target platform/architecture. This release implements macOS login installation and macOS/Linux foreground service code; Windows is not supported (POSIX locking/process groups).

```bash
# All global flags go before the command. Install only, starting at next login:
./ai-bridge install-service
# Or explicitly load it immediately:
./ai-bridge install-service --start
```

The installer copies the executable to `~/Library/Application Support/AI Bridge/bin/ai-bridge`, initializes private state there, and writes `~/Library/LaunchAgents/local.ai-bridge.api.plist`. It runs as the signed-in user at login, including after reboot and login. It does not run as root before login. Authentication belongs to that user's CLI accounts. No source checkout or Python installation is needed after installation.

From a source checkout, build and install with:

```bash
make build
make install-service
```

`make install-service` installs for the next login without starting the service immediately. Its JSON output includes `"started": false` for that successful install-only result. There is no `make install` target.

To start an already-installed service now, load its login configuration:

```bash
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/local.ai-bridge.api.plist"
```

Before loading it, ensure another bridge process is not using the configured port (8765 by default). If you installed with a different port, use that port for API requests. Run bootstrap only when the job is not already loaded; it does not restart an existing job.

Default port: 8765. For a different port/home: `./ai-bridge --home /absolute/state --port 8766 install-service --start`. Installation does not silently unload or replace a running job; for upgrades, cleanly shut down via API, run `launchctl bootout gui/$(id -u)/local.ai-bridge.api`, install the replacement, then bootstrap it again. The installer preserves data, configuration, transcripts and token. `--start` on an already loaded job fails visibly; it does not restart it.

```bash
# Foreground service with flags only:
./ai-bridge --port 8766 serve
# Show discovery paths/current service record:
./ai-bridge service-info
# Disable login startup and optionally unload now; data is preserved:
./ai-bridge uninstall-service --stop
```

These CLI commands administer installation/lifecycle. All project, message, context, provider and log operations use the API; clients never execute `bridge.py`. Read `service.json` in the service home for URL/token-file discovery, then follow [API.md](API.md). `service-info` reports discovery, not a live health probe. The token file is private and must stay local.

launchd restarts crashes with a throttle; clean API shutdown remains stopped until next login or an explicit kickstart. Launch output is in `console.log`; structured operation metadata rotates in `operations.jsonl`. Foreground logs also stream to stderr. Native CLI executable paths are discovered during initial setup; update them through the provider API if login PATH differs. CLI presence does not prove sign-in. The packaged launcher removes common API-key environment overrides; verify native CLI configuration for subscription-only usage.

The build is ad-hoc signed by PyInstaller, without a Developer ID/notarization identity. For broad download distribution, the maintainer must configure Apple Developer ID signing/notarization; CI currently uploads build artifacts, not a notarized installer. Python bytecode is bundled and can be extracted; distributing without a checkout does not provide source secrecy. The one-file executable extracts its embedded runtime into a temporary directory at launch.

## Build and verify

Developer prerequisites: Python 3.11+ and network access to install build dependencies. Runtime code has no pip dependencies; PyInstaller is build-time only.

```bash
make test
make build                 # isolated .venv-build; output dist/ai-bridge
make package               # shareable binary + docs + skill ZIP
AI_BRIDGE_TEST_BINARY="$PWD/dist/ai-bridge" python3 tests/test_service.py -v
make api SERVICE_PORT=8766 # run source service, separate from existing legacy port
```

Tests use temporary homes, ephemeral ports and fake providers; they do not send paid model requests or activate a real login job. The packaged integration suite runs the executable with PATH set to a nonexistent directory, using an absolute shell fixture, to verify it does not invoke an installed Python. Build CI runs source and packaged integration tests and uploads a macOS archive. Other architectures need their own native builds and verification.

## Add the skill to your agents

The repository root `SKILL.md` is the portable skill entry point (standard uppercase filename). `skills/ai-bridge/` is the ready-to-install directory, including optional Codex UI metadata. Both contain the same instructions, adapted from the contributed agent workflow for the multi-project API. They include subscription-only guidance, project selection, polling, model/effort, context updates, error handling and reporting.

```bash
# Codex personal skill:
mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills/ai-bridge"
cp -R skills/ai-bridge/. "${CODEX_HOME:-$HOME/.codex}/skills/ai-bridge/"
# Claude Code personal skill:
mkdir -p "$HOME/.claude/skills/ai-bridge"
cp SKILL.md API.md "$HOME/.claude/skills/ai-bridge/"
# Claude Code repository-local alternative:
mkdir -p /path/to/your/project/.claude/skills/ai-bridge
cp SKILL.md API.md /path/to/your/project/.claude/skills/ai-bridge/
```

Start a fresh agent session if it does not discover the new skill. For an agent without a skill-folder mechanism (including a browser chat), provide `SKILL.md` as instructions/reference and connect an HTTP-capable local tool to the bridge API or HTTP MCP endpoint. Uploading the skill alone does not give a hosted browser model access to your computer's localhost service. Antigravity versions vary in skill discovery: add the file through that agent's supported local-instructions mechanism instead of assuming a directory convention. Use the agent's actual identity in MCP URL/sender fields; installing this skill does not authorize unsolicited messages or daemon restarts.

The archive contains the skill and API reference for users without a checkout. For a skill-only installation, ensure the agent can also consult API.md when configuring the service. Never put a live token in skill files or shared MCP templates.

## Existing installations and migration

Existing `make run/start` targets, `bridge.py`, `manage.py`, `.bridge` profiles and stdio MCP continue to work unchanged. They serve the legacy one-project API. The new `make api`/binary serves v2 routes and defaults to a separate Application Support home. They cannot bind the same port simultaneously: choose another port during evaluation. Do not stop someone else's running daemon to test or deploy this change.

To reuse existing profiles, cleanly stop the legacy process yourself, back up the complete `.bridge` home (including SQLite WAL files only after shutdown), then pass that home using `ai-bridge --home /absolute/path/to/.bridge serve`. Named profile directories under `projects/` are discovered automatically, and each receives an exclusive delivery lock. The legacy unnamed root profile is not automatically imported; create a named project for new work or continue using the old interface for its history. Provider settings and shared context of named profiles are retained. The central service uses the base home's bearer token, not individual profile tokens. Update clients to v2 paths and HTTP MCP URLs. No live service is migrated automatically.

---

# Legacy per-project CLI reference

# ai-bridge

Local message bridge between **ChatGPT through Codex CLI**, **Gemini through Antigravity `agy`**, and **Claude Code**. This README is an operating guide for both humans and AI agents. It is self-contained; no prior chat context is required.

The implementation uses Python 3.11+ and its standard-library SQLite driver. The durable message queue and transcript live in a local SQLite file; no database server or extra Python packages are required. Supported systems: macOS and Linux.

## Agent quickstart

Use this procedure when an AI agent is asked to use the bridge:

1. Work from `~/Documents/ai-bridge`. No build or package installation is required. Verify Python with `python3 --version`.
2. Select one project name. Use exactly the same project name for startup, sends, reads, and cancellation. The default is `ai-bridge`.
3. Check `python3 manage.py --project NAME status`. Respect the user's lifecycle preference: if they said they will run the daemon themselves or asked to leave it stopped, do not start it. Otherwise, when startup is within the requested task and it reports `Not running`, start with `python3 bridge.py --background --project NAME --folder /absolute/workspace`. Background startup never asks questions.
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

Follow this argument order in agent-generated commands: global flags **before the command**, then command-specific flags, then positional arguments:

```sh
python3 bridge.py --project my-app send --conversation CONVERSATION_ID --sender chatgpt claude 'Review the recommendation in this conversation.'
```

Keep `--project` before `send` and use the command-specific flag order shown above. Quote the message as a single argument. Prompts are passed to the provider through stdin without shell interpolation.

## Complete shared-context workflow for an orchestrator

Use this sequence when a task needs durable context. `my-app` and `123` are example identifiers; reuse the user's project and actual task ID. Commands assume the daemon is already running. If it is stopped and the user manages startup, report that and wait for them to start it.

1. Create missing templates with `context scaffold`. It does not fill in the task's actual requirements or attach files to messages.
2. Read the brief and task requirements. Populate them from the user's request using their **current returned versions**. Do not invent goals or constraints.
3. Select the minimum useful input documents and an output document with repeated `--context`. Include the output path even when it is new, because downstream writes are limited to selected files.
4. Send the request. Save both JSON fields: `id` identifies this delivery; `conversation` identifies its transcript.
5. Read that conversation at reasonable intervals until the target message is terminal (`done`, `failed`, or `cancelled`). Use a bounded wait appropriate to the requested task; if it expires, report the last state instead of resending. Check routed child messages too if present.
6. For a completed message, read `result`, then separately read the findings document. Confirm its content, version, and writer; a conversational reply alone does not prove a context write occurred.
7. For another downstream agent, select the updated findings document explicitly. A new direct `send` does not inherit attachments automatically; only agent-routed messages inherit them.
8. If a context write conflicts, compare the current document with the failed message's `context_proposals`, reconcile deliberately, and write against the current version. Do not retry the model call or overwrite newer context blindly.

Example commands:

```sh
python3 bridge.py --project my-app context scaffold --task 123 --writer chatgpt
python3 bridge.py --project my-app context read project/brief.md
python3 bridge.py --project my-app context read tasks/123/requirements.md
# Populate the templates using context write and the versions returned above.
python3 bridge.py --project my-app send --sender chatgpt --context project/brief.md --context tasks/123/requirements.md --context tasks/123/findings.md gemini 'Review the requirements. Save evidence and open questions in the selected findings file. Return messages: [] when finished.'
# Substitute the conversation field returned by send:
python3 bridge.py --project my-app messages --conversation CONVERSATION_ID
python3 bridge.py --project my-app context read tasks/123/findings.md
```

For a new scaffold, files start at version 1. For an existing project or task, their versions may be higher. Context writes replace the **entire document**, so preserve material that remains relevant when merging findings or decisions.

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

## Optional model and effort per message

Either setting can be omitted independently. Set them on `send`:

```sh
python3 bridge.py --project my-app send --effort high gemini 'Review this design.'
python3 bridge.py --project my-app send --model sonnet --effort high claude 'Review this design.'
python3 bridge.py --project my-app send --model MODEL_ID --effort medium chatgpt 'Review this design.'
```

Replace `MODEL_ID` with a model supported by that CLI/account. For Antigravity, `agy models` lists available IDs. No model is selected or changed by the bridge when the option is omitted. Available effort levels also depend on the chosen model and CLI version; the bridge does not clamp or substitute a requested setting.

| Recipient | Model option | Effort mapping |
| --- | --- | --- |
| `chatgpt` | `codex exec --model ID` | `-c 'model_reasoning_effort="LEVEL"'` |
| `gemini` | `agy --model ID` | `--effort LEVEL` |
| `claude` | `claude --model ID` | `--effort LEVEL` |

The bridge accepts `low`, `medium`, `high`, `xhigh`, and `max` for all three recipients. Codex also accepts `none`, `minimal`, and `ultra`; Claude also accepts `ultracode`. Those extended levels require a compatible model/client. The installed CLI remains responsible for validating model availability and combinations, and its errors are recorded on the delivery.

Precedence is: **this message's option → agent defaults in the profile configuration → existing native command flags → the CLI's own defaults**. To set persistent defaults, edit that profile's `config.json` under the relevant agent (keep its existing command/format fields):

```json
{
  "agents": {
    "gemini": {"model": "MODEL_ID_FROM_AGY_MODELS", "effort": "high"},
    "claude": {"effort": "medium"}
  }
}
```

This fragment is illustrative: merge the fields into the existing agent objects rather than replacing the file. Restart your daemon to load configuration changes. Omit a field to retain native defaults.

MCP `bridge_send` accepts optional `model` and `effort` fields. A managed agent can also put them on an outgoing routed message, for example `{"to":"claude","body":"Review this","model":"sonnet","effort":"high"}`. Settings apply to that recipient turn only; they do **not** automatically propagate to other agents or later direct messages. Each recipient uses its own defaults when settings are omitted.

The requested values are persisted in message records and logged as `requested_model`/`requested_effort`; the effective explicit selections appear as `configured_model`/`configured_effort` on provider events. Provider-reported model identity, when available, is separate. A CLI's implicit default is not guessed.

After upgrading the bridge, restart an older running daemon before using these options. New CLI/MCP clients check the daemon's capability and refuse to queue an override-bearing request if the server is too old to honor it.

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

Each project has a versioned context store shared by the orchestrator and downstream agents. The standard layout is created by the scaffold command:

```text
context/
  project/brief.md          # stable goals and constraints
  project/decisions.md      # accepted decisions
  tasks/TASK/requirements.md
  tasks/TASK/findings.md    # downstream findings / handoff
```

Create the standard project documents and a task's documents explicitly:

```sh
python3 bridge.py --project my-app context scaffold --task 123 --writer chatgpt
```

Use the actual task identifier in place of `123`. Omit `--task` to create only `project/brief.md` and `project/decisions.md`. The command creates missing documents with editable templates; it preserves existing content and versions. Repeating it is safe. No documents are automatically attached to messages. The equivalent MCP tool is `bridge_context_scaffold` with optional `task_id`.

The README, installed skill, and each managed agent's runtime prompt explain the role of these paths. They are a supported convention, not a restriction: custom document paths remain valid.

Find the exact location without starting the daemon:

```sh
python3 bridge.py --project my-app init
```

The JSON response's `home` is that profile's state directory; the readable files are at `<home>/context/`, and the database is `<home>/messages.sqlite3`. With the default installation, the shape is:

```text
~/Documents/ai-bridge/.bridge/projects/<name>-<hash>/context/
  project/brief.md
  project/decisions.md
  tasks/<task-id>/requirements.md
  tasks/<task-id>/findings.md
```

`--context project/brief.md` is a document name relative to this context store. It is not an arbitrary path in the code workspace, and it is not a path relative to the terminal's current directory. `--folder` selects the providers' code workspace; it does not relocate this private store. Use `context write NAME --file /absolute/source.md` to import a workspace file.

SQLite holds the authoritative content and immutable revisions. Readable mirrors live under the selected profile's `context/` directory. The orchestrator chooses documents **per message**; documents are not attached automatically just because they belong to the same project.

After a new scaffold, each document starts at version 1. The examples below use that initial version; for an existing document, read and use its current version instead. Read/write through the CLI while the daemon is running:

```sh
python3 bridge.py --project my-app context write project/brief.md --text 'Goals and constraints' --expected-version 1 --writer chatgpt
python3 bridge.py --project my-app context write tasks/123/requirements.md --file /path/to/requirements.md --expected-version 1
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
    {"path": "tasks/123/findings.md", "expected_version": 1, "content": "Full findings text"}
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
| `bridge_send` | `to`, `body`, optional `conversation`, `context_files`, `model`, `effort` | Queued message ID and conversation ID |
| `bridge_messages` | optional `conversation`, `after` | Up to 200 transcript rows and states |
| `bridge_status` | none | Daemon health, project, and executable availability |
| `bridge_stop` | `conversation` | Cancel queued work and further routing |
| `bridge_context_scaffold` | optional `task_id` | Create standard project/task documents without overwriting |
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

Foreground runs (`make run`, `--foreground`, interactive mode, and direct `serve`) stream operation events immediately to **stderr** and keep the same events in the rotating file. Startup prints the exact log-file path. To opt out of terminal events, use `python3 bridge.py --log-file-only --foreground --project NAME`. Background and generated launchd services use file-only operation logging, keeping `console.log` for startup/crash diagnostics.

Follow the rotating file with `make logs PROJECT=NAME` (also works while an older daemon is still running). Source changes do not hot-reload: if you only see the old startup lines, stop your foreground process and run `make run` again to load the console handler. The log will show `daemon_started`, then enqueue/delivery/provider/HTTP events as the bridge is used.

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
| `requested_model`, `requested_effort` | Per-message choices, when provided |
| `configured_effort` | Effective explicit native reasoning-effort selection |
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
| `agents.NAME.model` | omitted | Default model ID/alias for that recipient; overridden by `send --model` |
| `agents.NAME.effort` | omitted | Default effort for that recipient; overridden by `send --effort` |
| `agents.NAME.input_format` | Provider-specific | `antigravity` sends an NDJSON user event; otherwise plain stdin |

Executable paths are resolved through PATH and `~/.local/bin` when configuration is created. The `gemini` adapter defaults to `agy`. Each CLI uses its existing local login and account allowance. Authenticate interactively once if necessary. Executable availability in health checks does not prove authentication or model access.

Codex defaults to a read-only sandbox, Antigravity to plan mode, and Claude to built-in tools disabled. No permission-bypass flags are enabled. CLIs can load their own settings, hooks, plugins, and MCP servers; the bridge is not an adversarial-agent sandbox.

State is stored in `messages.sqlite3` with SQLite WAL journaling. Queue claims and conversation budgets use immediate transactions. Replies and routed child messages commit atomically. A daemon file lock prevents competing processes from delivering the same queue. Each operation opens a short-lived connection, while the SQLite transaction handles concurrency between the HTTP handlers and worker. No ORM or third-party database driver is used.

Back up a stopped profile directory to preserve its history, configuration, token, and native project binding. If backing up a running profile, use SQLite's backup API rather than copying only the database file while ignoring its WAL. Do not share or commit the profile directory.

## Verify

Requires Python 3.11+ with its standard-library `sqlite3` module (available in the installed Python on this machine). No `pip install` step is needed.

```sh
make test
python3 -m py_compile bridge.py manage.py context_store.py provider_options.py
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
| POST | `/context/scaffold` | `{ "task_id":"123", "writer":"chatgpt" }`; task ID optional |
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
