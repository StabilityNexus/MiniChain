# MiniChain

MiniChain is a minimal, research-oriented blockchain implementation in Python. This repository currently contains the project scaffolding and development environment for the v0 core chain roadmap.

## Current Status

Issue #1 (project scaffolding) is implemented with:

- Python package layout under `src/minichain`
- Placeholder component modules for:
  - `crypto`, `transaction`, `block`, `state`, `mempool`, `consensus`, `network`, `storage`, `node`
- `pyproject.toml` project configuration
- `Makefile` for common developer tasks
- Basic CI workflow (`.github/workflows/ci.yml`)
- Baseline tests under `tests/`

## Requirements

- Python 3.11+

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
make dev-install
```

If you also want networking dependencies:

```bash
python -m pip install -e .[network]
```

## Common Commands

```bash
make test        # run unit tests
make lint        # run ruff checks
make format      # format with ruff
make start-node  # run scaffold node entrypoint
```

## Run the Node Entrypoint

```bash
PYTHONPATH=src python -m minichain --host 127.0.0.1 --port 7000
```

## Repository Layout

```text
.github/workflows/ci.yml
src/minichain/
  __init__.py
  __main__.py
  crypto.py
  transaction.py
  block.py
  state.py
  mempool.py
  consensus.py
  network.py
  storage.py
  node.py
tests/
  test_scaffold.py
issues.md
architectureProposal.md
```
