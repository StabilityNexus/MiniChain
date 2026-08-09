"""
Chain persistence: save and load the blockchain and state to/from SQLite.

Design:
  - data.db holds the full chain snapshot, account state, and small metadata.
  - legacy data.json snapshots can still be loaded for backward compatibility.

The public API intentionally stays the same:
    from minichain.persistence import save, load

    save(blockchain, path="data/")
    blockchain = load(path="data/")
"""

from __future__ import annotations

import copy
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any

from .block import Block
from .chain import Blockchain

logger = logging.getLogger(__name__)

_DB_FILE = "data.db"
_LEGACY_DATA_FILE = "data.json"


def persistence_exists(path: str = ".") -> bool:
    """Return True if a SQLite or legacy JSON snapshot exists inside *path*."""
    return os.path.exists(os.path.join(path, _DB_FILE)) or os.path.exists(
        os.path.join(path, _LEGACY_DATA_FILE)
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def save(blockchain: Blockchain, path: str = ".") -> None:
    """Persist the blockchain and account state to SQLite inside *path*."""
    os.makedirs(path, exist_ok=True)
    db_path = os.path.join(path, _DB_FILE)

    with blockchain._lock:
        chain_data = [block.to_dict() for block in blockchain.chain]
        state_data = copy.deepcopy(blockchain.state.accounts)

    _save_snapshot_to_sqlite(db_path, {"chain": chain_data, "state": state_data})

    logger.info(
        "Saved %d blocks and %d accounts to '%s'",
        len(chain_data),
        len(state_data),
        path,
    )


def load(path: str = ".") -> Blockchain:
    """Restore a Blockchain from SQLite inside *path* (with legacy JSON fallback).

    Blocks are replayed through the canonical ``Blockchain._apply_block()``
    pipeline so that difficulty adjustment, PoW, receipt validation, state-root
    validation and transaction validation are all performed by the same code
    path used at runtime.  No parallel validation logic is maintained here.
    """
    db_path = os.path.join(path, _DB_FILE)
    legacy_path = os.path.join(path, _LEGACY_DATA_FILE)

    if os.path.exists(db_path):
        try:
            snapshot = _load_snapshot_from_sqlite(db_path)
        except ValueError:
            if not os.path.exists(legacy_path):
                raise
            snapshot = _read_legacy_json(legacy_path)
    elif os.path.exists(legacy_path):
        snapshot = _read_legacy_json(legacy_path)
    else:
        raise FileNotFoundError(f"Persistence file not found in '{path}'")

    if not isinstance(snapshot, dict):
        raise ValueError(f"Invalid snapshot data in '{path}'")

    raw_blocks = snapshot.get("chain")
    raw_accounts = snapshot.get("state")

    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise ValueError(f"Invalid or empty chain data in '{path}'")
    if not isinstance(raw_accounts, dict):
        raise ValueError(f"Invalid accounts data in '{path}'")
    for address, account in raw_accounts.items():
        if not isinstance(address, str) or not isinstance(account, dict):
            raise ValueError(f"Invalid accounts data in '{path}'")

    blocks = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, dict):
            raise ValueError(f"Invalid chain data in '{path}'")
        try:
            blocks.append(Block.from_dict(raw_block))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid chain data in '{path}'") from exc

    blockchain = Blockchain()

    from .pow import calculate_hash
    genesis = blocks[0]
    if genesis.index != 0:
        raise ValueError("Invalid genesis block")
    if genesis.hash != calculate_hash(genesis.to_header_dict()):
        raise ValueError("Invalid genesis block hash")
    if genesis.hash != blockchain.chain[0].hash:
        raise ValueError(
            f"Persisted genesis hash {genesis.hash!r} does not match "
            f"local genesis {blockchain.chain[0].hash!r}"
        )

    from .validators import ValidationStatus
    from .state import State

    temp_state = State()
    temp_state.chain_id = blockchain.chain_id
    temp_state.restore(blockchain._genesis_state_snapshot)

    temp_difficulty = blocks[0].difficulty
    temp_avg_block_time = blockchain.target_block_time

    for i in range(1, len(blocks)):
        status, temp_difficulty, temp_avg_block_time = blockchain._apply_block(
            blocks[i - 1], blocks[i], temp_state, temp_difficulty, temp_avg_block_time
        )
        if status != ValidationStatus.VALID:
            raise ValueError(
                f"Block #{blocks[i].index} failed validation during load "
                f"(status={status.name})"
            )

    blockchain.chain = blocks
    blockchain.state = temp_state
    blockchain.current_difficulty = temp_difficulty
    blockchain.avg_block_time = temp_avg_block_time

    logger.info(
        "Loaded %d blocks and %d accounts from '%s'",
        len(blockchain.chain),
        len(blockchain.state.accounts),
        path,
    )
    return blockchain


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS blocks (
            height INTEGER PRIMARY KEY,
            block_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS accounts (
            address TEXT PRIMARY KEY,
            account_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """
    )


def _require_schema(conn: sqlite3.Connection) -> None:
    required = {"blocks", "accounts", "metadata"}
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    existing = {row["name"] for row in rows}
    if not required.issubset(existing):
        raise ValueError("Missing persistence tables")


def _save_snapshot_to_sqlite(db_path: str, snapshot: dict[str, Any]) -> None:
    conn = _connect(db_path)
    try:
        _initialize_schema(conn)
        with conn:
            conn.execute("DELETE FROM blocks")
            conn.execute("DELETE FROM accounts")
            conn.execute("DELETE FROM metadata")

            for block in snapshot["chain"]:
                conn.execute(
                    "INSERT INTO blocks (height, block_json) VALUES (?, ?)",
                    (int(block["index"]), json.dumps(block, sort_keys=True)),
                )

            for address, account in sorted(snapshot["state"].items()):
                conn.execute(
                    "INSERT INTO accounts (address, account_json) VALUES (?, ?)",
                    (address, json.dumps(account, sort_keys=True)),
                )

            conn.execute(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                ("chain_length", str(len(snapshot["chain"]))),
            )
    finally:
        conn.close()


def _load_snapshot_from_sqlite(db_path: str) -> dict[str, Any]:
    invalid = f"Invalid SQLite persistence data in '{db_path}'"
    try:
        conn = _connect(db_path)
    except sqlite3.DatabaseError as exc:
        raise ValueError(invalid) from exc

    try:
        _require_schema(conn)
        block_rows = conn.execute(
            "SELECT block_json FROM blocks ORDER BY height ASC"
        ).fetchall()
        account_rows = conn.execute(
            "SELECT address, account_json FROM accounts ORDER BY address ASC"
        ).fetchall()
        chain_length_row = conn.execute(
            "SELECT value FROM metadata WHERE key = ?",
            ("chain_length",),
        ).fetchone()
    except sqlite3.DatabaseError as exc:
        raise ValueError(invalid) from exc
    finally:
        conn.close()

    if chain_length_row is None:
        raise ValueError(invalid)
    try:
        expected_chain_length = int(chain_length_row["value"])
    except (TypeError, ValueError) as exc:
        raise ValueError(invalid) from exc
    if expected_chain_length != len(block_rows):
        raise ValueError(invalid)

    try:
        chain = [json.loads(row["block_json"]) for row in block_rows]
        state = {
            row["address"]: json.loads(row["account_json"])
            for row in account_rows
        }
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid persisted JSON payload in '{db_path}'") from exc

    return {"chain": chain, "state": state}


# ---------------------------------------------------------------------------
# Banned Peers (Track 1)
# ---------------------------------------------------------------------------


def _ensure_banned_peers_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS banned_peers (peer_id TEXT PRIMARY KEY, reason TEXT, timestamp REAL)"
    )


@contextmanager
def _banned_peers_conn(path: str, create: bool):
    """
    Yield a connection with the banned_peers table ensured, always closing it.
    When *create* is False and no DB exists yet, yields None so read-only callers
    can short-circuit without touching the filesystem.
    """
    db_path = os.path.join(path, _DB_FILE)
    if not create and not os.path.exists(db_path):
        yield None
        return
    if create:
        os.makedirs(path, exist_ok=True)
    conn = _connect(db_path)
    try:
        _ensure_banned_peers_table(conn)
        yield conn
    finally:
        conn.close()

def ban_peer(peer_id: str, reason: str, path: str = ".") -> None:
    with _banned_peers_conn(path, create=True) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO banned_peers (peer_id, reason, timestamp) VALUES (?, ?, ?)",
            (peer_id, reason, time.time())
        )

def unban_peer(peer_id: str, path: str = ".") -> None:
    with _banned_peers_conn(path, create=False) as conn:
        if conn is None:
            return
        with conn:
            conn.execute("DELETE FROM banned_peers WHERE peer_id = ?", (peer_id,))

def is_peer_banned(peer_id: str, path: str = ".") -> bool:
    with _banned_peers_conn(path, create=False) as conn:
        if conn is None:
            return False
        return conn.execute("SELECT peer_id FROM banned_peers WHERE peer_id = ?", (peer_id,)).fetchone() is not None

def get_banned_peers(path: str = ".") -> list[dict[str, Any]]:
    with _banned_peers_conn(path, create=False) as conn:
        if conn is None:
            return []
        rows = conn.execute("SELECT peer_id, reason, timestamp FROM banned_peers ORDER BY timestamp DESC").fetchall()
        return [{"peer_id": r["peer_id"], "reason": r["reason"], "timestamp": r["timestamp"]} for r in rows]


# ---------------------------------------------------------------------------
# Legacy JSON helpers
# ---------------------------------------------------------------------------


def _read_legacy_json(filepath: str) -> dict[str, Any]:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)



