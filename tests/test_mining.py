"""Unit tests for candidate block construction and mining flow."""

from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from minichain.chain import ChainConfig, ChainManager
from minichain.consensus import MAX_TARGET, is_valid_pow
from minichain.crypto import derive_address, generate_key_pair
from minichain.genesis import GenesisConfig, create_genesis_state
from minichain.mempool import Mempool
from minichain.mining import build_candidate_block, mine_candidate_block
from minichain.transaction import Transaction


def _build_manager(
    *,
    initial_balances: dict[str, int],
    genesis_target: int = MAX_TARGET,
) -> ChainManager:
    genesis_block, genesis_state = create_genesis_state(
        GenesisConfig(
            initial_balances=initial_balances,
            timestamp=1_739_000_000,
            difficulty_target=genesis_target,
        )
    )
    return ChainManager(
        genesis_block=genesis_block,
        genesis_state=genesis_state,
        config=ChainConfig(
            block_reward=50,
            difficulty_adjustment_interval=10,
            target_block_time_seconds=30,
        ),
    )


def _signed_transaction(
    *,
    sender_key: object,
    sender_address: str,
    recipient: str,
    amount: int,
    nonce: int,
    fee: int,
    timestamp: int,
) -> Transaction:
    tx = Transaction(
        sender=sender_address,
        recipient=recipient,
        amount=amount,
        nonce=nonce,
        fee=fee,
        timestamp=timestamp,
    )
    tx.sign(sender_key)
    return tx


def test_candidate_block_selects_by_fee_with_sender_nonce_ordering() -> None:
    a_key, a_verify = generate_key_pair()
    b_key, b_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender_a = derive_address(a_verify)
    sender_b = derive_address(b_verify)
    recipient = derive_address(recipient_verify)

    manager = _build_manager(initial_balances={sender_a: 100, sender_b: 100})
    mempool = Mempool()

    tx_a1 = _signed_transaction(
        sender_key=a_key,
        sender_address=sender_a,
        recipient=recipient,
        amount=10,
        nonce=1,
        fee=10,
        timestamp=1_739_000_010,
    )
    tx_b0 = _signed_transaction(
        sender_key=b_key,
        sender_address=sender_b,
        recipient=recipient,
        amount=10,
        nonce=0,
        fee=8,
        timestamp=1_739_000_011,
    )
    tx_a0 = _signed_transaction(
        sender_key=a_key,
        sender_address=sender_a,
        recipient=recipient,
        amount=10,
        nonce=0,
        fee=1,
        timestamp=1_739_000_012,
    )

    mempool.add_transaction(tx_a1, manager.state)
    mempool.add_transaction(tx_b0, manager.state)
    mempool.add_transaction(tx_a0, manager.state)

    candidate = build_candidate_block(
        chain_manager=manager,
        mempool=mempool,
        miner_address="11" * 20,
        max_transactions=3,
        timestamp=1_739_000_030,
    )

    assert candidate.header.previous_hash == manager.tip_hash
    assert candidate.header.block_height == manager.height + 1
    assert candidate.header.difficulty_target == manager.expected_next_difficulty()
    assert candidate.transactions[0].is_coinbase()
    assert [tx.fee for tx in candidate.transactions[1:]] == [8, 1, 10]
    assert [tx.nonce for tx in candidate.transactions[1:] if tx.sender == sender_a] == [0, 1]

    total_fees = sum(tx.fee for tx in candidate.transactions[1:])
    assert candidate.transactions[0].amount == manager.config.block_reward + total_fees
    assert candidate.has_valid_merkle_root()


def test_candidate_block_respects_max_transaction_limit() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)
    manager = _build_manager(initial_balances={sender: 200})
    mempool = Mempool()

    tx0 = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=1,
        timestamp=1_739_000_010,
    )
    tx1 = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=5,
        nonce=1,
        fee=2,
        timestamp=1_739_000_011,
    )
    mempool.add_transaction(tx0, manager.state)
    mempool.add_transaction(tx1, manager.state)

    candidate = build_candidate_block(
        chain_manager=manager,
        mempool=mempool,
        miner_address="22" * 20,
        max_transactions=1,
        timestamp=1_739_000_030,
    )

    assert len(candidate.transactions) == 2
    assert candidate.transactions[1].nonce == 0
    assert candidate.transactions[0].amount == manager.config.block_reward + tx0.fee


def test_mined_candidate_block_is_accepted_by_chain_manager() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)
    manager = _build_manager(initial_balances={sender: 100}, genesis_target=1 << 252)
    mempool = Mempool()

    tx = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=10,
        nonce=0,
        fee=2,
        timestamp=1_739_000_010,
    )
    mempool.add_transaction(tx, manager.state)

    candidate = build_candidate_block(
        chain_manager=manager,
        mempool=mempool,
        miner_address="33" * 20,
        max_transactions=10,
        timestamp=1_739_000_030,
    )
    mined_block, _digest = mine_candidate_block(
        block_template=candidate,
        max_nonce=500_000,
    )

    assert is_valid_pow(mined_block.header)
    result = manager.add_block(mined_block)
    assert result == "extended"
    assert manager.height == 1
    assert manager.state.get_account("33" * 20).balance == manager.config.block_reward + tx.fee


def test_candidate_block_timestamp_is_monotonic() -> None:
    manager = _build_manager(initial_balances={})
    mempool = Mempool()
    candidate = build_candidate_block(
        chain_manager=manager,
        mempool=mempool,
        miner_address="44" * 20,
        max_transactions=0,
        timestamp=manager.tip_block.header.timestamp - 10,
    )

    assert candidate.header.timestamp == manager.tip_block.header.timestamp + 1
