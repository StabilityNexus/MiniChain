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

import json
import logging
import os
import sqlite3
from typing import Any

from .block import Block
from .chain import Blockchain, validate_block_link_and_hash

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
        state_data = json.loads(json.dumps(blockchain.state.accounts))

    _save_snapshot_to_sqlite(db_path, {"chain": chain_data, "state": state_data})

    logger.info(
        "Saved %d blocks and %d accounts to '%s'",
        len(chain_data),
        len(state_data),
        path,
    )


def load(path: str = ".") -> Blockchain:
    """Restore a Blockchain from SQLite inside *path* (with legacy JSON fallback)."""
    db_path = os.path.join(path, _DB_FILE)
    legacy_path = os.path.join(path, _LEGACY_DATA_FILE)

    if os.path.exists(db_path):
        snapshot = _load_snapshot_from_sqlite(db_path)
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

    blocks = []
    for raw_block in raw_blocks:
        if not isinstance(raw_block, dict):
            raise ValueError(f"Invalid chain data in '{path}'")
        try:
            blocks.append(_deserialize_block(raw_block))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid chain data in '{path}'") from exc

    normalized_accounts = {}
    for address, account in raw_accounts.items():
        if not isinstance(address, str) or not isinstance(account, dict):
            raise ValueError(f"Invalid accounts data in '{path}'")
        normalized_accounts[address] = account

    _verify_chain_integrity(blocks)

    blockchain = Blockchain()
    blockchain.chain = blocks
    blockchain.state.accounts = normalized_accounts

    logger.info(
        "Loaded %d blocks and %d accounts from '%s'",
        len(blockchain.chain),
        len(blockchain.state.accounts),
        path,
    )
    return blockchain


# ---------------------------------------------------------------------------
# Integrity verification
# ---------------------------------------------------------------------------


def _verify_chain_integrity(blocks: list[Block]) -> None:
    """Verify genesis, hash linkage, and block hashes."""
    genesis = blocks[0]
    if genesis.index != 0 or genesis.hash != "0" * 64:
        raise ValueError("Invalid genesis block")

    for i in range(1, len(blocks)):
        block = blocks[i]
        prev = blocks[i - 1]
        try:
            validate_block_link_and_hash(prev, block)
        except ValueError as exc:
            raise ValueError(f"Block #{block.index}: {exc}") from exc


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
    try:
        conn = _connect(db_path)
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Invalid SQLite persistence data in '{db_path}'") from exc

    try:
        _require_schema(conn)
        block_rows = conn.execute(
            "SELECT block_json FROM blocks ORDER BY height ASC"
        ).fetchall()
        account_rows = conn.execute(
            "SELECT address, account_json FROM accounts ORDER BY address ASC"
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError(f"Invalid SQLite persistence data in '{db_path}'") from exc
    finally:
        conn.close()

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
# Legacy JSON helpers
# ---------------------------------------------------------------------------


def _read_legacy_json(filepath: str) -> dict[str, Any]:
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Block deserialisation
# ---------------------------------------------------------------------------


def _deserialize_block(data: dict[str, Any]) -> Block:
    return Block.from_dict(data)
