"""
Minimal TCP-based P2P network layer for MiniChain testnet demo.

Each node runs an asyncio TCP server and can connect to peers.
Messages are newline-delimited JSON.
"""

import asyncio
import json
import logging

logger = logging.getLogger(__name__)

TOPIC = "minichain-global"


class P2PNetwork:
    """
    Lightweight peer-to-peer networking using asyncio TCP streams.

    JSON wire format (one JSON object per line):
        {"type": "tx" | "block", "data": {...}}
    """

    def __init__(self, handler_callback=None):
        self._handler_callback = None
        if handler_callback is not None:
            self.register_handler(handler_callback)
        self._peers: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
        self._server: asyncio.Server | None = None
        self._port: int = 0
        self._listen_tasks: list[asyncio.Task] = []
        self._on_peer_connected = None  # callback(writer) called when a new peer connects
        self._ready_events: dict[str, asyncio.Event] = {}

    def register_handler(self, handler_callback):
        if not callable(handler_callback):
            raise ValueError("handler_callback must be callable")
        self._handler_callback = handler_callback

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def start(self, port: int = 9000):
        """Start listening for incoming peer connections on the given port."""
        self._port = port
        self._server = await asyncio.start_server(
            self._handle_incoming, "0.0.0.0", port
        )
        logger.info("Network: Listening on 0.0.0.0:%d", port)

    async def stop(self):
        """Gracefully shut down the server and disconnect all peers."""
        logger.info("Network: Shutting down")
        for task in self._listen_tasks:
            task.cancel()
        for _, writer in self._peers:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
        self._peers.clear()
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ------------------------------------------------------------------
    # Peer connections
    # ------------------------------------------------------------------

    async def connect_to_peer(self, host: str, port: int) -> bool:
        """Actively connect to another MiniChain node."""
        try:
            reader, writer = await asyncio.open_connection(host, port)
            self._peers.append((reader, writer))
            addr = f"{host}:{port}"
            ready_event = asyncio.Event()
            self._ready_events[addr] = ready_event
            task = asyncio.create_task(self._listen_to_peer(reader, writer, addr))
            self._listen_tasks.append(task)
            await self.send_to_peer(writer, {"type": "ready"})
            try:
                await asyncio.wait_for(ready_event.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.error("Network: Ready handshake timeout for %s", addr)
                self._ready_events.pop(addr, None)
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass
                if (reader, writer) in self._peers:
                    self._peers.remove((reader, writer))
                return False
            logger.info("Network: Connected to peer %s:%d", host, port)
            return True
        except Exception as e:
            logger.error("Network: Failed to connect to %s:%d — %s", host, port, e)
            return False

    async def _handle_incoming(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Accept an incoming peer connection."""
        peername = writer.get_extra_info("peername")
        addr = f"{peername[0]}:{peername[1]}" if peername else "unknown"
        logger.info("Network: Incoming peer connection from %s", addr)
        self._peers.append((reader, writer))
        task = asyncio.create_task(self._listen_to_peer(reader, writer, addr))
        self._listen_tasks.append(task)
        # Send current state to the new peer
        if self._on_peer_connected:
            try:
                await self._on_peer_connected(writer)
            except Exception:
                logger.exception("Network: Error during peer sync")

    async def _listen_to_peer(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, addr: str):
        """Read newline-delimited JSON messages from a peer."""
        try:
            while True:
                line = await reader.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode().strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    logger.warning("Network: Malformed message from %s", addr)
                    continue

                msg_type = data.get("type")
                if msg_type == "ready":
                    await self.send_to_peer(writer, {"type": "ready_ack"})
                    continue
                if msg_type == "ready_ack":
                    event = self._ready_events.pop(addr, None)
                    if event:
                        event.set()
                    continue

                if self._handler_callback:
                    try:
                        # Pass writer so handler can respond directly to sender
                        await self._handler_callback(data, writer)
                    except Exception:
                        logger.exception("Network: Handler error for message from %s", addr)
        except asyncio.CancelledError:
            pass
        except ConnectionResetError:
            pass
        finally:
            logger.info("Network: Peer %s disconnected", addr)
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if (reader, writer) in self._peers:
                self._peers.remove((reader, writer))

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------

    async def _broadcast_raw(self, payload: dict):
        """Send a JSON message to every connected peer."""
        line = (json.dumps(payload) + "\n").encode()
        disconnected = []
        for reader, writer in self._peers:
            try:
                writer.write(line)
                await writer.drain()
            except Exception:
                disconnected.append((reader, writer))
        for pair in disconnected:
            if pair in self._peers:
                self._peers.remove(pair)

    async def broadcast_transaction(self, tx):
        sender = getattr(tx, "sender", "<unknown>")
        logger.info("Network: Broadcasting Tx from %s...", sender[:8])
        try:
            payload = {"type": "tx", "data": tx.to_dict()}
        except (TypeError, ValueError) as e:
            logger.error("Network: Failed to serialize tx: %s", e)
            return
        await self._broadcast_raw(payload)

    async def broadcast_block(self, block):
        logger.info("Network: Broadcasting Block #%d", block.index)
        await self._broadcast_raw({"type": "block", "data": block.to_dict()})

    async def request_chain(self):
        """Request the full chain from all connected peers."""
        logger.info("Network: Requesting chain from %d peer(s)...", len(self._peers))
        await self._broadcast_raw({"type": "get_chain", "data": {}})

    async def send_chain(self, writer: asyncio.StreamWriter, chain_data: list):
        """Send the full chain to a specific peer."""
        try:
            payload = {"type": "chain", "data": chain_data}
            line = (json.dumps(payload) + "\n").encode()
            writer.write(line)
            await writer.drain()
            logger.info("Network: Sent chain (%d blocks) to peer", len(chain_data))
        except Exception as e:
            logger.exception("Network: Failed to send chain: %s", e)

    async def send_to_peer(self, writer: asyncio.StreamWriter, payload: dict):
        """Send a message to a specific peer."""
        try:
            line = (json.dumps(payload) + "\n").encode()
            writer.write(line)
            await writer.drain()
        except Exception as e:
            logger.exception("Network: Failed to send to peer: %s", e)

    @property
    def peer_count(self) -> int:
        return len(self._peers)
