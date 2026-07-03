"""
Libp2p-based P2P network layer for MiniChain.
Runs libp2p via trio in a background thread to stay compatible with asyncio.
"""

import asyncio
import json
import logging
import threading
import trio
import queue

from libp2p import new_host
TProtocol = str
from libp2p.peer.peerinfo import info_from_p2p_addr
from multiaddr import Multiaddr
from .serialization import canonical_json_hash, canonical_json_dumps

logger = logging.getLogger(__name__)

SUPPORTED_MESSAGE_TYPES = {"hello", "tx", "block", "chain_request", "chain_response"}
PROTOCOL_ID = TProtocol("/minichain/1.0.0")

class P2PNetwork:
    """Lightweight peer-to-peer networking using libp2p."""

    def __init__(self, handler_callback=None):
        self._handler_callback = handler_callback
        self._on_peer_connected = None
        self._seen_tx_ids = set()
        self._seen_block_hashes = set()
        self._to_trio = queue.Queue()
        self._to_asyncio = queue.Queue()
        self._peer_count = 0
        self._peer_count_lock = threading.Lock()

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
        logger.info(f"Network: Starting libp2p on port {port}")

    async def stop(self):
        logger.info("Network: Shutting down")
        self._to_trio.put(("STOP", None))

    async def connect_to_peer(self, maddr_str: str) -> bool:
        self._to_trio.put(("CONNECT", maddr_str))
        return True

    def _get_seen_set_and_id(self, msg_type, payload):
        if msg_type == "tx":
            return self._seen_tx_ids, canonical_json_hash(payload)
        elif msg_type == "block":
            return self._seen_block_hashes, payload.get("hash")
        return None, None

    def _is_duplicate(self, msg_type, payload):
        seen_set, mid = self._get_seen_set_and_id(msg_type, payload)
        return mid in seen_set if mid else False

    def _mark_seen(self, msg_type, payload):
        seen_set, mid = self._get_seen_set_and_id(msg_type, payload)
        if mid:
            seen_set.add(mid)

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



    async def disconnect_peer(self, peer_addr):
        self._to_trio.put(("DISCONNECT", peer_addr))

    @property
    def peer_count(self) -> int:
        with self._peer_count_lock:
            return self._peer_count

    async def _asyncio_reader(self):
        while True:
            try: msg = await self.loop.run_in_executor(None, self._to_asyncio.get)
            except Exception: continue
            
            if msg[0] == "MSG":
                data = msg[1]
                msg_type, payload = data.get("type"), data.get("data")
                if msg_type not in SUPPORTED_MESSAGE_TYPES or self._is_duplicate(msg_type, payload): continue
                self._mark_seen(msg_type, payload)
                if self._handler_callback: await self._handler_callback(data)
            elif msg[0] == "PEER_CONNECTED":
                class MockWriter:
                    def write(self, data): self.data = data
                    async def drain(self): pass
                if self._on_peer_connected:
                    writer = MockWriter()
                    await self._on_peer_connected(writer)
                    if hasattr(writer, 'data'):
                        try:
                            req = json.loads(writer.data.decode().strip())
                            await self._broadcast_raw(req)
                        except Exception: pass

    async def _trio_main(self):
        host = new_host()
        listen_addr = Multiaddr(f"/ip4/{self.host_addr}/tcp/{self.port}")
        await host.get_network().listen(listen_addr)
        print(f"  Network Multiaddr: {listen_addr}/p2p/{host.get_id().to_string()}")
        
        streams = []

        async def stream_handler(stream):
            streams.append(stream)
            with self._peer_count_lock:
                self._peer_count += 1
            peer_id = stream.muxed_conn.peer_id
            addr = f"peer:{peer_id}"
            self._to_asyncio.put(("PEER_CONNECTED", None))
            try:
                while True:
                    data = await stream.read(4096)
                    if not data: break
                    for line in data.split(b'\n'):
                        if not line: continue
                        try:
                            msg = json.loads(line.decode().strip())
                            msg["_peer_addr"] = addr
                            self._to_asyncio.put(("MSG", msg))
                        except Exception: pass
            except Exception: pass
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
                        if cmd == "STOP": return True
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
                                try: await s.write(msg)
                                except Exception: pass
                        elif cmd == "UNICAST":
                            target_addr, payload = arg
                            msg = (canonical_json_dumps(payload) + "\n").encode()
                            for s in list(streams):
                                addr = f"peer:{s.muxed_conn.peer_id}"
                                if addr == target_addr:
                                    try: await s.write(msg)
                                    except Exception: pass
                        elif cmd == "DISCONNECT":
                            for s in list(streams):
                                addr = f"peer:{s.muxed_conn.peer_id}"
                                if addr == arg:
                                    try: await s.reset()
                                    except Exception: pass
                                    if s in streams:
                                        streams.remove(s)
                                        with self._peer_count_lock:
                                            self._peer_count -= 1
                except Exception: pass
                await trio.sleep(0.1)

        async with trio.open_nursery() as nursery:
            async def run_monitor():
                if await check_queue():
                    await host.close()
                    nursery.cancel_scope.cancel()
            nursery.start_soon(run_monitor)
