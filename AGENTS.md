# Working with ai-bridge

Read README.md before operating the bridge. It documents exact CLI argument ordering, project selection, asynchronous reply polling, MCP setup, and the routing envelope.

Implementation: Python 3.11+ standard library with SQLite; no pip dependencies or database server. Run `make test` and `python3 -m py_compile bridge.py manage.py` after code changes. Do not commit `.bridge/` or `__pycache__/`.

Unattended startup: `python3 bridge.py --background --project NAME [--folder PATH]`. Interactive startup is foreground only. Always select the same project for sends and reads. `bridge.py stop ID` cancels a conversation; `manage.py --project NAME stop` stops the daemon. Operation logs contain metadata only; transcripts and raw failure details stay in the private SQLite database.

Before a shared-context task, use `context scaffold --task TASK_ID` to create the standard documents without overwriting existing content. The layout and per-file responsibilities are defined in README.md and the installed ai-bridge skill. Select attachments explicitly with repeated `--context`; scaffolding does not attach them automatically.

For a complete orchestration sequence, follow README.md's “Complete shared-context workflow for an orchestrator.” Respect user-managed startup: never restart the daemon after a request to leave it stopped. `--context` paths are names inside the profile context store, not arbitrary workspace files; use `init` to discover the store's location.
