"""Integration-style tests for node orchestration and persistence."""

from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from minichain.chain import ChainConfig
from minichain.crypto import derive_address, generate_key_pair
from minichain.genesis import GenesisConfig
from minichain.mining import build_candidate_block, mine_candidate_block
from minichain.node import MiniChainNode, NodeConfig
from minichain.storage import SQLiteStorage
from minichain.transaction import Transaction


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


def test_node_start_initializes_and_persists_genesis(
    tmp_path: pytest.TempPathFactory,
) -> None:
    config = NodeConfig(
        data_dir=tmp_path / "node-data",
        genesis_config=GenesisConfig(
            initial_balances={"aa" * 20: 123},
            timestamp=1_739_000_000,
            difficulty_target=(1 << 255) - 1,
        ),
        chain_config=ChainConfig(block_reward=50),
    )
    node = MiniChainNode(config)
    node.start()
    try:
        assert node.running
        assert node.height == 0
        assert node.chain_manager.state.get_account("aa" * 20).balance == 123
        metadata = node.storage.load_chain_metadata()
        assert metadata is not None
        assert metadata["height"] == 0
        assert metadata["head_hash"] == node.tip_hash
    finally:
        node.stop()

    with SQLiteStorage((tmp_path / "node-data") / "chain.sqlite3") as storage:
        assert storage.get_block_by_height(0) is not None
        persisted_meta = storage.load_chain_metadata()
        assert persisted_meta is not None
        assert persisted_meta["height"] == 0


def test_node_mine_block_then_reload_from_disk(
    tmp_path: pytest.TempPathFactory,
) -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)
    miner = "11" * 20

    config = NodeConfig(
        data_dir=tmp_path / "node-data",
        miner_address=miner,
        genesis_config=GenesisConfig(
            initial_balances={sender: 200},
            timestamp=1_739_000_000,
            difficulty_target=(1 << 255) - 1,
        ),
        chain_config=ChainConfig(block_reward=50),
    )

    node = MiniChainNode(config)
    node.start()
    try:
        tx = _signed_transaction(
            sender_key=sender_key,
            sender_address=sender,
            recipient=recipient,
            amount=25,
            nonce=0,
            fee=3,
            timestamp=1_739_000_010,
        )
        node.submit_transaction(tx)
        node.mine_one_block(max_nonce=500_000, timestamp=1_739_000_030)

        assert node.height == 1
        assert node.chain_manager.state.get_account(sender).balance == 172
        assert node.chain_manager.state.get_account(recipient).balance == 25
        assert node.chain_manager.state.get_account(miner).balance == 53
    finally:
        node.stop()

    restarted = MiniChainNode(config)
    restarted.start()
    try:
        assert restarted.height == 1
        assert restarted.chain_manager.state.get_account(sender).balance == 172
        assert restarted.chain_manager.state.get_account(recipient).balance == 25
        assert restarted.chain_manager.state.get_account(miner).balance == 53
    finally:
        restarted.stop()


def test_accept_block_persists_chain_head(
    tmp_path: pytest.TempPathFactory,
) -> None:
    miner = "22" * 20
    config = NodeConfig(
        data_dir=tmp_path / "node-data",
        miner_address=miner,
        genesis_config=GenesisConfig(
            initial_balances={},
            timestamp=1_739_000_000,
            difficulty_target=(1 << 255) - 1,
        ),
        chain_config=ChainConfig(block_reward=50),
    )
    node = MiniChainNode(config)
    node.start()
    try:
        candidate = build_candidate_block(
            chain_manager=node.chain_manager,
            mempool=node.mempool,
            miner_address=miner,
            max_transactions=0,
            timestamp=1_739_000_030,
        )
        mined_block, _digest = mine_candidate_block(block_template=candidate, max_nonce=500_000)
        result = node.accept_block(mined_block)
        assert result == "extended"
        assert node.height == 1
    finally:
        node.stop()

    reopened = MiniChainNode(config)
    reopened.start()
    try:
        assert reopened.height == 1
        assert reopened.chain_manager.state.get_account(miner).balance == 50
    finally:
        reopened.stop()
