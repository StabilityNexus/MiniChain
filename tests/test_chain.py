"""Unit tests for chain management and fork resolution."""

from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from minichain.block import Block, BlockHeader
from minichain.chain import ChainConfig, ChainManager, ChainValidationError
from minichain.consensus import MAX_TARGET
from minichain.genesis import GenesisConfig, create_genesis_state
from minichain.transaction import create_coinbase_transaction


def _build_manager(*, block_reward: int = 50) -> ChainManager:
    genesis_block, genesis_state = create_genesis_state(
        GenesisConfig(
            initial_balances={},
            timestamp=1_739_000_000,
            difficulty_target=MAX_TARGET,
        )
    )
    return ChainManager(
        genesis_block=genesis_block,
        genesis_state=genesis_state,
        config=ChainConfig(
            block_reward=block_reward,
            difficulty_adjustment_interval=10,
            target_block_time_seconds=30,
        ),
    )


def _coinbase_block(
    manager: ChainManager,
    *,
    parent: Block,
    miner_address: str,
    timestamp: int,
    coinbase_amount: int | None = None,
    difficulty_target: int | None = None,
) -> Block:
    reward_amount = manager.config.block_reward if coinbase_amount is None else coinbase_amount
    target = (
        manager.expected_next_difficulty(parent_hash=parent.hash().hex())
        if difficulty_target is None
        else difficulty_target
    )
    coinbase = create_coinbase_transaction(
        miner_address=miner_address,
        amount=reward_amount,
        timestamp=timestamp,
    )
    header = BlockHeader(
        version=0,
        previous_hash=parent.hash().hex(),
        merkle_root="",
        timestamp=timestamp,
        difficulty_target=target,
        nonce=0,
        block_height=parent.header.block_height + 1,
    )
    block = Block(header=header, transactions=[coinbase])
    block.update_header_merkle_root()
    return block


def test_appends_valid_blocks_to_tip() -> None:
    manager = _build_manager(block_reward=50)
    miner = "11" * 20

    block_1 = _coinbase_block(
        manager,
        parent=manager.tip_block,
        miner_address=miner,
        timestamp=1_739_000_030,
    )
    result_1 = manager.add_block(block_1)
    assert result_1 == "extended"
    assert manager.height == 1

    block_2 = _coinbase_block(
        manager,
        parent=manager.tip_block,
        miner_address=miner,
        timestamp=1_739_000_060,
    )
    result_2 = manager.add_block(block_2)
    assert result_2 == "extended"
    assert manager.height == 2
    assert manager.tip_hash == block_2.hash().hex()
    assert manager.state.get_account(miner).balance == 100


def test_longer_fork_triggers_reorg_and_state_replay() -> None:
    manager = _build_manager(block_reward=50)
    miner_a = "11" * 20
    miner_b = "22" * 20

    a1 = _coinbase_block(
        manager,
        parent=manager.tip_block,
        miner_address=miner_a,
        timestamp=1_739_000_030,
    )
    manager.add_block(a1)

    a2 = _coinbase_block(
        manager,
        parent=a1,
        miner_address=miner_a,
        timestamp=1_739_000_060,
    )
    assert manager.add_block(a2) == "extended"
    assert manager.state.get_account(miner_a).balance == 100

    b2 = _coinbase_block(
        manager,
        parent=a1,
        miner_address=miner_b,
        timestamp=1_739_000_061,
    )
    assert manager.add_block(b2) == "stored_fork"

    b3 = _coinbase_block(
        manager,
        parent=b2,
        miner_address=miner_b,
        timestamp=1_739_000_090,
    )
    assert manager.add_block(b3) == "reorg"
    assert manager.tip_hash == b3.hash().hex()
    assert manager.height == 3
    assert manager.state.get_account(miner_a).balance == 50
    assert manager.state.get_account(miner_b).balance == 100


def test_rejects_block_with_unknown_parent() -> None:
    manager = _build_manager(block_reward=50)
    coinbase = create_coinbase_transaction(
        miner_address="33" * 20,
        amount=50,
        timestamp=1_739_000_030,
    )
    block = Block(
        header=BlockHeader(
            version=0,
            previous_hash="ff" * 32,
            merkle_root="",
            timestamp=1_739_000_030,
            difficulty_target=MAX_TARGET,
            nonce=0,
            block_height=1,
        ),
        transactions=[coinbase],
    )
    block.update_header_merkle_root()

    with pytest.raises(ChainValidationError, match="Unknown parent block"):
        manager.add_block(block)


def test_rejects_invalid_coinbase_amount() -> None:
    manager = _build_manager(block_reward=50)

    invalid_block = _coinbase_block(
        manager,
        parent=manager.tip_block,
        miner_address="44" * 20,
        timestamp=1_739_000_030,
        coinbase_amount=60,
    )
    invalid_hash = invalid_block.hash().hex()
    with pytest.raises(ChainValidationError, match="State transition failed"):
        manager.add_block(invalid_block)

    assert manager.height == 0
    assert not manager.contains_block(invalid_hash)


def test_rejects_block_with_wrong_difficulty_target() -> None:
    manager = _build_manager(block_reward=50)
    expected = manager.expected_next_difficulty(parent_hash=manager.tip_hash)
    wrong_target = expected - 1

    invalid_block = _coinbase_block(
        manager,
        parent=manager.tip_block,
        miner_address="55" * 20,
        timestamp=1_739_000_030,
        difficulty_target=wrong_target,
    )

    with pytest.raises(ChainValidationError, match="Invalid difficulty target"):
        manager.add_block(invalid_block)
