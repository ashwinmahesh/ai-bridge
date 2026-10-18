# Packaged API service development

Read API.md and SKILL.md for the v2 multi-project contract. `ai_bridge.py` is the distribution entry point; `service.py` reuses bridge.Store/Worker/ContextStore. All runtime project/message/config/context/log operations are HTTP; CLI is limited to serving and login-service installation. Legacy commands below remain compatible.

Run `make test`, build with `make build`, then `AI_BRIDGE_TEST_BINARY="$PWD/dist/ai-bridge" python3 tests/test_service.py -v`. Use fake providers, temporary homes and ephemeral ports. Never restart a user-managed bridge or activate their real login agent to test changes. Do not commit tokens, SQLite state, build environments, or generated binaries. SKILL.md and skills/ai-bridge/SKILL.md must remain identical. Release archives include README.md, API.md and the skill.

# Working with ai-bridge

Read README.md before operating the bridge. It documents exact CLI argument ordering, project selection, asynchronous reply polling, MCP setup, and the routing envelope.

Implementation: Python 3.11+ standard library with SQLite; no pip dependencies or database server. Run `make test` and `python3 -m py_compile bridge.py manage.py` after code changes. Do not commit `.bridge/` or `__pycache__/`.

Unattended startup: `python3 bridge.py --background --project NAME [--folder PATH]`. Interactive startup is foreground only. Always select the same project for sends and reads. `bridge.py stop ID` cancels a conversation; `manage.py --project NAME stop` stops the daemon. Operation logs contain metadata only; transcripts and raw failure details stay in the private SQLite database.

Before a shared-context task, use `context scaffold --task TASK_ID` to create the standard documents without overwriting existing content. The layout and per-file responsibilities are defined in README.md and the installed ai-bridge skill. Select attachments explicitly with repeated `--context`; scaffolding does not attach them automatically.

For a complete orchestration sequence, follow README.md's “Complete shared-context workflow for an orchestrator.” Respect user-managed startup: never restart the daemon after a request to leave it stopped. `--context` paths are names inside the profile context store, not arbitrary workspace files; use `init` to discover the store's location.

`send --model ID --effort LEVEL` and the corresponding MCP fields select the recipient turn's settings. Leave unspecified fields unset. Foreground events stream to stderr; background events stay in the rotating JSONL file. Do not restart a user-managed daemon to deploy changes; tell the user a restart is needed.
