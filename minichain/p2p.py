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

    def register_handler(self, handler_callback):
        if not callable(handler_callback):
            raise ValueError("handler_callback must be callable")
        self._handler_callback = handler_callback

    def set_on_peer_connected(self, callback):
        if callback is not None and not asyncio.iscoroutinefunction(callback):
            raise ValueError("on_peer_connected callback must be an async callable")
        self._on_peer_connected = callback

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    async def start(self, port: int = 9000, host: str = "127.0.0.1"):
        """Start listening for incoming peer connections on the given port."""
        self._port = port
        self._server = await asyncio.start_server(
            self._handle_incoming, host, port
        )
        logger.info("Network: Listening on %s:%d", host, port)

    async def stop(self):
        """Gracefully shut down the server and disconnect all peers."""
        logger.info("Network: Shutting down")
        for task in self._listen_tasks:
            task.cancel()
        if self._listen_tasks:
            await asyncio.gather(*self._listen_tasks, return_exceptions=True)
        self._listen_tasks.clear()
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
            if self._on_peer_connected:
                try:
                    await self._on_peer_connected(writer)
                except Exception:
                    logger.exception("Network: Error during peer sync")
            task = asyncio.create_task(self._listen_to_peer(reader, writer, f"{host}:{port}"))
            self._listen_tasks.append(task)
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
                if isinstance(data, dict):
                    data["_peer_addr"] = addr

                if self._handler_callback:
                    try:
                        await self._handler_callback(data)
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
        for reader, writer in disconnected:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            pair = (reader, writer)
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

    @property
    def peer_count(self) -> int:
        return len(self._peers)
