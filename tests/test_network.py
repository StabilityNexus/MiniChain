"""Integration tests for peer networking and discovery."""

from __future__ import annotations

import asyncio
import socket

from minichain.network import MiniChainNetwork, NetworkConfig, PeerAddress


def test_bootstrap_peer_discovery() -> None:
    async def scenario() -> None:
        node_a = MiniChainNetwork(
            NetworkConfig(
                host="127.0.0.1",
                port=0,
                node_id="node-a",
                enable_mdns=False,
            )
        )
        await node_a.start()

        node_b = MiniChainNetwork(
            NetworkConfig(
                host="127.0.0.1",
                port=0,
                node_id="node-b",
                enable_mdns=False,
                bootstrap_peers=(node_a.listen_address(),),
            )
        )
        await node_b.start()

        try:
            await node_a.wait_for_connected_peers(1, timeout=3.0)
            await node_b.wait_for_connected_peers(1, timeout=3.0)

            assert node_a.is_connected_to("node-b")
            assert node_b.is_connected_to("node-a")
        finally:
            await node_b.stop()
            await node_a.stop()

    asyncio.run(scenario())


def test_mdns_discovery_connects_two_nodes() -> None:
    async def scenario() -> None:
        mdns_port = _pick_free_udp_port()
        config_a = NetworkConfig(
            host="127.0.0.1",
            port=0,
            node_id="node-mdns-a",
            enable_mdns=True,
            mdns_group="224.1.1.199",
            mdns_port=mdns_port,
            mdns_interval_seconds=0.2,
        )
        config_b = NetworkConfig(
            host="127.0.0.1",
            port=0,
            node_id="node-mdns-b",
            enable_mdns=True,
            mdns_group="224.1.1.199",
            mdns_port=mdns_port,
            mdns_interval_seconds=0.2,
        )
        node_a = MiniChainNetwork(config_a)
        node_b = MiniChainNetwork(config_b)
        await node_a.start()
        await node_b.start()

        try:
            await node_a.wait_for_connected_peers(1, timeout=5.0)
            await node_b.wait_for_connected_peers(1, timeout=5.0)

            assert node_a.is_connected_to("node-mdns-b")
            assert node_b.is_connected_to("node-mdns-a")
        finally:
            await node_b.stop()
            await node_a.stop()

    asyncio.run(scenario())


def test_bootstrap_reconnects_when_peer_starts_late() -> None:
    async def scenario() -> None:
        listen_port = _pick_free_tcp_port()
        node_b = MiniChainNetwork(
            NetworkConfig(
                host="127.0.0.1",
                port=0,
                node_id="node-reconnect-b",
                enable_mdns=False,
                bootstrap_peers=(PeerAddress(host="127.0.0.1", port=listen_port),),
                reconnect_interval_seconds=0.1,
                connect_timeout_seconds=0.1,
            )
        )
        await node_b.start()
        await asyncio.sleep(0.2)

        node_a = MiniChainNetwork(
            NetworkConfig(
                host="127.0.0.1",
                port=listen_port,
                node_id="node-reconnect-a",
                enable_mdns=False,
                reconnect_interval_seconds=0.1,
                connect_timeout_seconds=0.1,
            )
        )
        await node_a.start()

        try:
            await node_a.wait_for_connected_peers(1, timeout=3.0)
            await node_b.wait_for_connected_peers(1, timeout=3.0)
            assert node_a.is_connected_to("node-reconnect-b")
            assert node_b.is_connected_to("node-reconnect-a")
        finally:
            await node_a.stop()
            await node_b.stop()

    asyncio.run(scenario())


def _pick_free_udp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()


def _pick_free_tcp_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
    finally:
        sock.close()
