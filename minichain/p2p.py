"""
Libp2p-based P2P network layer for MiniChain.
Runs libp2p via trio in a background thread to stay compatible with asyncio.
"""

import asyncio
import json
import logging
import threading
import time
import struct
import trio
import queue
from collections import OrderedDict

from .node_config import (
    MALFORMED_THRESHOLD, FAILED_THRESHOLD, INVALID_THRESHOLD, DECAY_INTERVAL_MINUTES,
    SEEN_CACHE_MAX,
)
from .network_config import SUPPORTED_MESSAGE_TYPES, PROTOCOL_ID, MAX_FRAME_BYTES

from libp2p import new_host
TProtocol = str
from libp2p.discovery.events.peerDiscovery import peerDiscovery
from libp2p.discovery.mdns.mdns import MDNSDiscovery
from libp2p.peer.id import ID
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.relay.circuit_v2 import CircuitV2Protocol, CircuitV2Transport
from libp2p.relay.circuit_v2.config import RelayConfig, RelayRole
from libp2p.relay.circuit_v2.protocol import PROTOCOL_ID as CIRCUIT_PROTOCOL_ID
from libp2p.tools.async_service import background_trio_service
from multiaddr import Multiaddr
from .serialization import canonical_json_hash, canonical_json_dumps
from .validators import ValidationStatus
from .persistence import ban_peer, is_peer_banned, remember_peer, get_known_peers
from .identity import load_or_create_keypair

logger = logging.getLogger(__name__)


def _peer_id_from_addr(peer_addr: str) -> str:
    """Strip the "peer:" prefix from a peer address, if present."""
    prefix = "peer:"
    return peer_addr[len(prefix):] if peer_addr.startswith(prefix) else peer_addr


# (Constants moved to node_config.py and network_config.py)


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
        # OrderedDict as an LRU: unbounded sets here would let a live node accumulate
        # tx/block ids forever, and since relaying now depends on this set to stop
        # echoes circulating, its size is no longer just a memory concern.
        self._seen_tx_ids = OrderedDict()
        self._seen_block_hashes = OrderedDict()
        self._to_trio = queue.Queue()
        self._to_asyncio = queue.Queue()
        self._peer_count = 0
        self._peer_count_lock = threading.Lock()
        self._peer_ids: set = set()
        self._peer_id = None  # our own peer id, set once _trio_main starts the host

        # Misbehavior tracking, keyed directly by ValidationStatus so there is a
        # single vocabulary for statuses (no parallel string keys to convert).
        self.data_path = data_path
        self.thresholds = {
            ValidationStatus.MALFORMED: malformed_threshold,
            ValidationStatus.FAILED: failed_threshold,
            ValidationStatus.INVALID: invalid_threshold,
        }
        self.decay_interval_minutes = decay_interval_minutes
        # { peer_id_str -> { ValidationStatus -> int } }
        self._peer_counters: dict = {}

        if self.decay_interval_minutes <= 0:
            raise ValueError(f"decay_interval_minutes must be positive, got {self.decay_interval_minutes}")
        for status, value in self.thresholds.items():
            if value <= 0:
                raise ValueError(f"{status.name.lower()}_threshold must be positive, got {value}")

    def register_handler(self, handler_callback):
        self._handler_callback = handler_callback

    def register_on_peer_connected(self, handler_callback):
        self._on_peer_connected = handler_callback

    async def start(
        self,
        port: int = 9000,
        host: str = "127.0.0.1",
        *,
        upnp: bool = False,
        bootstrap: list | None = None,
        relay: bool = False,
        relay_addr: str | None = None,
        announce: str | None = None,
        datadir: str | None = None,
    ):
        """
        Bring the node online.

        Reaching a node on another network needs its address to be dialable, which
        this handles in three escalating ways: mDNS finds peers on the same LAN with
        no addresses typed at all, `upnp` asks the router for a port mapping, and
        `relay`/`relay_addr` route through another MiniChain node when neither works.
        `bootstrap` is discovery rather than reachability — a list of peers to dial
        on startup instead of typing `connect`. `announce` overrides the address
        printed for others to dial, for when the bind address (e.g. 0.0.0.0 under
        --upnp) is not itself dialable. `datadir` persists this node's identity and
        the peers it has connected to, so both survive a restart.
        """
        self.port = port
        self.host_addr = host
        self.upnp = upnp
        self.bootstrap = bootstrap or []
        self.relay = relay
        self.relay_addr = relay_addr
        self.announce = announce
        self.data_path = datadir or self.data_path
        self.loop = asyncio.get_running_loop()

        # mDNS and bootstrap only put peers in the peerstore and open a transport
        # connection; opening the MiniChain stream on top is our job, so every
        # discovery is routed through the same CONNECT path as a manual dial.
        peerDiscovery.register_peer_discovered_handler(self._on_peer_discovered)

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

    def _on_peer_discovered(self, peer_info):
        """
        Queue dials for a peer announced by mDNS or bootstrap. Runs on the discovery
        thread, so it only touches the thread-safe command queue. Every address is
        queued because any one of them may be unreachable; CONNECT skips a peer we
        already hold a stream to, so the extras cost nothing once one succeeds.

        Both ends see the same discovery event, so only the lower peer ID dials. Left
        to themselves both would dial and the pair would end up with two streams. The
        equal case is our own announcement coming back to us.
        """
        if self._peer_id is None or self._peer_id >= peer_info.peer_id.to_string():
            return
        for addr in peer_info.addrs:
            addr_str = str(addr)
            if "/p2p/" not in addr_str:
                addr_str = f"{addr_str}/p2p/{peer_info.peer_id.to_string()}"
            self._to_trio.put(("CONNECT", addr_str))

    def _message_id(self, msg_type, payload):
        if msg_type == "tx": return canonical_json_hash(payload)
        if msg_type == "block": return payload["hash"]
        return None

    def _seen_set(self, msg_type):
        return self._seen_tx_ids if msg_type == "tx" else self._seen_block_hashes

    def _is_duplicate(self, msg_type, payload):
        mid = self._message_id(msg_type, payload)
        return bool(mid) and mid in self._seen_set(msg_type)

    def _mark_seen(self, msg_type, payload):
        mid = self._message_id(msg_type, payload)
        if not mid:
            return
        seen = self._seen_set(msg_type)
        seen[mid] = True
        seen.move_to_end(mid)
        if len(seen) > SEEN_CACHE_MAX:
            seen.popitem(last=False)

    async def _broadcast_raw(self, payload: dict, exclude: str | None = None):
        self._to_trio.put(("BROADCAST", (payload, exclude)))

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

    @property
    def peer_ids(self) -> list:
        with self._peer_count_lock:
            return list(self._peer_ids)

    # ── misbehavior helpers ──────────────────────────────────────────────────

    def _increment_counter(self, peer_id: str, status: ValidationStatus) -> bool:
        """
        Increment peer_id's counter for the given ValidationStatus.
        Returns True if that counter now meets or exceeds its threshold.
        Called only from the asyncio thread — no lock needed.
        """
        counts = self._peer_counters.setdefault(peer_id, {s: 0 for s in self.thresholds})
        counts[status] += 1
        return counts[status] >= self.thresholds[status]

    async def _handle_validation_status(
        self, peer_id: str, peer_addr: str, status: ValidationStatus
    ):
        """
        Apply misbehavior policy for a single ValidationStatus event:
          MALFORMED → always disconnect; ban if counter >= threshold
          FAILED    → drop silently; ban + disconnect if counter >= threshold
          INVALID   → always ban + disconnect (threshold configurable, default=1)
        """
        if status not in self.thresholds:
            return

        exceeded = self._increment_counter(peer_id, status)

        if exceeded:
            ban_peer(peer_id, reason=f"{status.name.lower()}_threshold_exceeded", path=self.data_path)
            logger.warning(
                "Banned peer %s: %s threshold (%d) exceeded",
                peer_id, status.name.lower(), self.thresholds[status],
            )

        always_disconnect = status in (ValidationStatus.MALFORMED, ValidationStatus.INVALID)
        if always_disconnect or exceeded:
            await self.disconnect_peer(peer_addr)

    async def _decay_counters(self):
        """
        Half-life decay: every decay_interval_minutes minutes divide all per-peer
        counters by 2 (integer floor division).  Runs for the lifetime of the node.
        """
        interval_seconds = self.decay_interval_minutes * 60
        while True:
            await asyncio.sleep(interval_seconds)
            for counts in self._peer_counters.values():
                for key in counts:
                    counts[key] //= 2
            self._peer_counters = {
                peer_id: counts
                for peer_id, counts in self._peer_counters.items()
                if any(v > 0 for v in counts.values())
            }

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
                peer_id = _peer_id_from_addr(peer_addr)

                if msg_type not in SUPPORTED_MESSAGE_TYPES:
                    continue
                try:
                    if self._is_duplicate(msg_type, payload):
                        continue
                except Exception:
                    await self._handle_validation_status(peer_id, peer_addr, ValidationStatus.MALFORMED)
                    continue

                status = None
                if self._handler_callback:
                    status = await self._handler_callback(data)

                # Only apply interception for content-bearing message types.
                if msg_type in ("tx", "block") and status is not None:
                    await self._handle_validation_status(peer_id, peer_addr, status)

                if status is None or status == ValidationStatus.VALID:
                    try:
                        self._mark_seen(msg_type, payload)
                    except Exception:
                        pass

                    # Relay accepted content onward, so gossip travels further than one
                    # hop. Only VALID is forwarded: rejected content is never amplified,
                    # and a None status (hello/chain_request/chain_response) is not
                    # relayable at all. Excluding the sender avoids a pointless echo, and
                    # _mark_seen above means an echo arriving from another peer is dropped
                    # as a duplicate, so cycles in the peer graph terminate.
                    if msg_type in ("tx", "block") and status == ValidationStatus.VALID:
                        await self._broadcast_raw(
                            {"type": msg_type, "data": payload}, exclude=peer_addr
                        )

            elif msg[0] == "MALFORMED":
                # JSON parse failure signalled from the Trio thread.
                peer_addr = msg[1]
                peer_id = _peer_id_from_addr(peer_addr)
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
        # A persisted keypair keeps our peer ID stable across restarts. Without it
        # every address a peer saved for us, or that we advertise, goes stale the
        # moment we restart.
        key_pair = load_or_create_keypair(self.data_path)
        host = new_host(
            key_pair=key_pair,
            enable_upnp=self.upnp,
            bootstrap=self.bootstrap,
        )
        # new_host(enable_mDNS=True) builds the discovery with libp2p's default port
        # of 8000 rather than the port we listen on, so peers would find us and then
        # dial nothing. Attaching it ourselves is the only way to advertise the real
        # port; host.run() starts whatever is set here.
        host.mDNS = MDNSDiscovery(host.get_network(), port=self.port)
        self._peer_id = host.get_id().to_string()
        listen_addr = Multiaddr(f"/ip4/{self.host_addr}/tcp/{self.port}")
        # Swarm.listen() waits on a nursery that only exists while the swarm runs as
        # a background service, so listening must go through host.run(). Calling
        # host.get_network().listen() directly blocks forever and the node comes up
        # with no networking at all.
        async with host.run(listen_addrs=[listen_addr]):
            await self._serve(host, listen_addr)

    async def _run_circuit(self, protocol, nursery, host, circuit):
        """
        Run the circuit relay service, and reserve a slot on a relay if one was given.

        A reservation is what makes an unreachable node dialable: the relay agrees to
        accept connections addressed to us and forward them, so peers dial our circuit
        address instead of an address our NAT would drop.
        """
        async with background_trio_service(protocol):
            await protocol.event_started.wait()
            if self.relay_addr:
                try:
                    info = info_from_p2p_addr(Multiaddr(self.relay_addr))
                    await host.connect(info)
                    stream = await host.new_stream(info.peer_id, [CIRCUIT_PROTOCOL_ID])
                    if await circuit.reserve(stream, info.peer_id, nursery):
                        print(
                            f"  Reachable via relay: {self.relay_addr}"
                            f"/p2p-circuit/p2p/{host.get_id().to_string()}"
                        )
                    else:
                        logger.error("Relay %s refused our reservation", info.peer_id)
                except Exception as e:
                    logger.error(f"Relay reservation failed: {e}")
            await trio.sleep_forever()

    async def _serve(self, host, listen_addr):
        # The bind address is not necessarily dialable (0.0.0.0, a NAT'd LAN IP), so
        # --announce lets the operator substitute the address peers should actually use.
        advertise_addr = listen_addr
        if self.announce:
            try:
                announce_host, announce_port = self.announce.rsplit(":", 1)
                advertise_addr = Multiaddr(f"/ip4/{announce_host}/tcp/{announce_port}")
            except Exception:
                logger.warning("Ignoring malformed --announce %r, expected host:port", self.announce)
        print(f"  Network Multiaddr: {advertise_addr}/p2p/{host.get_id().to_string()}")

        streams = []
        circuit = None
        # Peers we have dialed but whose stream is not in `streams` yet: the reader
        # task that registers it only starts on the next scheduler pass, so without
        # this a peer announcing several addresses gets dialed once per address.
        dialing = set()
        # Redial peers we dialed ourselves when their stream drops. Only populated for
        # outbound connections: an inbound one never reveals an address to redial with.
        dialed_addrs: dict = {}
        redial_backoff: dict = {}
        REDIAL_BACKOFF_MAX = 300  # seconds

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
                self._peer_ids.add(peer_id)
            self._to_asyncio.put(("PEER_CONNECTED", None))

            try:
                buffer = b""
                while True:
                    data = await stream.read(4096)
                    if not data:
                        break
                    buffer += data
                    if len(buffer) > MAX_FRAME_BYTES:
                        self._to_asyncio.put(("MALFORMED", addr))
                        break
                    *lines, buffer = buffer.split(b"\n")
                    for line in lines:
                        if not line.strip():
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

            dialing.discard(stream.muxed_conn.peer_id)
            if stream in streams:
                streams.remove(stream)
                with self._peer_count_lock:
                    self._peer_count -= 1
                    self._peer_ids.discard(peer_id)

            # Redial a peer we dialed ourselves once its stream drops, with backoff so a
            # peer that is genuinely gone does not get hammered. The task ends right
            # after, so sleeping here does not block anything else.
            redial_addr = dialed_addrs.pop(stream.muxed_conn.peer_id, None)
            if redial_addr is not None and not is_peer_banned(peer_id, path=self.data_path):
                delay = redial_backoff.get(stream.muxed_conn.peer_id, 1.0)
                redial_backoff[stream.muxed_conn.peer_id] = min(delay * 2, REDIAL_BACKOFF_MAX)
                await trio.sleep(delay)
                self._to_trio.put(("CONNECT", redial_addr))

        host.set_stream_handler(PROTOCOL_ID, stream_handler)

        # Redial peers we have connected to before, so a restart with no --connect
        # or --bootstrap still rejoins the network it was already part of. Same
        # CONNECT path as a manual dial, so the in-flight dedup above applies.
        for known in get_known_peers(self.data_path):
            self._to_trio.put(("CONNECT", known["multiaddr"]))

        async def check_queue(nursery):
            while True:
                try:
                    while not self._to_trio.empty():
                        cmd, arg = self._to_trio.get_nowait()
                        if cmd == "STOP":
                            return True
                        elif cmd == "CONNECT":
                            peer_id = None
                            try:
                                # The peer we end up talking to is always the last
                                # /p2p/ hop, whether the address is direct or routed
                                # through a relay.
                                peer_id = ID.from_base58(arg.split("/p2p/")[-1])
                                if peer_id in dialing or any(
                                    s.muxed_conn.peer_id == peer_id for s in streams
                                ):
                                    continue
                                dialing.add(peer_id)
                                maddr = Multiaddr(arg)
                                if "/p2p-circuit" in arg:
                                    if circuit is None:
                                        raise ValueError("node was not started with relay support")
                                    await circuit.dial(maddr)
                                else:
                                    await host.connect(info_from_p2p_addr(maddr))
                                stream = await host.new_stream(peer_id, [PROTOCOL_ID])
                                # Read the outbound stream in our own nursery: the swarm
                                # exposes no nursery to borrow, and without this the
                                # dialing side never registers the peer or reads from it.
                                nursery.start_soon(stream_handler, stream)
                                # Only the dialer ends up with a dialable address for the
                                # peer — an inbound connection reveals nothing but an
                                # ephemeral remote port — so this is the only place a
                                # peer can be remembered for a future redial.
                                remember_peer(peer_id.to_string(), arg, self.data_path)
                                dialed_addrs[peer_id] = arg
                                redial_backoff.pop(peer_id, None)
                            except Exception as e:
                                dialing.discard(peer_id)
                                logger.error(f"Dial error: {e}")
                        elif cmd == "BROADCAST":
                            payload, exclude = arg
                            msg = (canonical_json_dumps(payload) + "\n").encode()
                            for s in list(streams):
                                if exclude and f"peer:{s.muxed_conn.peer_id}" == exclude:
                                    continue
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
                                            self._peer_ids.discard(str(s.muxed_conn.peer_id))
                except Exception:
                    pass
                await trio.sleep(0.1)

        async with trio.open_nursery() as nursery:
            # STOP and CLIENT are always on so that any node can accept a relayed
            # connection and dial a /p2p-circuit address without extra flags. HOP is
            # the one that costs something — it lets others push traffic through us —
            # so it stays opt-in.
            roles = RelayRole.STOP | RelayRole.CLIENT
            if self.relay:
                roles |= RelayRole.HOP
            config = RelayConfig(roles=roles)
            protocol = CircuitV2Protocol(host, limits=config.limits, allow_hop=self.relay)
            circuit = CircuitV2Transport(host, protocol, config)
            nursery.start_soon(self._run_circuit, protocol, nursery, host, circuit)

            async def run_monitor():
                if await check_queue(nursery):
                    await host.close()
                    nursery.cancel_scope.cancel()
            nursery.start_soon(run_monitor)
