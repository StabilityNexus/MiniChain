"""Integration tests for transaction gossip propagation."""

from __future__ import annotations

import asyncio

from minichain.crypto import derive_address, generate_key_pair
from minichain.network import MiniChainNetwork, NetworkConfig
from minichain.transaction import Transaction


def test_transaction_gossip_propagates_across_three_nodes() -> None:
    async def scenario() -> None:
        seen_by_node: dict[str, list[str]] = {"a": [], "b": [], "c": []}

        def make_handler(node_name: str):
            def handler(transaction: Transaction) -> bool:
                seen_by_node[node_name].append(transaction.transaction_id().hex())
                return True

            return handler

        node_b = MiniChainNetwork(
            NetworkConfig(host="127.0.0.1", port=0, node_id="node-b", enable_mdns=False)
        )
        node_b.set_transaction_handler(make_handler("b"))
        await node_b.start()

        node_a = MiniChainNetwork(
            NetworkConfig(
                host="127.0.0.1",
                port=0,
                node_id="node-a",
                enable_mdns=False,
                bootstrap_peers=(node_b.listen_address(),),
            )
        )
        node_a.set_transaction_handler(make_handler("a"))
        await node_a.start()

        node_c = MiniChainNetwork(
            NetworkConfig(
                host="127.0.0.1",
                port=0,
                node_id="node-c",
                enable_mdns=False,
                bootstrap_peers=(node_b.listen_address(),),
            )
        )
        node_c.set_transaction_handler(make_handler("c"))
        await node_c.start()

        try:
            await node_a.wait_for_connected_peers(1, timeout=3.0)
            await node_b.wait_for_connected_peers(2, timeout=3.0)
            await node_c.wait_for_connected_peers(1, timeout=3.0)

            transaction = _build_signed_transaction()
            assert await node_a.submit_transaction(transaction)

            await _wait_until(
                lambda: len(seen_by_node["b"]) == 1 and len(seen_by_node["c"]) == 1,
                timeout=3.0,
            )
            assert len(seen_by_node["a"]) == 1
            assert len(seen_by_node["b"]) == 1
            assert len(seen_by_node["c"]) == 1

            assert not await node_a.submit_transaction(transaction)
            await asyncio.sleep(0.2)
            assert len(seen_by_node["a"]) == 1
            assert len(seen_by_node["b"]) == 1
            assert len(seen_by_node["c"]) == 1
        finally:
            await node_c.stop()
            await node_a.stop()
            await node_b.stop()

    asyncio.run(scenario())


def _build_signed_transaction() -> Transaction:
    signing_key, verify_key = generate_key_pair()
    sender = derive_address(verify_key)
    transaction = Transaction(
        sender=sender,
        recipient="11" * 20,
        amount=25,
        nonce=0,
        fee=1,
        timestamp=1_700_000_000,
    )
    transaction.sign(signing_key)
    return transaction


async def _wait_until(predicate, *, timeout: float) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise TimeoutError("condition was not met before timeout")
