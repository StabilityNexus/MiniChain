"""Integration tests for block propagation across peers."""

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


def test_block_gossip_propagates_and_applies_on_three_nodes() -> None:
    async def scenario() -> None:
        manager_a = _build_manager()
        manager_b = _build_manager()
        manager_c = _build_manager()

        accepted_hashes: dict[str, list[str]] = {"a": [], "b": [], "c": []}

        def make_block_handler(manager: ChainManager, node_name: str):
            def handler(block) -> bool:
                try:
                    result = manager.add_block(block)
                except ChainValidationError:
                    return False
                if result in {"extended", "reorg"}:
                    accepted_hashes[node_name].append(block.hash().hex())
                    return True
                return False

            return handler

        node_b = MiniChainNetwork(
            NetworkConfig(host="127.0.0.1", port=0, node_id="node-block-b", enable_mdns=False)
        )
        node_b.set_block_handler(make_block_handler(manager_b, "b"))
        await node_b.start()

        node_a = MiniChainNetwork(
            NetworkConfig(
                host="127.0.0.1",
                port=0,
                node_id="node-block-a",
                enable_mdns=False,
                bootstrap_peers=(node_b.listen_address(),),
            )
        )
        node_a.set_block_handler(make_block_handler(manager_a, "a"))
        await node_a.start()

        node_c = MiniChainNetwork(
            NetworkConfig(
                host="127.0.0.1",
                port=0,
                node_id="node-block-c",
                enable_mdns=False,
                bootstrap_peers=(node_b.listen_address(),),
            )
        )
        node_c.set_block_handler(make_block_handler(manager_c, "c"))
        await node_c.start()

        try:
            await node_a.wait_for_connected_peers(1, timeout=3.0)
            await node_b.wait_for_connected_peers(2, timeout=3.0)
            await node_c.wait_for_connected_peers(1, timeout=3.0)

            candidate = build_candidate_block(
                chain_manager=manager_a,
                mempool=Mempool(),
                miner_address="11" * 20,
                max_transactions=0,
                timestamp=1_739_000_030,
            )
            mined_block, _digest = mine_candidate_block(
                block_template=candidate,
                max_nonce=100_000,
            )

            assert await node_a.submit_block(mined_block)

            await _wait_until(
                lambda: manager_b.height == 1 and manager_c.height == 1,
                timeout=3.0,
            )

            expected_tip = mined_block.hash().hex()
            assert manager_a.height == 1
            assert manager_b.height == 1
            assert manager_c.height == 1
            assert manager_a.tip_hash == expected_tip
            assert manager_b.tip_hash == expected_tip
            assert manager_c.tip_hash == expected_tip

            assert len(accepted_hashes["a"]) == 1
            assert len(accepted_hashes["b"]) == 1
            assert len(accepted_hashes["c"]) == 1

            assert not await node_a.submit_block(mined_block)
            await asyncio.sleep(0.2)
            assert len(accepted_hashes["a"]) == 1
            assert len(accepted_hashes["b"]) == 1
            assert len(accepted_hashes["c"]) == 1
        finally:
            await node_c.stop()
            await node_a.stop()
            await node_b.stop()

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


async def _wait_until(predicate, *, timeout: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("condition was not met before timeout")
