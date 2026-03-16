"""
Chain persistence: save and load the blockchain and state to/from JSON.

Design:
  - blockchain.json  holds the full list of serialised blocks
  - state.json       holds the accounts dict (includes off-chain credits)

Both files are written atomically (temp → rename) to prevent corruption
on crash.  On load, chain integrity is verified before the data is trusted.

Usage:
    from minichain.persistence import save, load

    save(blockchain, path="data/")
    blockchain = load(path="data/")
"""

import json
import os
import tempfile
import logging
import copy

from .block import Block
from .transaction import Transaction
from .chain import Blockchain
from .state import State
from .pow import calculate_hash

logger = logging.getLogger(__name__)

_DATA_FILE = "data.json"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def save(blockchain: Blockchain, path: str = ".") -> None:
    """
    Persist the blockchain and account state to a JSON file inside *path*.

    Uses atomic write (write-to-temp → rename) with fsync so a crash mid-save
    never corrupts the existing file. Chain and state are saved together to
    prevent torn snapshots.
    """
    os.makedirs(path, exist_ok=True)

    with blockchain._lock:  # Thread-safe: hold lock while serialising
        chain_data = [block.to_dict() for block in blockchain.chain]
        state_data = copy.deepcopy(blockchain.state.accounts)

    snapshot = {
        "chain": chain_data,
        "state": state_data
    }

    _atomic_write_json(os.path.join(path, _DATA_FILE), snapshot)

    logger.info(
        "Saved %d blocks and %d accounts to '%s'",
        len(chain_data),
        len(state_data),
        path,
    )


def load(path: str = ".") -> Blockchain:
    """
    Restore a Blockchain from the JSON file inside *path*.

    Steps:
      1. Load and deserialise blocks from data.json
      2. Verify chain integrity (genesis, linkage, hashes)
      3. Load account state

    Raises:
        FileNotFoundError: if data.json is missing.
        ValueError:        if data is invalid or integrity checks fail.
    """
    data_path = os.path.join(path, _DATA_FILE)
    snapshot = _read_json(data_path)

    if not isinstance(snapshot, dict):
        raise ValueError(f"Invalid snapshot data in '{data_path}'")

    raw_blocks = snapshot.get("chain")
    raw_accounts = snapshot.get("state")

    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise ValueError(f"Invalid or empty chain data in '{data_path}'")
    if not isinstance(raw_accounts, dict):
        raise ValueError(f"Invalid accounts data in '{data_path}'")

    blocks = [_deserialize_block(b) for b in raw_blocks]

    # --- Integrity verification ---
    _verify_chain_integrity(blocks)

    # --- Rebuild blockchain properly (no __new__ hack) ---
    blockchain = Blockchain()           # creates genesis + fresh state
    blockchain.chain = blocks           # replace with loaded chain

    # Restore state
    blockchain.state.accounts = raw_accounts

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

def _verify_chain_integrity(blocks: list) -> None:
    """Verify genesis, hash linkage, and block hashes."""
    # Check genesis
    genesis = blocks[0]
    if genesis.index != 0 or genesis.hash != "0" * 64:
        raise ValueError("Invalid genesis block")

    # Check linkage and hashes for every subsequent block
    for i in range(1, len(blocks)):
        block = blocks[i]
        prev = blocks[i - 1]

        if block.index != prev.index + 1:
            raise ValueError(
                f"Block #{block.index}: index gap (expected {prev.index + 1})"
            )

        if block.previous_hash != prev.hash:
            raise ValueError(
                f"Block #{block.index}: previous_hash mismatch"
            )

        expected_hash = calculate_hash(block.to_header_dict())
        if block.hash != expected_hash:
            raise ValueError(
                f"Block #{block.index}: hash mismatch "
                f"(stored={block.hash[:16]}..., computed={expected_hash[:16]}...)"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _atomic_write_json(filepath: str, data) -> None:
    """Write JSON atomically with fsync for durability."""
    dir_name = os.path.dirname(filepath) or "."
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())  # Ensure data is on disk
        os.replace(tmp_path, filepath)   # Atomic rename

        # Attempt to fsync the directory so the rename is durable
        if hasattr(os, "O_DIRECTORY"):
            try:
                dir_fd = os.open(dir_name, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass  # Directory fsync not supported on all platforms

    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _read_json(filepath: str):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"Persistence file not found: '{filepath}'")
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def _deserialize_block(data: dict) -> Block:
    """Reconstruct a Block (including its transactions) from a plain dict."""
    transactions = [
        Transaction(
            sender=tx["sender"],
            receiver=tx["receiver"],
            amount=tx["amount"],
            nonce=tx["nonce"],
            data=tx.get("data"),
            signature=tx.get("signature"),
            timestamp=tx["timestamp"],
        )
        for tx in data.get("transactions", [])
    ]

    block = Block(
        index=data["index"],
        previous_hash=data["previous_hash"],
        transactions=transactions,
        timestamp=data["timestamp"],
        difficulty=data.get("difficulty"),
    )
    block.nonce = data["nonce"]
    block.hash = data["hash"]
    # Only overwrite merkle_root if explicitly saved; otherwise keep computed value
    if "merkle_root" in data:
        block.merkle_root = data["merkle_root"]
    return block
