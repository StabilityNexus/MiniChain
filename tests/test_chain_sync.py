"""Integration tests for `/minichain/sync/1.0.0` chain synchronization."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("nacl")

from minichain.chain import ChainConfig, ChainManager, ChainValidationError
from minichain.consensus import MAX_TARGET
from minichain.genesis import GenesisConfig, create_genesis_state
from minichain.mempool import Mempool
from minichain.mining import build_candidate_block, mine_candidate_block
from minichain.network import MiniChainNetwork, NetworkConfig


def test_chain_sync_catches_up_shorter_peer() -> None:
    async def scenario() -> None:
        source_manager = _build_manager()
        target_manager = _build_manager()
        _mine_blocks(source_manager, count=5)

        source_node = MiniChainNetwork(
            NetworkConfig(
                host="127.0.0.1",
                port=0,
                node_id="node-sync-source",
                enable_mdns=False,
                sync_batch_size=2,
            )
        )
        source_node.set_sync_handlers(
            get_height=lambda: source_manager.height,
            get_block_by_height=source_manager.get_canonical_block_by_height,
            apply_block=lambda _block: True,
        )
        await source_node.start()

        target_node = MiniChainNetwork(
            NetworkConfig(
                host="127.0.0.1",
                port=0,
                node_id="node-sync-target",
                enable_mdns=False,
                sync_batch_size=2,
                bootstrap_peers=(source_node.listen_address(),),
            )
        )
        target_node.set_sync_handlers(
            get_height=lambda: target_manager.height,
            get_block_by_height=target_manager.get_canonical_block_by_height,
            apply_block=lambda block: _apply_block(target_manager, block),
        )
        await target_node.start()

        try:
            await source_node.wait_for_connected_peers(1, timeout=3.0)
            await target_node.wait_for_connected_peers(1, timeout=3.0)

            await target_node.wait_for_height(source_manager.height, timeout=5.0)
            assert target_manager.height == source_manager.height
            assert target_manager.tip_hash == source_manager.tip_hash
        finally:
            await target_node.stop()
            await source_node.stop()

    asyncio.run(scenario())


def _build_manager() -> ChainManager:
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
            block_reward=50,
            difficulty_adjustment_interval=10,
            target_block_time_seconds=30,
        ),
    )


def _mine_blocks(manager: ChainManager, *, count: int) -> None:
    for _ in range(count):
        timestamp = manager.tip_block.header.timestamp + 30
        candidate = build_candidate_block(
            chain_manager=manager,
            mempool=Mempool(),
            miner_address="aa" * 20,
            max_transactions=0,
            timestamp=timestamp,
        )
        block, _digest = mine_candidate_block(block_template=candidate, max_nonce=0)
        result = manager.add_block(block)
        assert result == "extended"


def _apply_block(manager: ChainManager, block) -> bool:
    try:
        result = manager.add_block(block)
    except ChainValidationError:
        return False
    return result in {"extended", "reorg", "duplicate"}
