"""Block construction utilities for miners."""

from __future__ import annotations

import time
from dataclasses import replace
from threading import Event

from minichain.block import Block, BlockHeader
from minichain.chain import ChainManager
from minichain.consensus import mine_block_header
from minichain.mempool import Mempool
from minichain.transaction import ADDRESS_HEX_LENGTH, create_coinbase_transaction


class BlockConstructionError(ValueError):
    """Raised when a candidate block cannot be constructed."""


def build_candidate_block(
    *,
    chain_manager: ChainManager,
    mempool: Mempool,
    miner_address: str,
    max_transactions: int,
    timestamp: int | None = None,
) -> Block:
    """Build a candidate block template from chain tip and mempool."""
    if max_transactions < 0:
        raise BlockConstructionError("max_transactions must be non-negative")
    if not _is_lower_hex(miner_address, ADDRESS_HEX_LENGTH):
        raise BlockConstructionError("miner_address must be a 20-byte lowercase hex string")

    parent = chain_manager.tip_block
    parent_hash = chain_manager.tip_hash
    base_timestamp = int(time.time()) if timestamp is None else timestamp
    if base_timestamp < 0:
        raise BlockConstructionError("timestamp must be non-negative")
    block_timestamp = max(base_timestamp, parent.header.timestamp + 1)

    selected_transactions = mempool.get_transactions_for_mining(
        chain_manager.state,
        limit=max_transactions,
        current_time=block_timestamp,
    )
    total_fees = sum(transaction.fee for transaction in selected_transactions)
    coinbase_amount = chain_manager.config.block_reward + total_fees
    coinbase = create_coinbase_transaction(
        miner_address=miner_address,
        amount=coinbase_amount,
        timestamp=block_timestamp,
    )

    header = BlockHeader(
        version=parent.header.version,
        previous_hash=parent_hash,
        merkle_root="",
        timestamp=block_timestamp,
        difficulty_target=chain_manager.expected_next_difficulty(parent_hash=parent_hash),
        nonce=0,
        block_height=parent.header.block_height + 1,
    )
    candidate = Block(header=header, transactions=[coinbase, *selected_transactions])
    candidate.update_header_merkle_root()
    return candidate


def mine_candidate_block(
    *,
    block_template: Block,
    start_nonce: int = 0,
    max_nonce: int = (1 << 64) - 1,
    stop_event: Event | None = None,
) -> tuple[Block, bytes]:
    """Search for a valid nonce and return a mined copy of the block."""
    nonce, digest = mine_block_header(
        block_template.header,
        start_nonce=start_nonce,
        max_nonce=max_nonce,
        stop_event=stop_event,
    )
    mined_header = replace(block_template.header, nonce=nonce)
    mined_block = Block(header=mined_header, transactions=list(block_template.transactions))
    return mined_block, digest


def _is_lower_hex(value: str, expected_length: int) -> bool:
    if len(value) != expected_length:
        return False
    return all(ch in "0123456789abcdef" for ch in value)
