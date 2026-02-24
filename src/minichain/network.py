"""Peer-to-peer networking and peer discovery for MiniChain."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import socket
import struct
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Coroutine

from minichain.block import Block, BlockHeader
from minichain.transaction import Transaction

_LOCAL_DISCOVERY_REGISTRY: set["MiniChainNetwork"] = set()
TX_GOSSIP_PROTOCOL_ID = "/minichain/tx/1.0.0"
BLOCK_GOSSIP_PROTOCOL_ID = "/minichain/block/1.0.0"
SYNC_PROTOCOL_ID = "/minichain/sync/1.0.0"


class NetworkError(ValueError):
    """Raised when networking configuration or message handling is invalid."""


@dataclass(frozen=True)
class PeerAddress:
    """Network address for a peer node."""

    host: str
    port: int

    def validate(self) -> None:
        if not self.host:
            raise NetworkError("peer host must be non-empty")
        if not (0 <= self.port <= 65535):
            raise NetworkError("peer port must be between 0 and 65535")

    @classmethod
    def from_string(cls, value: str) -> PeerAddress:
        if ":" not in value:
            raise NetworkError("peer must be formatted as host:port")
        host, port_text = value.rsplit(":", 1)
        if not port_text.isdigit():
            raise NetworkError("peer port must be numeric")
        peer = cls(host=host, port=int(port_text))
        peer.validate()
        return peer


@dataclass(frozen=True)
class PeerInfo:
    """Metadata tracked for a discovered peer."""

    node_id: str
    address: PeerAddress
    discovered_via: str
    last_seen: int


@dataclass(frozen=True)
class NetworkConfig:
    """Runtime configuration for the MiniChain networking service."""

    host: str = "127.0.0.1"
    port: int = 0
    advertise_host: str | None = None
    node_id: str | None = None
    bootstrap_peers: tuple[PeerAddress, ...] = field(default_factory=tuple)
    connect_timeout_seconds: float = 2.0
    enable_mdns: bool = True
    mdns_group: str = "224.1.1.199"
    mdns_port: int = 10099
    mdns_interval_seconds: float = 0.5
    reconnect_interval_seconds: float = 1.0
    seen_tx_cache_size: int = 20_000
    seen_block_cache_size: int = 5_000
    sync_batch_size: int = 128

    def validate(self) -> None:
        if not self.host:
            raise NetworkError("host must be non-empty")
        if self.advertise_host is not None and not self.advertise_host:
            raise NetworkError("advertise_host must be non-empty when provided")
        if not (0 <= self.port <= 65535):
            raise NetworkError("port must be between 0 and 65535")
        if self.connect_timeout_seconds <= 0:
            raise NetworkError("connect_timeout_seconds must be positive")
        if not (0 <= self.mdns_port <= 65535):
            raise NetworkError("mdns_port must be between 0 and 65535")
        if self.mdns_interval_seconds <= 0:
            raise NetworkError("mdns_interval_seconds must be positive")
        if self.reconnect_interval_seconds <= 0:
            raise NetworkError("reconnect_interval_seconds must be positive")
        if self.seen_tx_cache_size <= 0:
            raise NetworkError("seen_tx_cache_size must be positive")
        if self.seen_block_cache_size <= 0:
            raise NetworkError("seen_block_cache_size must be positive")
        if self.sync_batch_size <= 0:
            raise NetworkError("sync_batch_size must be positive")
        for peer in self.bootstrap_peers:
            peer.validate()


@dataclass
class _PeerConnection:
    peer: PeerInfo
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    task: asyncio.Task[None] | None = None


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    def __init__(self, callback: Callable[[bytes, tuple[str, int]], None]) -> None:
        self._callback = callback

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._callback(data, addr)


class MiniChainNetwork:
    """Async TCP-based peer networking with bootstrap and multicast discovery."""

    def __init__(self, config: NetworkConfig) -> None:
        self.config = config
        self.config.validate()

        self._node_id = self.config.node_id or secrets.token_hex(16)
        self._server: asyncio.AbstractServer | None = None
        self._connections: dict[str, _PeerConnection] = {}
        self._known_peers: dict[str, PeerInfo] = {}
        self._connecting_addresses: set[tuple[str, int]] = set()
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._running = False
        self._listen_port = self.config.port

        self._mdns_transport: asyncio.DatagramTransport | None = None
        self._mdns_protocol: _DiscoveryProtocol | None = None
        self._mdns_announce_task: asyncio.Task[None] | None = None
        self._use_local_discovery = False
        self._seen_transactions: set[str] = set()
        self._seen_transaction_order: deque[str] = deque()
        self._seen_blocks: set[str] = set()
        self._seen_block_order: deque[str] = deque()
        self._transaction_handler: Callable[[Transaction], bool] | None = None
        self._block_handler: Callable[[Block], bool] | None = None
        self._sync_height_getter: Callable[[], int] | None = None
        self._sync_block_getter: Callable[[int], Block | None] | None = None
        self._sync_block_applier: Callable[[Block], bool] | None = None
        self._peer_advertised_heights: dict[str, int] = {}
        self._sync_inflight: set[str] = set()

    @property
    def node_id(self) -> str:
        return self._node_id

    @property
    def running(self) -> bool:
        return self._running

    @property
    def listen_host(self) -> str:
        return self.config.host

    @property
    def listen_port(self) -> int:
        return self._listen_port

    @property
    def advertise_host(self) -> str:
        if self.config.advertise_host is not None:
            return self.config.advertise_host
        return self.listen_host

    def listen_address(self) -> PeerAddress:
        return PeerAddress(host=self.listen_host, port=self.listen_port)

    def known_peers(self) -> list[PeerInfo]:
        return sorted(self._known_peers.values(), key=lambda peer: peer.node_id)

    def connected_peer_ids(self) -> set[str]:
        return set(self._connections)

    def is_connected_to(self, peer_id: str) -> bool:
        return peer_id in self._connections

    def set_transaction_handler(self, handler: Callable[[Transaction], bool] | None) -> None:
        """Register a local transaction validation/ingestion callback."""
        self._transaction_handler = handler

    def set_block_handler(self, handler: Callable[[Block], bool] | None) -> None:
        """Register a local block validation/ingestion callback."""
        self._block_handler = handler

    def set_sync_handlers(
        self,
        *,
        get_height: Callable[[], int] | None,
        get_block_by_height: Callable[[int], Block | None] | None,
        apply_block: Callable[[Block], bool] | None,
    ) -> None:
        """Register callbacks used by `/minichain/sync/1.0.0`."""
        self._sync_height_getter = get_height
        self._sync_block_getter = get_block_by_height
        self._sync_block_applier = apply_block

    async def wait_for_connected_peers(self, expected_count: int, *, timeout: float = 5.0) -> None:
        if expected_count < 0:
            raise NetworkError("expected_count must be non-negative")
        if timeout <= 0:
            raise NetworkError("timeout must be positive")

        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if len(self._connections) >= expected_count:
                return
            await asyncio.sleep(0.05)
        raise TimeoutError(
            f"Timed out waiting for {expected_count} peers; got {len(self._connections)}"
        )

    async def wait_for_height(self, expected_height: int, *, timeout: float = 5.0) -> None:
        """Wait until local sync height reaches at least `expected_height`."""
        if expected_height < 0:
            raise NetworkError("expected_height must be non-negative")
        if timeout <= 0:
            raise NetworkError("timeout must be positive")

        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self._local_chain_height() >= expected_height:
                return
            await asyncio.sleep(0.05)
        raise TimeoutError(
            f"Timed out waiting for height {expected_height}; got {self._local_chain_height()}"
        )

    async def submit_transaction(self, transaction: Transaction) -> bool:
        """Validate and gossip a locally submitted transaction."""
        if not transaction.verify():
            raise NetworkError("cannot gossip invalid transaction")
        tx_id = transaction.transaction_id().hex()
        if not self._remember_seen_transaction(tx_id):
            return False
        if not self._accept_transaction(transaction):
            return False

        message = self._transaction_payload(transaction, tx_id=tx_id)
        await self._broadcast_message(message, exclude_peer_ids=set())
        return True

    async def submit_block(self, block: Block, *, already_applied: bool = False) -> bool:
        """Validate and gossip a locally mined or received canonical block."""
        if not block.has_valid_merkle_root():
            raise NetworkError("cannot gossip block with invalid merkle root")

        block_hash = block.hash().hex()
        if not self._remember_seen_block(block_hash):
            return False
        if not already_applied and not self._accept_block(block):
            return False

        message = self._block_payload(block, block_hash=block_hash)
        await self._broadcast_message(message, exclude_peer_ids=set())
        return True

    async def start(self) -> None:
        """Start the TCP server, discovery tasks, and bootstrap connections."""
        if self._running:
            return

        self._server = await asyncio.start_server(
            self._handle_incoming_connection,
            host=self.config.host,
            port=self.config.port,
        )
        sockets = self._server.sockets or []
        if not sockets:
            raise NetworkError("failed to bind network server socket")
        self._listen_port = int(sockets[0].getsockname()[1])
        self._running = True

        if self.config.enable_mdns:
            await self._start_mdns_discovery()

        for peer in self.config.bootstrap_peers:
            self._spawn(self.connect_to_peer(peer, discovered_via="bootstrap"))
        self._spawn(self._reconnect_loop())

    async def stop(self) -> None:
        """Stop server, discovery, and all active peer connections."""
        if not self._running:
            return
        self._running = False

        if self._mdns_announce_task is not None:
            self._mdns_announce_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._mdns_announce_task
            self._mdns_announce_task = None

        if self._mdns_transport is not None:
            self._mdns_transport.close()
            self._mdns_transport = None
            self._mdns_protocol = None
        if self._use_local_discovery:
            _LOCAL_DISCOVERY_REGISTRY.discard(self)
            self._use_local_discovery = False

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

        for peer_id in list(self._connections):
            self._close_connection(peer_id)
        self._peer_advertised_heights.clear()
        self._sync_inflight.clear()

        if self._background_tasks:
            for task in list(self._background_tasks):
                task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

    async def connect_to_peer(self, peer: PeerAddress, *, discovered_via: str) -> bool:
        """Open a TCP connection to a peer and perform handshake."""
        peer.validate()
        if self._is_self_peer(peer):
            return False

        address_key = (peer.host, peer.port)
        if address_key in self._connecting_addresses:
            return False
        if any(connection.peer.address == peer for connection in self._connections.values()):
            return False

        self._connecting_addresses.add(address_key)
        try:
            try:
                connection = asyncio.open_connection(peer.host, peer.port)
                reader, writer = await asyncio.wait_for(
                    connection,
                    timeout=self.config.connect_timeout_seconds,
                )
            except (TimeoutError, OSError):
                return False

            try:
                await self._write_message(writer, self._hello_payload())
                message = await self._read_message(reader)
                peer_info = self._peer_from_hello(
                    message=message,
                    fallback_host=peer.host,
                    discovered_via=discovered_via,
                )
                if peer_info.node_id == self.node_id:
                    writer.close()
                    await writer.wait_closed()
                    return False

                if not self._register_connection(peer_info, reader, writer):
                    writer.close()
                    await writer.wait_closed()
                    return False

                await self._write_message(writer, self._peer_list_payload())
                await self._send_sync_status(writer)
                self._start_peer_reader(peer_info.node_id)
                return True
            except Exception:
                writer.close()
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
                raise
        finally:
            self._connecting_addresses.discard(address_key)

    async def _handle_incoming_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            message = await self._read_message(reader)
            peername = writer.get_extra_info("peername")
            fallback_host = "127.0.0.1" if not peername else str(peername[0])
            peer_info = self._peer_from_hello(
                message=message,
                fallback_host=fallback_host,
                discovered_via="incoming",
            )
            if peer_info.node_id == self.node_id:
                writer.close()
                await writer.wait_closed()
                return

            await self._write_message(writer, self._hello_payload())
            if not self._register_connection(peer_info, reader, writer):
                writer.close()
                await writer.wait_closed()
                return

            await self._write_message(writer, self._peer_list_payload())
            await self._send_sync_status(writer)
            self._start_peer_reader(peer_info.node_id)
        except Exception:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _register_connection(
        self,
        peer: PeerInfo,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> bool:
        existing = self._connections.get(peer.node_id)
        if existing is not None:
            return False

        self._known_peers[peer.node_id] = peer
        self._connections[peer.node_id] = _PeerConnection(
            peer=peer,
            reader=reader,
            writer=writer,
        )
        return True

    def _start_peer_reader(self, peer_id: str) -> None:
        connection = self._connections.get(peer_id)
        if connection is None:
            return
        task = asyncio.create_task(self._peer_reader_loop(peer_id))
        connection.task = task
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _peer_reader_loop(self, peer_id: str) -> None:
        connection = self._connections.get(peer_id)
        if connection is None:
            return
        try:
            while self._running:
                message = await self._read_message(connection.reader, eof_ok=True)
                if message is None:
                    break
                await self._handle_peer_message(peer_id, message)
        except Exception:
            pass
        finally:
            self._close_connection(peer_id)

    async def _handle_peer_message(self, peer_id: str, message: dict[str, object]) -> None:
        message_type = message.get("type")
        if message_type == "peers":
            await self._handle_peer_addresses(peer_id, message)
            return
        if message_type == "tx_result":
            return
        if message_type == "tx_gossip":
            await self._handle_transaction_gossip(peer_id, message)
            return
        if message_type == "block_gossip":
            await self._handle_block_gossip(peer_id, message)
            return
        if message_type == "sync_status":
            await self._handle_sync_status(peer_id, message)
            return
        if message_type == "sync_request":
            await self._handle_sync_request(peer_id, message)
            return
        if message_type == "sync_blocks":
            await self._handle_sync_blocks(peer_id, message)
            return

    async def _handle_peer_addresses(self, peer_id: str, message: dict[str, object]) -> None:
        peers = message.get("peers")
        if not isinstance(peers, list):
            raise NetworkError("peers message requires list payload")

        for candidate in peers:
            if not isinstance(candidate, dict):
                continue
            host = candidate.get("host")
            port = candidate.get("port")
            if not isinstance(host, str) or not isinstance(port, int):
                continue
            peer = PeerAddress(host=host, port=port)
            if self._is_self_peer(peer):
                continue
            self._spawn(
                self.connect_to_peer(
                    peer,
                    discovered_via=f"peer:{peer_id}",
                )
            )

    async def _handle_transaction_gossip(
        self,
        source_peer_id: str,
        message: dict[str, object],
    ) -> None:
        if message.get("protocol") != TX_GOSSIP_PROTOCOL_ID:
            raise NetworkError("tx_gossip protocol id mismatch")

        payload = message.get("transaction")
        if not isinstance(payload, dict):
            raise NetworkError("tx_gossip transaction payload must be an object")
        transaction = self._transaction_from_payload(payload)

        announced_id = message.get("transaction_id")
        if announced_id is not None and not isinstance(announced_id, str):
            raise NetworkError("transaction_id must be a string")

        tx_id = transaction.transaction_id().hex()
        accepted = False
        reason = "accepted"
        if not transaction.verify():
            reason = "invalid_signature_or_identity"
        elif announced_id is not None and announced_id != tx_id:
            reason = "transaction_id_mismatch"
        elif not self._remember_seen_transaction(tx_id):
            reason = "duplicate"
        elif not self._accept_transaction(transaction):
            reason = "rejected_by_node"
        else:
            accepted = True

        await self._send_tx_result(
            peer_id=source_peer_id,
            transaction_id=tx_id,
            accepted=accepted,
            reason=reason,
        )
        if not accepted:
            return

        forward_payload = self._transaction_payload(transaction, tx_id=tx_id)
        await self._broadcast_message(
            forward_payload,
            exclude_peer_ids={source_peer_id},
        )

    async def _handle_block_gossip(
        self,
        source_peer_id: str,
        message: dict[str, object],
    ) -> None:
        if message.get("protocol") != BLOCK_GOSSIP_PROTOCOL_ID:
            raise NetworkError("block_gossip protocol id mismatch")

        payload = message.get("block")
        if not isinstance(payload, dict):
            raise NetworkError("block_gossip payload must be an object")
        block = self._block_from_payload(payload)
        if not block.has_valid_merkle_root():
            return

        announced_hash = message.get("block_hash")
        if announced_hash is not None and not isinstance(announced_hash, str):
            raise NetworkError("block_hash must be a string")

        block_hash = block.hash().hex()
        if announced_hash is not None and announced_hash != block_hash:
            return
        if not self._remember_seen_block(block_hash):
            return
        if not self._accept_block(block):
            return

        forward_payload = self._block_payload(block, block_hash=block_hash)
        await self._broadcast_message(
            forward_payload,
            exclude_peer_ids={source_peer_id},
        )

    async def _handle_sync_status(self, peer_id: str, message: dict[str, object]) -> None:
        if message.get("protocol") != SYNC_PROTOCOL_ID:
            raise NetworkError("sync_status protocol id mismatch")
        peer_height = message.get("height")
        if not isinstance(peer_height, int):
            raise NetworkError("sync_status height must be an integer")
        if peer_height < 0:
            raise NetworkError("sync_status height must be non-negative")

        self._peer_advertised_heights[peer_id] = peer_height
        if peer_height > self._local_chain_height():
            self._spawn(self._request_missing_blocks(peer_id))

    async def _handle_sync_request(self, peer_id: str, message: dict[str, object]) -> None:
        if message.get("protocol") != SYNC_PROTOCOL_ID:
            raise NetworkError("sync_request protocol id mismatch")
        if self._sync_block_getter is None:
            return

        from_height = message.get("from_height")
        to_height = message.get("to_height")
        if not isinstance(from_height, int) or not isinstance(to_height, int):
            raise NetworkError("sync_request heights must be integers")
        if from_height < 0 or to_height < from_height:
            raise NetworkError("sync_request range is invalid")

        max_to_height = min(to_height, from_height + self.config.sync_batch_size - 1)
        blocks: list[Block] = []
        for height in range(from_height, max_to_height + 1):
            block = self._sync_block_getter(height)
            if block is None:
                break
            blocks.append(block)

        connection = self._connections.get(peer_id)
        if connection is None:
            return
        response = self._sync_blocks_payload(start_height=from_height, blocks=blocks)
        await self._write_message(connection.writer, response)

    async def _handle_sync_blocks(self, peer_id: str, message: dict[str, object]) -> None:
        if message.get("protocol") != SYNC_PROTOCOL_ID:
            raise NetworkError("sync_blocks protocol id mismatch")
        if self._sync_block_applier is None:
            self._sync_inflight.discard(peer_id)
            return

        start_height = message.get("start_height")
        payloads = message.get("blocks")
        if not isinstance(start_height, int):
            raise NetworkError("sync_blocks start_height must be an integer")
        if not isinstance(payloads, list):
            raise NetworkError("sync_blocks blocks must be a list")

        for entry in payloads:
            if not isinstance(entry, dict):
                raise NetworkError("sync_blocks entry must be an object")
            block = self._block_from_payload(entry)
            if not block.has_valid_merkle_root():
                self._sync_inflight.discard(peer_id)
                return
            if not self._sync_block_applier(block):
                self._sync_inflight.discard(peer_id)
                return
            self._remember_seen_block(block.hash().hex())

        if not payloads:
            self._sync_inflight.discard(peer_id)
            return

        self._sync_inflight.discard(peer_id)
        if self._peer_advertised_heights.get(peer_id, -1) > self._local_chain_height():
            self._spawn(self._request_missing_blocks(peer_id))

    async def _request_missing_blocks(self, peer_id: str) -> None:
        if peer_id in self._sync_inflight:
            return
        remote_height = self._peer_advertised_heights.get(peer_id)
        if remote_height is None:
            return

        local_height = self._local_chain_height()
        if remote_height <= local_height:
            return

        connection = self._connections.get(peer_id)
        if connection is None:
            return

        from_height = local_height + 1
        to_height = min(remote_height, from_height + self.config.sync_batch_size - 1)
        request = self._sync_request_payload(from_height=from_height, to_height=to_height)
        self._sync_inflight.add(peer_id)
        try:
            await self._write_message(connection.writer, request)
        except Exception:
            self._sync_inflight.discard(peer_id)
            self._close_connection(peer_id)

    def _close_connection(self, peer_id: str) -> None:
        connection = self._connections.pop(peer_id, None)
        if connection is None:
            return

        self._peer_advertised_heights.pop(peer_id, None)
        self._sync_inflight.discard(peer_id)

        if connection.task is not None and not connection.task.done():
            connection.task.cancel()
        connection.writer.close()

    async def _broadcast_message(
        self,
        payload: dict[str, object],
        *,
        exclude_peer_ids: set[str],
    ) -> None:
        failed_peer_ids: list[str] = []
        for peer_id, connection in list(self._connections.items()):
            if peer_id in exclude_peer_ids:
                continue
            try:
                await self._write_message(connection.writer, payload)
            except Exception:
                failed_peer_ids.append(peer_id)

        for peer_id in failed_peer_ids:
            self._close_connection(peer_id)

    async def _start_mdns_discovery(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            sock.bind(("", self.config.mdns_port))

            membership = struct.pack(
                "=4s4s",
                socket.inet_aton(self.config.mdns_group),
                socket.inet_aton("0.0.0.0"),
            )
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)

            protocol = _DiscoveryProtocol(self._on_discovery_packet)
            transport, _ = await loop.create_datagram_endpoint(
                lambda: protocol,
                sock=sock,
            )
            self._mdns_transport = transport
            self._mdns_protocol = protocol
        except OSError:
            self._use_local_discovery = True
            _LOCAL_DISCOVERY_REGISTRY.add(self)

        self._mdns_announce_task = asyncio.create_task(self._announce_loop())
        self._background_tasks.add(self._mdns_announce_task)
        self._mdns_announce_task.add_done_callback(self._background_tasks.discard)

    async def _announce_loop(self) -> None:
        while self._running and (self._mdns_transport is not None or self._use_local_discovery):
            payload = {
                "service": "minichain",
                "node_id": self.node_id,
                "host": self.advertise_host,
                "port": self.listen_port,
            }
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            if self._use_local_discovery:
                for peer in list(_LOCAL_DISCOVERY_REGISTRY):
                    if peer is self:
                        continue
                    peer._on_discovery_packet(encoded, (self.listen_host, self.listen_port))
            elif self._mdns_transport is not None:
                self._mdns_transport.sendto(
                    encoded,
                    (self.config.mdns_group, self.config.mdns_port),
                )
            await asyncio.sleep(self.config.mdns_interval_seconds)

    def _on_discovery_packet(self, data: bytes, _addr: tuple[str, int]) -> None:
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if not isinstance(payload, dict):
            return
        if payload.get("service") != "minichain":
            return

        node_id = payload.get("node_id")
        host = payload.get("host")
        port = payload.get("port")
        if not isinstance(node_id, str) or not isinstance(host, str) or not isinstance(port, int):
            return
        if node_id == self.node_id:
            return

        peer = PeerAddress(host=host, port=port)
        info = PeerInfo(
            node_id=node_id,
            address=peer,
            discovered_via="mdns",
            last_seen=int(time.time()),
        )
        existing = self._known_peers.get(node_id)
        if existing is None:
            self._known_peers[node_id] = info
        self._spawn(self.connect_to_peer(peer, discovered_via="mdns"))

    async def _reconnect_loop(self) -> None:
        while self._running:
            for peer in self._reconnect_candidates():
                if not self._running:
                    return
                await self.connect_to_peer(peer, discovered_via="reconnect")
            await asyncio.sleep(self.config.reconnect_interval_seconds)

    def _reconnect_candidates(self) -> tuple[PeerAddress, ...]:
        seen: set[tuple[str, int]] = set()
        ordered: list[PeerAddress] = []

        for peer in self.config.bootstrap_peers:
            key = (peer.host, peer.port)
            if key in seen or self._is_self_peer(peer):
                continue
            seen.add(key)
            ordered.append(peer)

        for info in self._known_peers.values():
            peer = info.address
            key = (peer.host, peer.port)
            if key in seen or self._is_self_peer(peer):
                continue
            seen.add(key)
            ordered.append(peer)

        return tuple(ordered)

    def _spawn(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _is_self_peer(self, peer: PeerAddress) -> bool:
        if peer.port != self.listen_port:
            return False
        known_self_hosts = {
            self.listen_host,
            self.advertise_host,
        }
        return peer.host in known_self_hosts

    async def _send_sync_status(self, writer: asyncio.StreamWriter) -> None:
        payload = self._sync_status_payload(height=self._local_chain_height())
        await self._write_message(writer, payload)

    async def _send_tx_result(
        self,
        *,
        peer_id: str,
        transaction_id: str,
        accepted: bool,
        reason: str,
    ) -> None:
        connection = self._connections.get(peer_id)
        if connection is None:
            return
        payload = self._tx_result_payload(
            transaction_id=transaction_id,
            accepted=accepted,
            reason=reason,
        )
        try:
            await self._write_message(connection.writer, payload)
        except Exception:
            self._close_connection(peer_id)

    def _local_chain_height(self) -> int:
        if self._sync_height_getter is None:
            return 0
        try:
            height = int(self._sync_height_getter())
        except Exception:
            return 0
        return max(0, height)

    def _remember_seen_transaction(self, transaction_id: str) -> bool:
        if transaction_id in self._seen_transactions:
            return False

        self._seen_transactions.add(transaction_id)
        self._seen_transaction_order.append(transaction_id)
        while len(self._seen_transactions) > self.config.seen_tx_cache_size:
            oldest = self._seen_transaction_order.popleft()
            self._seen_transactions.discard(oldest)
        return True

    def _accept_transaction(self, transaction: Transaction) -> bool:
        if self._transaction_handler is None:
            return True
        try:
            return bool(self._transaction_handler(transaction))
        except Exception:
            return False

    def _remember_seen_block(self, block_hash: str) -> bool:
        if block_hash in self._seen_blocks:
            return False

        self._seen_blocks.add(block_hash)
        self._seen_block_order.append(block_hash)
        while len(self._seen_blocks) > self.config.seen_block_cache_size:
            oldest = self._seen_block_order.popleft()
            self._seen_blocks.discard(oldest)
        return True

    def _accept_block(self, block: Block) -> bool:
        if self._block_handler is None:
            return True
        try:
            return bool(self._block_handler(block))
        except Exception:
            return False

    def _hello_payload(self) -> dict[str, object]:
        return {
            "type": "hello",
            "node_id": self.node_id,
            "host": self.advertise_host,
            "port": self.listen_port,
        }

    def _peer_list_payload(self) -> dict[str, object]:
        unique_peers = {
            (peer.address.host, peer.address.port)
            for peer in self._known_peers.values()
        }
        unique_peers.update((peer.host, peer.port) for peer in self.config.bootstrap_peers)
        unique_peers.discard((self.listen_host, self.listen_port))
        unique_peers.discard((self.advertise_host, self.listen_port))
        peers = [{"host": host, "port": port} for host, port in sorted(unique_peers)]
        return {"type": "peers", "peers": peers}

    def _sync_status_payload(self, *, height: int) -> dict[str, object]:
        return {
            "type": "sync_status",
            "protocol": SYNC_PROTOCOL_ID,
            "height": height,
        }

    def _sync_request_payload(self, *, from_height: int, to_height: int) -> dict[str, object]:
        return {
            "type": "sync_request",
            "protocol": SYNC_PROTOCOL_ID,
            "from_height": from_height,
            "to_height": to_height,
        }

    def _sync_blocks_payload(self, *, start_height: int, blocks: list[Block]) -> dict[str, object]:
        return {
            "type": "sync_blocks",
            "protocol": SYNC_PROTOCOL_ID,
            "start_height": start_height,
            "blocks": [self._encode_block(block) for block in blocks],
        }

    def _transaction_payload(self, transaction: Transaction, *, tx_id: str) -> dict[str, object]:
        return {
            "type": "tx_gossip",
            "protocol": TX_GOSSIP_PROTOCOL_ID,
            "transaction_id": tx_id,
            "transaction": asdict(transaction),
        }

    def _tx_result_payload(
        self,
        *,
        transaction_id: str,
        accepted: bool,
        reason: str,
    ) -> dict[str, object]:
        return {
            "type": "tx_result",
            "transaction_id": transaction_id,
            "accepted": accepted,
            "reason": reason,
        }

    def _transaction_from_payload(self, payload: dict[str, object]) -> Transaction:
        try:
            return Transaction(**payload)
        except TypeError as exc:
            raise NetworkError("invalid transaction payload shape") from exc

    def _block_payload(self, block: Block, *, block_hash: str) -> dict[str, object]:
        return {
            "type": "block_gossip",
            "protocol": BLOCK_GOSSIP_PROTOCOL_ID,
            "block_hash": block_hash,
            "block": self._encode_block(block),
        }

    @staticmethod
    def _encode_block(block: Block) -> dict[str, object]:
        return {
            "header": asdict(block.header),
            "transactions": [asdict(transaction) for transaction in block.transactions],
        }

    def _block_from_payload(self, payload: dict[str, object]) -> Block:
        header_payload = payload.get("header")
        transactions_payload = payload.get("transactions")
        if not isinstance(header_payload, dict):
            raise NetworkError("block header payload must be an object")
        if not isinstance(transactions_payload, list):
            raise NetworkError("block transactions payload must be a list")

        try:
            header = BlockHeader(**header_payload)
        except TypeError as exc:
            raise NetworkError("invalid block header payload shape") from exc

        transactions: list[Transaction] = []
        for transaction_payload in transactions_payload:
            if not isinstance(transaction_payload, dict):
                raise NetworkError("transaction entry must be an object")
            try:
                transactions.append(Transaction(**transaction_payload))
            except TypeError as exc:
                raise NetworkError("invalid block transaction payload shape") from exc
        return Block(header=header, transactions=transactions)

    def _peer_from_hello(
        self,
        *,
        message: dict[str, object],
        fallback_host: str,
        discovered_via: str,
    ) -> PeerInfo:
        if message.get("type") != "hello":
            raise NetworkError("handshake message must be type=hello")
        node_id = message.get("node_id")
        host = message.get("host")
        port = message.get("port")
        if not isinstance(node_id, str):
            raise NetworkError("handshake node_id must be a string")
        if not isinstance(port, int):
            raise NetworkError("handshake port must be an integer")
        if not isinstance(host, str) or not host:
            host = fallback_host
        address = PeerAddress(host=host, port=port)
        address.validate()
        return PeerInfo(
            node_id=node_id,
            address=address,
            discovered_via=discovered_via,
            last_seen=int(time.time()),
        )

    async def _read_message(
        self,
        reader: asyncio.StreamReader,
        *,
        eof_ok: bool = False,
    ) -> dict[str, object] | None:
        line = await asyncio.wait_for(
            reader.readline(),
            timeout=self.config.connect_timeout_seconds,
        )
        if not line:
            if eof_ok:
                return None
            raise NetworkError("unexpected EOF while reading peer message")

        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NetworkError("received malformed JSON message") from exc
        if not isinstance(payload, dict):
            raise NetworkError("message payload must be an object")
        return payload

    async def _write_message(
        self,
        writer: asyncio.StreamWriter,
        payload: dict[str, object],
    ) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        writer.write(body + b"\n")
        await writer.drain()
