"""
Minimal TCP-based P2P network layer for MiniChain testnet demo.

Each node runs an asyncio TCP server and can connect to peers.
Messages are newline-delimited JSON.
"""

import asyncio
import json
import logging

from .serialization import canonical_json_hash, canonical_json_dumps
from .validators import is_valid_receiver

logger = logging.getLogger(__name__)

TOPIC = "minichain-global"
SUPPORTED_MESSAGE_TYPES = {"sync", "tx", "block"}


class P2PNetwork:
    """
    Lightweight peer-to-peer networking using asyncio TCP streams.

    JSON wire format (one JSON object per line):
        {"type": "sync" | "tx" | "block", "data": {...}}
    """

    def __init__(self, handler_callback=None):
        self._handler_callback = None
        if handler_callback is not None:
            self.register_handler(handler_callback)
        self._peers: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
        self._server: asyncio.Server | None = None
        self._port: int = 0
        self._listen_tasks: list[asyncio.Task] = []
        self._on_peer_connected = None
        self._seen_tx_ids = set()
        self._seen_block_hashes = set()

    def register_handler(self, handler_callback):
        if not callable(handler_callback):
            raise ValueError("handler_callback must be callable")
        self._handler_callback = handler_callback

    def register_on_peer_connected(self, handler_callback):
        if not callable(handler_callback):
            raise ValueError("handler_callback must be callable")
        self._on_peer_connected = handler_callback

    async def _notify_peer_connected(self, writer, error_message):
        if self._on_peer_connected:
            try:
                await self._on_peer_connected(writer)
            except Exception:
                logger.exception(error_message)

    async def start(self, port: int = 9000, host: str = "127.0.0.1"):
        """Start listening for incoming peer connections on the given port."""
        self._port = port
        self._server = await asyncio.start_server(self._handle_incoming, host, port)
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

    async def connect_to_peer(self, host: str, port: int) -> bool:
        """Actively connect to another MiniChain node."""
        try:
            reader, writer = await asyncio.open_connection(host, port)
            self._peers.append((reader, writer))
            task = asyncio.create_task(
                self._listen_to_peer(reader, writer, f"{host}:{port}")
            )
            self._listen_tasks.append(task)
            await self._notify_peer_connected(writer, "Network: Error during outbound peer sync")
            logger.info("Network: Connected to peer %s:%d", host, port)
            return True
        except Exception as exc:
            logger.error("Network: Failed to connect to %s:%d — %s", host, port, exc)
            return False

    async def _handle_incoming(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        """Accept an incoming peer connection."""
        peername = writer.get_extra_info("peername")
        addr = f"{peername[0]}:{peername[1]}" if peername else "unknown"
        logger.info("Network: Incoming peer connection from %s", addr)
        self._peers.append((reader, writer))
        task = asyncio.create_task(self._listen_to_peer(reader, writer, addr))
        self._listen_tasks.append(task)
        await self._notify_peer_connected(writer, "Network: Error during peer sync")

    def _validate_transaction_payload(self, payload):
        if not isinstance(payload, dict):
            return False

        required_fields = {
            "sender": str,
            "amount": int,
            "nonce": int,
            "timestamp": int,
            "signature": str,
        }
        optional_fields = {
            "receiver": (str, type(None)),
            "data": (str, type(None)),
        }
        allowed_fields = set(required_fields) | set(optional_fields)

        if set(payload) != allowed_fields:
            return False

        for field, expected_type in required_fields.items():
            if not isinstance(payload.get(field), expected_type):
                return False

        for field, expected_type in optional_fields.items():
            if not isinstance(payload.get(field), expected_type):
                return False

        if payload["amount"] <= 0:
            return False

        receiver = payload.get("receiver")
        if receiver is not None and not is_valid_receiver(receiver):
            return False

        return True

    def _validate_sync_payload(self, payload):
        if not isinstance(payload, dict) or set(payload) != {"accounts"}:
            return False

        accounts = payload["accounts"]
        if not isinstance(accounts, dict):
            return False

        for address, account in accounts.items():
            if not isinstance(address, str) or not isinstance(account, dict):
                return False
            required = {"balance", "nonce", "code", "storage"}
            if set(account) != required:
                return False
            if not isinstance(account["balance"], int):
                return False
            if not isinstance(account["nonce"], int):
                return False
            if not isinstance(account["code"], (str, type(None))):
                return False
            if not isinstance(account["storage"], dict):
                return False

        return True

    def _validate_block_payload(self, payload):
        if not isinstance(payload, dict):
            return False

        required_fields = {
            "index": int,
            "previous_hash": str,
            "merkle_root": (str, type(None)),
            "transactions": list,
            "timestamp": int,
            "difficulty": (int, type(None)),
            "nonce": int,
            "hash": str,
        }
        optional_fields = {"miner": str}
        allowed_fields = set(required_fields) | set(optional_fields)

        if not set(payload).issubset(allowed_fields):
            return False

        for field, expected_type in required_fields.items():
            if not isinstance(payload.get(field), expected_type):
                return False

        if "miner" in payload and not isinstance(payload["miner"], str):
            return False

        return all(
            self._validate_transaction_payload(tx_payload)
            for tx_payload in payload["transactions"]
        )

    def _validate_message(self, message):
        # FIX: Check if message is a dictionary first to prevent crashes
        if not isinstance(message, dict):
            logger.warning("Network: Received non-dict message")
            return False
        required_fields = {"type", "data"}
        if not required_fields.issubset(set(message)):
            return False
        if not set(message).issubset(required_fields):
            return False

        msg_type = message.get("type")
        payload = message.get("data")

        if msg_type not in SUPPORTED_MESSAGE_TYPES:
            return False

        validators = {
            "sync": self._validate_sync_payload,
            "tx": self._validate_transaction_payload,
            "block": self._validate_block_payload,
        }
        return validators[msg_type](payload)

    def _message_id(self, msg_type, payload):
        if msg_type == "tx":
            return canonical_json_hash(payload)
        if msg_type == "block":
            return payload["hash"]
        return None

    def _mark_seen(self, msg_type, payload):
        message_id = self._message_id(msg_type, payload)
        if message_id is None:
            return
        if msg_type == "tx":
            self._seen_tx_ids.add(message_id)
        elif msg_type == "block":
            self._seen_block_hashes.add(message_id)

    def _is_duplicate(self, msg_type, payload):
        message_id = self._message_id(msg_type, payload)
        if message_id is None:
            return False
        if msg_type == "tx":
            return message_id in self._seen_tx_ids
        if msg_type == "block":
            return message_id in self._seen_block_hashes
        return False

    async def _listen_to_peer(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        addr: str,
    ):
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
                if not self._validate_message(data):
                    logger.warning("Network: Invalid message schema from %s", addr)
                    continue

                msg_type = data["type"]
                payload = data["data"]
                if self._is_duplicate(msg_type, payload):
                    logger.info("Network: Duplicate %s ignored from %s", msg_type, addr)
                    continue
                self._mark_seen(msg_type, payload)
                data["_peer_addr"] = addr

                if self._handler_callback:
                    try:
                        await self._handler_callback(data)
                    except Exception:
                        logger.exception(
                            "Network: Handler error for message from %s", addr
                        )
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

    async def _broadcast_raw(self, payload: dict):
        """Send a JSON message to every connected peer."""
        line = (canonical_json_dumps(payload) + "\n").encode()
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
        except (TypeError, ValueError) as exc:
            logger.error("Network: Failed to serialize tx: %s", exc)
            return
        self._mark_seen("tx", payload["data"])
        await self._broadcast_raw(payload)

    async def broadcast_block(self, block):
        """Broadcast a block. Block must have miner populated."""
        logger.info("Network: Broadcasting Block #%d", block.index)

        # Enforce that the block is fully populated before it enters the network layer
        if getattr(block, "miner", None) is None:
            raise ValueError("block.miner must be populated before broadcasting")

        payload = {
            "type": "block",
            "data": json.loads(block.canonical_payload.decode("utf-8"))
        }

        self._mark_seen("block", payload["data"])
        await self._broadcast_raw(payload)

    @property
    def peer_count(self) -> int:
        return len(self._peers)
