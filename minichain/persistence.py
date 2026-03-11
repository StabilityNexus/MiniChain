"""
Chain persistence: save and load the blockchain and state to/from JSON.

Design:
  - blockchain.json  holds the full list of serialized blocks
  - state.json       holds the accounts dict

Usage:
    from minichain.persistence import save, load

    save(blockchain, path="data/")
    blockchain = load(path="data/")
"""

import json
import os
import logging
from .block import Block
from .transaction import Transaction
from .chain import Blockchain

logger = logging.getLogger(__name__)

_CHAIN_FILE = "blockchain.json"
_STATE_FILE = "state.json"


# Public API

def save(blockchain: Blockchain, path: str = ".") -> None:
    """
    Persist the blockchain and account state to two JSON files inside `path`.

    Args:
        blockchain: The live Blockchain instance to save.
        path:       Directory to write blockchain.json and state.json into.
    """
    os.makedirs(path, exist_ok=True)

    _write_json(
        os.path.join(path, _CHAIN_FILE),
        [block.to_dict() for block in blockchain.chain],
    )

    _write_json(
        os.path.join(path, _STATE_FILE),
        blockchain.state.accounts,
    )

    logger.info(
        "Saved %d blocks and %d accounts to '%s'",
        len(blockchain.chain),
        len(blockchain.state.accounts),
        path,
    )


def load(path: str = ".") -> Blockchain:
    """
    Restore a Blockchain from JSON files inside `path`.

    Returns a fully initialised Blockchain whose chain and state match
    what was previously saved with save().

    Raises:
        FileNotFoundError: if blockchain.json or state.json are missing.
        ValueError:        if the data is structurally invalid.
    """
    chain_path = os.path.join(path, _CHAIN_FILE)
    state_path = os.path.join(path, _STATE_FILE)

    raw_blocks = _read_json(chain_path)
    raw_accounts = _read_json(state_path)

    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise ValueError(f"Invalid or empty chain data in '{chain_path}'")

    blockchain = Blockchain.__new__(Blockchain)   # skip __init__ (no genesis)
    import threading
    from .state import State
    from .contract import ContractMachine

    blockchain._lock = threading.RLock()
    blockchain.chain = [_deserialize_block(b) for b in raw_blocks]

    blockchain.state = State.__new__(State)
    blockchain.state.accounts = raw_accounts
    blockchain.state.contract_machine = ContractMachine(blockchain.state)

    logger.info(
        "Loaded %d blocks and %d accounts from '%s'",
        len(blockchain.chain),
        len(blockchain.state.accounts),
        path,
    )
    return blockchain


# Helpers

def _write_json(filepath: str, data) -> None:
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


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
    # Preserve the stored merkle root rather than recomputing to guard against
    # any future change in the hash algorithm.
    block.merkle_root = data.get("merkle_root")
    return block
