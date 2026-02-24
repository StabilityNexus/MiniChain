"""Comprehensive multi-node integration scenarios for v0."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

pytest.importorskip("nacl")

from minichain.block import Block
from minichain.chain import ChainConfig, ChainManager, ChainValidationError
from minichain.consensus import MAX_TARGET
from minichain.genesis import GenesisConfig, create_genesis_state
from minichain.mempool import Mempool, MempoolValidationError
from minichain.mining import build_candidate_block, mine_candidate_block
from minichain.network import MiniChainNetwork, NetworkConfig, PeerAddress
from minichain.transaction import Transaction


@dataclass
class _IntegratedNode:
    manager: ChainManager
    mempool: Mempool
    network: MiniChainNetwork


def test_three_node_network_mining_converges() -> None:
    async def scenario() -> None:
        node_b = _build_node(node_id="node-int-b", bootstrap_peers=())
        await node_b.network.start()

        node_a = _build_node(
            node_id="node-int-a",
            bootstrap_peers=(node_b.network.listen_address(),),
        )
        node_c = _build_node(
            node_id="node-int-c",
            bootstrap_peers=(node_b.network.listen_address(),),
        )
        await node_a.network.start()
        await node_c.network.start()

        nodes = [node_a, node_b, node_c]
        try:
            await node_a.network.wait_for_connected_peers(1, timeout=3.0)
            await node_b.network.wait_for_connected_peers(2, timeout=3.0)
            await node_c.network.wait_for_connected_peers(1, timeout=3.0)

            miners = ["11" * 20, "22" * 20, "33" * 20]

            for expected_height, (mining_node, miner_address) in enumerate(
                zip(nodes, miners),
                start=1,
            ):
                await _mine_and_broadcast(mining_node, miner_address)
                await _wait_until(
                    lambda: all(node.manager.height >= expected_height for node in nodes),
                    timeout=5.0,
                )
                await _wait_until(
                    lambda: len({node.manager.tip_hash for node in nodes}) == 1
                    and all(node.manager.height == expected_height for node in nodes),
                    timeout=5.0,
                )

            assert all(node.manager.height == 3 for node in nodes)
            assert len({node.manager.tip_hash for node in nodes}) == 1
        finally:
            await node_c.network.stop()
            await node_a.network.stop()
            await node_b.network.stop()

    asyncio.run(scenario())


def test_competing_blocks_trigger_fork_then_reorg_convergence() -> None:
    async def scenario() -> None:
        node_a = _build_node(node_id="node-fork-a", bootstrap_peers=())
        await node_a.network.start()
        node_b = _build_node(
            node_id="node-fork-b",
            bootstrap_peers=(node_a.network.listen_address(),),
        )
        await node_b.network.start()

        try:
            await node_a.network.wait_for_connected_peers(1, timeout=3.0)
            await node_b.network.wait_for_connected_peers(1, timeout=3.0)

            timestamp = node_a.manager.tip_block.header.timestamp + 30
            block_a = _build_mined_block(node_a, miner_address="44" * 20, timestamp=timestamp)
            block_b = _build_mined_block(node_b, miner_address="55" * 20, timestamp=timestamp)

            await asyncio.gather(
                _apply_and_broadcast_block(node_a, block_a),
                _apply_and_broadcast_block(node_b, block_b),
            )

            await _wait_until(
                lambda: node_a.manager.height == 1 and node_b.manager.height == 1,
                timeout=3.0,
            )
            assert node_a.manager.tip_hash != node_b.manager.tip_hash

            resolved = await _mine_and_broadcast(node_a, "44" * 20)
            await _wait_until(
                lambda: node_a.manager.height == 2 and node_b.manager.height == 2,
                timeout=5.0,
            )
            assert node_a.manager.tip_hash == resolved.hash().hex()
            assert node_b.manager.tip_hash == resolved.hash().hex()
        finally:
            await node_b.network.stop()
            await node_a.network.stop()

    asyncio.run(scenario())


def _build_node(
    *,
    node_id: str,
    bootstrap_peers: tuple[PeerAddress, ...],
    initial_balances: dict[str, int] | None = None,
) -> _IntegratedNode:
    manager = _build_manager(initial_balances=initial_balances or {})
    mempool = Mempool(max_size=200, max_age_seconds=3_600)
    network = MiniChainNetwork(
        NetworkConfig(
            host="127.0.0.1",
            port=0,
            node_id=node_id,
            enable_mdns=False,
            bootstrap_peers=bootstrap_peers,
            sync_batch_size=4,
        )
    )
    node = _IntegratedNode(manager=manager, mempool=mempool, network=network)

    network.set_transaction_handler(lambda transaction: _accept_transaction(node, transaction))
    network.set_block_handler(lambda block: _apply_block(node, block))
    network.set_sync_handlers(
        get_height=lambda: node.manager.height,
        get_block_by_height=node.manager.get_canonical_block_by_height,
        apply_block=lambda block: _apply_block(node, block),
    )
    return node


def _build_manager(*, initial_balances: dict[str, int]) -> ChainManager:
    genesis_block, genesis_state = create_genesis_state(
        GenesisConfig(
            initial_balances=initial_balances,
            timestamp=1_739_000_000,
            difficulty_target=MAX_TARGET,
        )
    )
    return ChainManager(
        genesis_block=genesis_block,
        genesis_state=genesis_state,
        config=ChainConfig(
            block_reward=50,
            difficulty_adjustment_interval=1_000_000,
            target_block_time_seconds=30,
        ),
    )


def _build_mined_block(
    node: _IntegratedNode,
    *,
    miner_address: str,
    timestamp: int | None = None,
) -> Block:
    candidate = build_candidate_block(
        chain_manager=node.manager,
        mempool=node.mempool,
        miner_address=miner_address,
        max_transactions=500,
        timestamp=timestamp,
    )
    block, _digest = mine_candidate_block(block_template=candidate, max_nonce=0)
    return block


async def _mine_and_broadcast(node: _IntegratedNode, miner_address: str) -> Block:
    next_timestamp = node.manager.tip_block.header.timestamp + 30
    block = _build_mined_block(
        node,
        miner_address=miner_address,
        timestamp=next_timestamp,
    )
    await _apply_and_broadcast_block(node, block)
    return block


async def _apply_and_broadcast_block(node: _IntegratedNode, block: Block) -> None:
    assert _apply_block(node, block)
    sent = await node.network.submit_block(block)
    assert sent


def _apply_block(node: _IntegratedNode, block: Block) -> bool:
    try:
        result = node.manager.add_block(block)
    except ChainValidationError:
        return False

    if result in {"extended", "reorg"}:
        node.mempool.remove_confirmed_transactions(block.transactions, node.manager.state)
    return result in {"extended", "reorg", "stored_fork", "duplicate"}


def _accept_transaction(node: _IntegratedNode, transaction: Transaction) -> bool:
    try:
        node.mempool.add_transaction(transaction, node.manager.state)
    except MempoolValidationError:
        return False
    return True


async def _wait_until(predicate, *, timeout: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("condition was not met before timeout")
