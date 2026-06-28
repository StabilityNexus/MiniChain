"""
Libp2p-based P2P network layer for MiniChain.
Runs libp2p via trio in a background thread to stay compatible with asyncio.
"""

import asyncio
import json
import logging
import threading
import time
import trio
import queue

from libp2p import new_host
TProtocol = str
from libp2p.peer.peerinfo import info_from_p2p_addr
from multiaddr import Multiaddr
from .serialization import canonical_json_hash, canonical_json_dumps
from .validators import ValidationStatus
from .persistence import ban_peer, is_peer_banned

logger = logging.getLogger(__name__)

SUPPORTED_MESSAGE_TYPES = {"hello", "tx", "block", "chain_request", "chain_response"}
PROTOCOL_ID = TProtocol("/minichain/1.0.0")

# Misbehavior thresholds — all four are overridable per P2PNetwork instance.
MALFORMED_THRESHOLD = 15     # N: accumulated malformed messages before ban
FAILED_THRESHOLD = 15        # M: accumulated failed messages before ban
INVALID_THRESHOLD = 1        # L: accumulated invalid messages before ban (1 = immediate)
DECAY_INTERVAL_MINUTES = 10  # T: counter half-life period in minutes


class P2PNetwork:
    """Lightweight peer-to-peer networking using libp2p."""

    def __init__(
        self,
        handler_callback=None,
        data_path: str = ".",
        malformed_threshold: int = MALFORMED_THRESHOLD,
        failed_threshold: int = FAILED_THRESHOLD,
        invalid_threshold: int = INVALID_THRESHOLD,
        decay_interval_minutes: float = DECAY_INTERVAL_MINUTES,
    ):
        self._handler_callback = handler_callback
        self._on_peer_connected = None
        self._seen_tx_ids = set()
        self._seen_block_hashes = set()
        self._to_trio = queue.Queue()
        self._to_asyncio = queue.Queue()
        self._peer_count = 0
        self._peer_count_lock = threading.Lock()

        # Misbehavior tracking
        self.data_path = data_path
        self.malformed_threshold = malformed_threshold
        self.failed_threshold = failed_threshold
        self.invalid_threshold = invalid_threshold
        self.decay_interval_minutes = decay_interval_minutes
        # { peer_id_str -> {"malformed": int, "failed": int, "invalid": int} }
        self._peer_counters: dict = {}

    def register_handler(self, handler_callback):
        self._handler_callback = handler_callback

    def register_on_peer_connected(self, handler_callback):
        self._on_peer_connected = handler_callback

    async def start(self, port: int = 9000, host: str = "127.0.0.1"):
        self.port = port
        self.host_addr = host
        self.loop = asyncio.get_running_loop()

        threading.Thread(target=trio.run, args=(self._trio_main,), daemon=True).start()
        asyncio.create_task(self._asyncio_reader())
        asyncio.create_task(self._decay_counters())
        logger.info(f"Network: Starting libp2p on port {port}")

    async def stop(self):
        logger.info("Network: Shutting down")
        self._to_trio.put(("STOP", None))

    async def connect_to_peer(self, maddr_str: str) -> bool:
        self._to_trio.put(("CONNECT", maddr_str))
        return True

    def _message_id(self, msg_type, payload):
        if msg_type == "tx": return canonical_json_hash(payload)
        if msg_type == "block": return payload["hash"]
        return None

    def _is_duplicate(self, msg_type, payload):
        mid = self._message_id(msg_type, payload)
        if not mid: return False
        return mid in (self._seen_tx_ids if msg_type == "tx" else self._seen_block_hashes)

    def _mark_seen(self, msg_type, payload):
        mid = self._message_id(msg_type, payload)
        if mid: (self._seen_tx_ids if msg_type == "tx" else self._seen_block_hashes).add(mid)

    async def _broadcast_raw(self, payload: dict):
        self._to_trio.put(("BROADCAST", payload))

    async def _unicast_raw(self, target_addr: str, payload: dict):
        self._to_trio.put(("UNICAST", (target_addr, payload)))

    async def broadcast_transaction(self, tx):
        payload = {"type": "tx", "data": tx.to_dict()}
        self._mark_seen("tx", payload["data"])
        await self._broadcast_raw(payload)

    async def broadcast_block(self, block):
        payload = {"type": "block", "data": block.to_dict()}
        self._mark_seen("block", payload["data"])
        await self._broadcast_raw(payload)

    async def broadcast_chain_request(self):
        await self._broadcast_raw({"type": "chain_request", "data": {}})

    async def send_chain_response(self, blocks_dicts, peer_stream=None):
        await self._broadcast_raw({"type": "chain_response", "data": {"blocks": blocks_dicts}})

    async def disconnect_peer(self, peer_addr):
        self._to_trio.put(("DISCONNECT", peer_addr))

    @property
    def peer_count(self) -> int:
        with self._peer_count_lock:
            return self._peer_count

    # ── misbehavior helpers ──────────────────────────────────────────────────

    def _increment_counter(self, peer_id: str, category: str) -> bool:
        """
        Increment the named counter (malformed/failed/invalid) for peer_id.
        Returns True if any counter now meets or exceeds its threshold.
        Called only from the asyncio thread — no lock needed.
        """
        if peer_id not in self._peer_counters:
            self._peer_counters[peer_id] = {"malformed": 0, "failed": 0, "invalid": 0}
        self._peer_counters[peer_id][category] += 1
        counts = self._peer_counters[peer_id]
        return (
            counts["malformed"] >= self.malformed_threshold
            or counts["failed"] >= self.failed_threshold
            or counts["invalid"] >= self.invalid_threshold
        )

    async def _handle_validation_status(
        self, peer_id: str, peer_addr: str, status: ValidationStatus
    ):
        """
        Apply misbehavior policy for a single ValidationStatus event:
          MALFORMED → always disconnect; ban if counter >= N
          FAILED    → drop silently; ban + disconnect if counter >= M
          INVALID   → always ban + disconnect (L=1 means first occurrence triggers)
        """
        if status == ValidationStatus.MALFORMED:
            await self.disconnect_peer(peer_addr)
            if self._increment_counter(peer_id, "malformed"):
                ban_peer(peer_id, reason="malformed_threshold_exceeded", path=self.data_path)
                logger.warning(
                    "Banned peer %s: malformed message threshold (%d) exceeded",
                    peer_id, self.malformed_threshold,
                )

        elif status == ValidationStatus.FAILED:
            if self._increment_counter(peer_id, "failed"):
                ban_peer(peer_id, reason="failed_threshold_exceeded", path=self.data_path)
                await self.disconnect_peer(peer_addr)
                logger.warning(
                    "Banned and disconnected peer %s: failed message threshold (%d) exceeded",
                    peer_id, self.failed_threshold,
                )

        elif status == ValidationStatus.INVALID:
            if self._increment_counter(peer_id, "invalid"):
                ban_peer(peer_id, reason="invalid_threshold_exceeded", path=self.data_path)
                await self.disconnect_peer(peer_addr)
                logger.warning(
                    "Banned and disconnected peer %s: invalid message threshold (%d) exceeded",
                    peer_id, self.invalid_threshold,
                )

    async def _decay_counters(self):
        """
        Half-life decay: every decay_interval_minutes minutes divide all per-peer
        counters by 2 (integer floor division).  Runs for the lifetime of the node.
        """
        interval_seconds = self.decay_interval_minutes * 60
        while True:
            await asyncio.sleep(interval_seconds)
            for counts in self._peer_counters.values():
                counts["malformed"] //= 2
                counts["failed"] //= 2
                counts["invalid"] //= 2

    # ── asyncio reader ───────────────────────────────────────────────────────

    async def _asyncio_reader(self):
        while True:
            try:
                msg = await self.loop.run_in_executor(None, self._to_asyncio.get)
            except Exception:
                continue

            if msg[0] == "MSG":
                data = msg[1]
                msg_type = data.get("type")
                payload = data.get("data")
                peer_addr = data.get("_peer_addr", "")
                peer_id = (
                    peer_addr[len("peer:"):] if peer_addr.startswith("peer:") else peer_addr
                )

                if msg_type not in SUPPORTED_MESSAGE_TYPES or self._is_duplicate(msg_type, payload):
                    continue
                self._mark_seen(msg_type, payload)

                status = None
                if self._handler_callback:
                    status = await self._handler_callback(data)

                # Only apply interception for content-bearing message types.
                if msg_type in ("tx", "block") and status is not None:
                    await self._handle_validation_status(peer_id, peer_addr, status)

            elif msg[0] == "MALFORMED":
                # JSON parse failure signalled from the Trio thread.
                peer_addr = msg[1]
                peer_id = (
                    peer_addr[len("peer:"):] if peer_addr.startswith("peer:") else peer_addr
                )
                await self._handle_validation_status(peer_id, peer_addr, ValidationStatus.MALFORMED)

            elif msg[0] == "PEER_CONNECTED":
                class MockWriter:
                    def write(self, data): self.data = data
                    async def drain(self): pass
                if self._on_peer_connected:
                    writer = MockWriter()
                    await self._on_peer_connected(writer)
                    if hasattr(writer, "data"):
                        try:
                            req = json.loads(writer.data.decode().strip())
                            await self._broadcast_raw(req)
                        except Exception:
                            pass

    # ── trio main ────────────────────────────────────────────────────────────

    async def _trio_main(self):
        host = new_host()
        listen_addr = Multiaddr(f"/ip4/{self.host_addr}/tcp/{self.port}")
        await host.get_network().listen(listen_addr)
        print(f"  Network Multiaddr: {listen_addr}/p2p/{host.get_id().to_string()}")

        streams = []

        async def stream_handler(stream):
            peer_id = str(stream.muxed_conn.peer_id)
            addr = f"peer:{peer_id}"

            # Reject banned peers before doing anything else.
            if is_peer_banned(peer_id, path=self.data_path):
                logger.warning("Rejected connection from banned peer %s", peer_id)
                try:
                    await stream.reset()
                except Exception:
                    pass
                return

            streams.append(stream)
            with self._peer_count_lock:
                self._peer_count += 1
            self._to_asyncio.put(("PEER_CONNECTED", None))

            try:
                while True:
                    data = await stream.read(4096)
                    if not data:
                        break
                    for line in data.split(b"\n"):
                        if not line:
                            continue
                        try:
                            parsed = json.loads(line.decode().strip())
                            parsed["_peer_addr"] = addr
                            self._to_asyncio.put(("MSG", parsed))
                        except Exception:
                            # Signal the asyncio side to apply MALFORMED policy.
                            self._to_asyncio.put(("MALFORMED", addr))
            except Exception:
                pass

            if stream in streams:
                streams.remove(stream)
                with self._peer_count_lock:
                    self._peer_count -= 1

        host.set_stream_handler(PROTOCOL_ID, stream_handler)

        async def check_queue():
            while True:
                try:
                    while not self._to_trio.empty():
                        cmd, arg = self._to_trio.get_nowait()
                        if cmd == "STOP":
                            return True
                        elif cmd == "CONNECT":
                            try:
                                maddr = Multiaddr(arg)
                                info = info_from_p2p_addr(maddr)
                                await host.connect(info)
                                stream = await host.new_stream(info.peer_id, [PROTOCOL_ID])
                                host.get_network().nursery.start_soon(stream_handler, stream)
                            except Exception as e:
                                logger.error(f"Dial error: {e}")
                        elif cmd == "BROADCAST":
                            msg = (canonical_json_dumps(arg) + "\n").encode()
                            for s in list(streams):
                                try:
                                    await s.write(msg)
                                except Exception:
                                    pass
                        elif cmd == "UNICAST":
                            target_addr, payload = arg
                            msg = (canonical_json_dumps(payload) + "\n").encode()
                            for s in list(streams):
                                s_addr = f"peer:{s.muxed_conn.peer_id}"
                                if s_addr == target_addr:
                                    try:
                                        await s.write(msg)
                                    except Exception:
                                        pass
                        elif cmd == "DISCONNECT":
                            for s in list(streams):
                                s_addr = f"peer:{s.muxed_conn.peer_id}"
                                if s_addr == arg:
                                    try:
                                        await s.reset()
                                    except Exception:
                                        pass
                                    if s in streams:
                                        streams.remove(s)
                                        with self._peer_count_lock:
                                            self._peer_count -= 1
                except Exception:
                    pass
                await trio.sleep(0.1)

        async with trio.open_nursery() as nursery:
            async def run_monitor():
                if await check_queue():
                    await host.close()
                    nursery.cancel_scope.cancel()
            nursery.start_soon(run_monitor)
