"""Unit tests for genesis block and state initialization."""

from __future__ import annotations

from dataclasses import replace

from minichain.crypto import blake2b_digest
from minichain.genesis import (
    GENESIS_PREVIOUS_HASH,
    GenesisConfig,
    apply_genesis_block,
    create_genesis_block,
    create_genesis_state,
)
from minichain.state import Account, State


def test_create_genesis_block_uses_conventional_fields() -> None:
    config = GenesisConfig(
        initial_balances={"11" * 20: 1_000_000},
        timestamp=1_739_123_456,
        difficulty_target=123_456,
        version=0,
    )

    block = create_genesis_block(config)

    assert block.header.block_height == 0
    assert block.header.previous_hash == GENESIS_PREVIOUS_HASH
    assert block.header.timestamp == config.timestamp
    assert block.header.difficulty_target == config.difficulty_target
    assert block.header.nonce == 0
    assert block.header.merkle_root == blake2b_digest(b"").hex()
    assert block.transactions == []


def test_apply_genesis_block_initializes_expected_balances() -> None:
    balances = {"aa" * 20: 500, "bb" * 20: 300}
    config = GenesisConfig(initial_balances=balances)
    block = create_genesis_block(config)
    state = State()

    apply_genesis_block(state, block, config)

    assert state.get_account("aa" * 20).balance == 500
    assert state.get_account("aa" * 20).nonce == 0
    assert state.get_account("bb" * 20).balance == 300
    assert state.get_account("bb" * 20).nonce == 0


def test_create_genesis_state_builds_block_and_state() -> None:
    config = GenesisConfig(initial_balances={"cc" * 20: 42})

    block, state = create_genesis_state(config)

    assert block.header.block_height == 0
    assert state.get_account("cc" * 20).balance == 42


def test_genesis_requires_empty_state() -> None:
    config = GenesisConfig(initial_balances={"dd" * 20: 1})
    block = create_genesis_block(config)
    state = State()
    state.set_account("ff" * 20, Account(balance=1, nonce=0))

    try:
        apply_genesis_block(state, block, config)
    except ValueError as exc:
        assert "empty state" in str(exc)
    else:
        raise AssertionError("Expected ValueError for non-empty state")


def test_genesis_block_rejects_wrong_previous_hash() -> None:
    config = GenesisConfig(initial_balances={"ee" * 20: 10})
    block = create_genesis_block(config)
    block.header = replace(block.header, previous_hash="11" * 32)
    state = State()

    try:
        apply_genesis_block(state, block, config)
    except ValueError as exc:
        assert "previous_hash" in str(exc)
    else:
        raise AssertionError("Expected ValueError for invalid previous_hash")
