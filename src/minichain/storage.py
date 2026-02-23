"""Persistent storage integration using SQLite."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from minichain.block import Block, BlockHeader
from minichain.state import Account, State
from minichain.transaction import Transaction


class StorageError(ValueError):
    """Raised when persistence operations fail validation or constraints."""


def _is_lower_hex(value: str, expected_length: int) -> bool:
    if len(value) != expected_length:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


class SQLiteStorage:
    """SQLite-backed block/state persistence."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        self._connection = sqlite3.connect(self.db_path)
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteStorage:
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        self.close()

    def _initialize_schema(self) -> None:
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS blocks (
                hash TEXT PRIMARY KEY,
                height INTEGER NOT NULL UNIQUE,
                version INTEGER NOT NULL,
                previous_hash TEXT NOT NULL,
                merkle_root TEXT NOT NULL,
                timestamp INTEGER NOT NULL,
                difficulty_target TEXT NOT NULL,
                nonce INTEGER NOT NULL,
                transactions_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS accounts (
                address TEXT PRIMARY KEY,
                balance INTEGER NOT NULL,
                nonce INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chain_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self._connection.commit()

    def store_block(self, block: Block, *, connection: sqlite3.Connection | None = None) -> None:
        """Persist a block by hash and height."""
        if connection is None:
            with self._connection:
                self.store_block(block, connection=self._connection)
            return

        if not block.has_valid_merkle_root():
            raise StorageError("Block merkle_root does not match transactions")

        block_hash = block.hash().hex()
        transactions_json = json.dumps(
            [asdict(transaction) for transaction in block.transactions],
            sort_keys=True,
            separators=(",", ":"),
        )
        conn = connection

        try:
            conn.execute(
                """
                INSERT INTO blocks (
                    hash, height, version, previous_hash, merkle_root,
                    timestamp, difficulty_target, nonce, transactions_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    block_hash,
                    block.header.block_height,
                    block.header.version,
                    block.header.previous_hash,
                    block.header.merkle_root,
                    block.header.timestamp,
                    str(block.header.difficulty_target),
                    block.header.nonce,
                    transactions_json,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise StorageError(
                f"Block already exists or violates constraints: {block_hash}"
            ) from exc

    def get_block_by_hash(self, block_hash: str) -> Block | None:
        """Load a block by hash."""
        row = self._connection.execute(
            """
            SELECT
                height, version, previous_hash, merkle_root, timestamp,
                difficulty_target, nonce, transactions_json
            FROM blocks
            WHERE hash = ?
            """,
            (block_hash,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_block(row)

    def get_block_by_height(self, height: int) -> Block | None:
        """Load a block by canonical height."""
        row = self._connection.execute(
            """
            SELECT
                height, version, previous_hash, merkle_root, timestamp,
                difficulty_target, nonce, transactions_json
            FROM blocks
            WHERE height = ?
            """,
            (height,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_block(row)

    def save_state(self, state: State, *, connection: sqlite3.Connection | None = None) -> None:
        """Persist all accounts as the current canonical state snapshot."""
        if connection is None:
            with self._connection:
                self.save_state(state, connection=self._connection)
            return

        conn = connection

        for address, account in state.accounts.items():
            if not _is_lower_hex(address, 40):
                raise StorageError(f"Invalid account address: {address}")
            if account.balance < 0 or account.nonce < 0:
                raise StorageError(f"Invalid account values for {address}")

        conn.execute("DELETE FROM accounts")
        rows = [
            (address, account.balance, account.nonce)
            for address, account in sorted(state.accounts.items())
        ]
        conn.executemany(
            "INSERT INTO accounts (address, balance, nonce) VALUES (?, ?, ?)",
            rows,
        )

    def load_state(self) -> State:
        """Load the latest persisted account snapshot."""
        state = State()
        rows = self._connection.execute(
            "SELECT address, balance, nonce FROM accounts ORDER BY address"
        ).fetchall()
        for address, balance, nonce in rows:
            state.set_account(address, Account(balance=balance, nonce=nonce))
        return state

    def save_chain_metadata(
        self,
        *,
        height: int,
        head_hash: str,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        """Persist canonical chain metadata."""
        if connection is None:
            with self._connection:
                self.save_chain_metadata(
                    height=height,
                    head_hash=head_hash,
                    connection=self._connection,
                )
            return

        if height < 0:
            raise StorageError("height must be non-negative")
        if not _is_lower_hex(head_hash, 64):
            raise StorageError("head_hash must be a 32-byte lowercase hex string")

        conn = connection
        conn.execute(
            """
            INSERT INTO chain_metadata (key, value) VALUES ('height', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(height),),
        )
        conn.execute(
            """
            INSERT INTO chain_metadata (key, value) VALUES ('head_hash', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (head_hash,),
        )

    def load_chain_metadata(self) -> dict[str, int | str] | None:
        """Load canonical chain metadata (height and head hash)."""
        rows = self._connection.execute(
            "SELECT key, value FROM chain_metadata WHERE key IN ('height', 'head_hash')"
        ).fetchall()
        if not rows:
            return None
        kv = {key: value for key, value in rows}
        if "height" not in kv or "head_hash" not in kv:
            raise StorageError("Incomplete chain metadata in storage")
        return {"height": int(kv["height"]), "head_hash": kv["head_hash"]}

    def persist_block_state_and_metadata(
        self,
        *,
        block: Block,
        state: State,
        height: int | None = None,
        head_hash: str | None = None,
    ) -> None:
        """Atomically persist block, state snapshot, and metadata."""
        resolved_height = block.header.block_height if height is None else height
        resolved_head_hash = block.hash().hex() if head_hash is None else head_hash

        with self._connection:
            self.store_block(block, connection=self._connection)
            self.save_state(state, connection=self._connection)
            self.save_chain_metadata(
                height=resolved_height,
                head_hash=resolved_head_hash,
                connection=self._connection,
            )

    @staticmethod
    def _row_to_block(row: sqlite3.Row | tuple[object, ...]) -> Block:
        (
            height,
            version,
            previous_hash,
            merkle_root,
            timestamp,
            difficulty_target,
            nonce,
            transactions_json,
        ) = row
        header = BlockHeader(
            version=int(version),
            previous_hash=str(previous_hash),
            merkle_root=str(merkle_root),
            timestamp=int(timestamp),
            difficulty_target=int(difficulty_target),
            nonce=int(nonce),
            block_height=int(height),
        )
        transaction_dicts = json.loads(str(transactions_json))
        transactions = [Transaction(**tx) for tx in transaction_dicts]
        block = Block(header=header, transactions=transactions)
        if not block.has_valid_merkle_root():
            raise StorageError("Corrupt block data: merkle_root mismatch")
        return block
