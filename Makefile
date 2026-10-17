PYTHON ?= python3
BRIDGE_HOME ?= $(CURDIR)/.bridge
PROJECT ?= ai-bridge
FOLDER ?=
PORT ?=
PROFILE_ARGS = --home "$(BRIDGE_HOME)" --project "$(PROJECT)" $(if $(FOLDER),--folder "$(FOLDER)") $(if $(PORT),--port "$(PORT)")

.PHONY: help init interactive run foreground start background stop restart status logs test

help:
	@echo "PROJECT=name FOLDER=/path PORT=8765 select the workspace and service port"
	@echo "make interactive Prompt for project/folder, then run in foreground"
	@echo "make run         Run in foreground (Ctrl-C stops)"
	@echo "make start       Run in background"
	@echo "make stop        Stop the daemon"
	@echo "make restart     Restart in background"
	@echo "make status      Show daemon status"
	@echo "make logs        Follow structured operation logs"
	@echo "make test        Run automated tests"

init:
	$(PYTHON) bridge.py $(PROFILE_ARGS) init

interactive:
	$(PYTHON) bridge.py --home "$(BRIDGE_HOME)" --interactive

run foreground:
	$(PYTHON) bridge.py $(PROFILE_ARGS) --foreground

start background:
	$(PYTHON) bridge.py $(PROFILE_ARGS) --background

stop:
	$(PYTHON) manage.py $(PROFILE_ARGS) stop

restart:
	$(PYTHON) manage.py $(PROFILE_ARGS) restart

status:
	$(PYTHON) manage.py $(PROFILE_ARGS) status

logs: init
	$(PYTHON) manage.py $(PROFILE_ARGS) logs

test:
	$(PYTHON) -m unittest discover -s tests -v
