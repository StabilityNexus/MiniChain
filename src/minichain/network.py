"""Peer-to-peer networking and peer discovery for MiniChain."""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine

_LOCAL_DISCOVERY_REGISTRY: set["MiniChainNetwork"] = set()


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
    node_id: str | None = None
    bootstrap_peers: tuple[PeerAddress, ...] = field(default_factory=tuple)
    connect_timeout_seconds: float = 2.0
    enable_mdns: bool = True
    mdns_group: str = "224.1.1.199"
    mdns_port: int = 10099
    mdns_interval_seconds: float = 0.5

    def validate(self) -> None:
        if not self.host:
            raise NetworkError("host must be non-empty")
        if not (0 <= self.port <= 65535):
            raise NetworkError("port must be between 0 and 65535")
        if self.connect_timeout_seconds <= 0:
            raise NetworkError("connect_timeout_seconds must be positive")
        if not (0 <= self.mdns_port <= 65535):
            raise NetworkError("mdns_port must be between 0 and 65535")
        if self.mdns_interval_seconds <= 0:
            raise NetworkError("mdns_interval_seconds must be positive")
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

    def listen_address(self) -> PeerAddress:
        return PeerAddress(host=self.listen_host, port=self.listen_port)

    def known_peers(self) -> list[PeerInfo]:
        return sorted(self._known_peers.values(), key=lambda peer: peer.node_id)

    def connected_peer_ids(self) -> set[str]:
        return set(self._connections)

    def is_connected_to(self, peer_id: str) -> bool:
        return peer_id in self._connections

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

        if self._background_tasks:
            for task in list(self._background_tasks):
                task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()

    async def connect_to_peer(self, peer: PeerAddress, *, discovered_via: str) -> bool:
        """Open a TCP connection to a peer and perform handshake."""
        peer.validate()
        if peer == self.listen_address():
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
        if message_type != "peers":
            return

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
            if peer == self.listen_address():
                continue
            self._spawn(
                self.connect_to_peer(
                    peer,
                    discovered_via=f"peer:{peer_id}",
                )
            )

    def _close_connection(self, peer_id: str) -> None:
        connection = self._connections.pop(peer_id, None)
        if connection is None:
            return

        if connection.task is not None and not connection.task.done():
            connection.task.cancel()
        connection.writer.close()

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
                "host": self.listen_host,
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

    def _spawn(self, coroutine: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    def _hello_payload(self) -> dict[str, object]:
        return {
            "type": "hello",
            "node_id": self.node_id,
            "host": self.listen_host,
            "port": self.listen_port,
        }

    def _peer_list_payload(self) -> dict[str, object]:
        unique_peers = {
            (peer.address.host, peer.address.port)
            for peer in self._known_peers.values()
        }
        unique_peers.update((peer.host, peer.port) for peer in self.config.bootstrap_peers)
        unique_peers.discard((self.listen_host, self.listen_port))
        peers = [{"host": host, "port": port} for host, port in sorted(unique_peers)]
        return {"type": "peers", "peers": peers}

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
