PYTHON ?= python3

.PHONY: install dev-install test lint format start-node

install:
	$(PYTHON) -m pip install .

dev-install:
	$(PYTHON) -m pip install -e .[dev]

test:
	$(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check src tests

format:
	$(PYTHON) -m ruff format src tests

start-node:
	PYTHONPATH=src $(PYTHON) -m minichain --host 127.0.0.1 --port 7000
