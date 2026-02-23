"""Unit tests for block hashing and transaction commitments."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("nacl")

from minichain.block import Block, BlockHeader, BlockValidationError
from minichain.crypto import derive_address, generate_key_pair
from minichain.transaction import Transaction, create_coinbase_transaction


def _make_signed_transaction(amount: int, nonce: int) -> Transaction:
    signing_key, verify_key = generate_key_pair()
    tx = Transaction(
        sender=derive_address(verify_key),
        recipient="ab" * 20,
        amount=amount,
        nonce=nonce,
        fee=1,
        timestamp=1_739_800_000 + nonce,
    )
    tx.sign(signing_key)
    return tx


def _make_block() -> Block:
    transactions = [
        _make_signed_transaction(amount=10, nonce=0),
        _make_signed_transaction(amount=11, nonce=1),
    ]
    header = BlockHeader(
        version=0,
        previous_hash="00" * 32,
        merkle_root="",
        timestamp=1_739_800_111,
        difficulty_target=1_000_000,
        nonce=7,
        block_height=1,
    )
    block = Block(header=header, transactions=transactions)
    block.update_header_merkle_root()
    return block


def _make_block_with_coinbase(*, block_reward: int = 50) -> Block:
    miner_key, miner_verify = generate_key_pair()
    _ = miner_key
    regular_transactions = [
        _make_signed_transaction(amount=10, nonce=0),
        _make_signed_transaction(amount=11, nonce=1),
    ]
    coinbase = create_coinbase_transaction(
        miner_address=derive_address(miner_verify),
        amount=block_reward + sum(tx.fee for tx in regular_transactions),
        timestamp=1_739_800_111,
    )
    header = BlockHeader(
        version=0,
        previous_hash="00" * 32,
        merkle_root="",
        timestamp=1_739_800_111,
        difficulty_target=1_000_000,
        nonce=7,
        block_height=1,
    )
    block = Block(header=header, transactions=[coinbase, *regular_transactions])
    block.update_header_merkle_root()
    return block


def test_block_hash_is_deterministic() -> None:
    block = _make_block()
    assert block.hash() == block.hash()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 1),
        ("previous_hash", "11" * 32),
        ("merkle_root", "22" * 32),
        ("timestamp", 1_739_800_222),
        ("difficulty_target", 2_000_000),
        ("nonce", 8),
        ("block_height", 2),
    ],
)
def test_changing_header_field_changes_hash(field: str, value: int | str) -> None:
    block = _make_block()
    mutated_header = replace(block.header, **{field: value})

    assert block.header.hash() != mutated_header.hash()


def test_header_merkle_root_matches_transaction_body() -> None:
    block = _make_block()
    assert block.has_valid_merkle_root()

    block.transactions[0].amount += 1
    assert not block.has_valid_merkle_root()


def test_validate_coinbase_accepts_correct_amount() -> None:
    block = _make_block_with_coinbase(block_reward=50)
    block.validate_coinbase(block_reward=50)


def test_validate_coinbase_rejects_wrong_amount() -> None:
    block = _make_block_with_coinbase(block_reward=50)
    block.transactions[0].amount += 1
    block.update_header_merkle_root()

    with pytest.raises(BlockValidationError, match="Invalid coinbase amount"):
        block.validate_coinbase(block_reward=50)
