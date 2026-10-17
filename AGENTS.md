# Working with ai-bridge

Read README.md before operating the bridge. It documents exact CLI argument ordering, project selection, asynchronous reply polling, MCP setup, and the routing envelope.

Implementation: Python 3.11+ standard library with SQLite; no pip dependencies or database server. Run `make test` and `python3 -m py_compile bridge.py manage.py` after code changes. Do not commit `.bridge/` or `__pycache__/`.

Unattended startup: `python3 bridge.py --background --project NAME [--folder PATH]`. Interactive startup is foreground only. Always select the same project for sends and reads. `bridge.py stop ID` cancels a conversation; `manage.py --project NAME stop` stops the daemon. Operation logs contain metadata only; transcripts and raw failure details stay in the private SQLite database.
