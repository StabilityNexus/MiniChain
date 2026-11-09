"""Headers-first block synchronization.

The node used to ask for a range of full blocks and only discover an invalid
chain after downloading every body. This module separates the cheap consensus
checks (header hash, linkage, proof-of-work, timestamp) from body download, so a
peer must prove its chain is plausible and heavier before we request blocks.

The protocol is intentionally small:

    getheaders  -> headers
    getblocks   -> blocks

A locator is a list of recent block hashes, newest first. The responding node
finds the first hash it knows and returns the headers that follow it. Bodies are
then requested by hash in bounded batches. This is the same shape as Bitcoin's
headers-first sync, without trying to reproduce the rest of Bitcoin's protocol.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from .block import Block, BlockHeader
from .network_config import (
    MAX_FRAME_BYTES,
    MAX_FUTURE_BLOCK_TIME_MS,
    MAX_TARGET,
    MIN_TARGET,
)
from .node_config import (
    MAX_BLOCKS_PER_REQUEST,
    MAX_FORK_HEADERS,
    MAX_HEADERS_PER_REQUEST,
    MAX_LOCATOR_HASHES,
    MAX_SYNC_REQUEST_ID_BYTES,
    MAX_SYNC_RESPONSE_BYTES,
    SYNC_RATE_LIMIT_MAX_PEERS,
    SYNC_REQUEST_BURST,
    SYNC_REQUEST_MAX_RETRIES,
    SYNC_REQUEST_RETRY_SECONDS,
    SYNC_REQUEST_TIMEOUT_SECONDS,
    SYNC_REQUESTS_PER_SECOND,
)
from .serialization import canonical_json_dumps
from .validators import ValidationStatus

logger = logging.getLogger(__name__)

SYNC_MESSAGE_TYPES = frozenset({"getheaders", "headers", "getblocks", "blocks"})


class SyncError(ValueError):
    """A peer sent a sync message that cannot be processed safely."""

    def __init__(
        self, message: str, status: ValidationStatus = ValidationStatus.MALFORMED
    ):
        super().__init__(message)
        self.status = status


class PeerRateLimiter:
    """Small token bucket, one per peer, for expensive sync requests."""

    def __init__(
        self,
        rate_per_second: float = SYNC_REQUESTS_PER_SECOND,
        burst: int = SYNC_REQUEST_BURST,
        clock: Callable[[], float] = time.monotonic,
        max_peers: int = SYNC_RATE_LIMIT_MAX_PEERS,
    ):
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        if max_peers <= 0:
            raise ValueError("max_peers must be positive")
        self.rate_per_second = float(rate_per_second)
        self.burst = float(burst)
        self.max_peers = max_peers
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, peer_addr: str) -> bool:
        now = self._clock()
        if peer_addr not in self._buckets and len(self._buckets) >= self.max_peers:
            # Buckets that have refilled to the burst are indistinguishable
            # from absent ones, so dropping them cannot change any active
            # limit. If every retained bucket is still in use, refuse the new
            # peer rather than evicting one and resetting a live rate limit.
            self._prune(now)
            if len(self._buckets) >= self.max_peers:
                return False
        tokens, last_seen = self._buckets.get(peer_addr, (self.burst, now))
        elapsed = max(0.0, now - last_seen)
        tokens = min(self.burst, tokens + elapsed * self.rate_per_second)
        if tokens < 1.0:
            self._buckets[peer_addr] = (tokens, now)
            return False
        self._buckets[peer_addr] = (tokens - 1.0, now)
        return True

    def prune(self) -> None:
        """Drop buckets that have refilled completely, bounding retained state."""
        self._prune(self._clock())

    def retry_after(self, peer_addr: str) -> float:
        """Seconds until this peer has a token, or zero if one is available."""
        now = self._clock()
        tokens, last_seen = self._buckets.get(peer_addr, (self.burst, now))
        tokens = min(
            self.burst,
            tokens + max(0.0, now - last_seen) * self.rate_per_second,
        )
        if tokens >= 1.0:
            return 0.0
        return (1.0 - tokens) / self.rate_per_second

    def _prune(self, now: float) -> None:
        refilled = [
            peer
            for peer, (tokens, last_seen) in self._buckets.items()
            if tokens + (now - last_seen) * self.rate_per_second >= self.burst
        ]
        for peer in refilled:
            self._buckets.pop(peer, None)


@dataclass
class _PendingRequest:
    peer_addr: str
    kind: str
    created_at: float
    expires_at: float = 0.0
    attempts: int = 1


@dataclass
class _DeferredRequest:
    message: dict
    not_before: float


@dataclass
class _SyncSession:
    peer_addr: str
    headers: list[BlockHeader]
    common_ancestor: Block
    fork: bool
    work: int = 0
    local_tip: str | None = None
    next_index: int = 0
    blocks: list[Block] = field(default_factory=list)


def _encoded_size(payload) -> int:
    """Serialized size in bytes, matching the P2P layer's UTF-8 framing."""
    return len(canonical_json_dumps(payload).encode("utf-8"))


def _bounded_int(value, *, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise SyncError("boolean is not a valid integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise SyncError("invalid integer value") from exc
    return max(minimum, min(maximum, parsed))


def _validate_hashes(value, *, maximum: int, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise SyncError(f"{field_name} must be a list")
    if len(value) > maximum:
        raise SyncError(f"{field_name} exceeds the maximum of {maximum}")
    if not all(isinstance(item, str) and item for item in value):
        raise SyncError(f"{field_name} must contain non-empty strings")
    return value


def _total_work(blocks: Sequence[Block | BlockHeader]) -> int:
    return sum((1 << 256) // block.target for block in blocks)


def validate_header_chain(
    headers: Sequence[BlockHeader],
    start_block: Block,
    *,
    now_ms: int | None = None,
) -> None:
    """Validate a contiguous header range that starts after ``start_block``.

    This deliberately mirrors the cheap checks in ``chain.add_block``. The
    expected difficulty for each height is still enforced when the bodies are
    applied: MiniChain does not persist the EMA value per block, so a fork's
    target sequence cannot be reconstructed without replaying state. Header
    validation still stops bad linkage, bad proof-of-work and timestamps before
    any body is downloaded.
    """

    previous: Block | BlockHeader = start_block
    max_allowed_time = (
        int(time.time() * 1000) if now_ms is None else now_ms
    ) + MAX_FUTURE_BLOCK_TIME_MS

    for header in headers:
        if header.hash is None:
            raise SyncError("header is missing its hash")
        if header.index != previous.index + 1:
            raise SyncError(
                f"invalid header index {header.index}, expected {previous.index + 1}"
            )
        if header.previous_hash != previous.hash:
            raise SyncError(
                f"invalid previous hash {header.previous_hash} != {previous.hash}"
            )
        if header.hash != header.compute_hash():
            raise SyncError(f"invalid header hash {header.hash}")
        if not isinstance(header.target, int) or not (
            MIN_TARGET <= header.target <= MAX_TARGET
        ):
            raise SyncError(f"invalid target {header.target}")
        try:
            hash_value = int(header.hash, 16)
        except ValueError as exc:
            raise SyncError(f"invalid header hash {header.hash}") from exc
        if hash_value >= header.target:
            raise SyncError(
                f"invalid proof of work: hash {header.hash} does not satisfy target {header.target}"
            )
        if header.timestamp <= previous.timestamp:
            raise SyncError(
                f"invalid timestamp {header.timestamp} <= previous {previous.timestamp}"
            )
        if header.timestamp > max_allowed_time:
            raise SyncError(f"timestamp {header.timestamp} is too far in the future")
        previous = header


class SyncManager:
    """Drives one headers-first sync per peer and answers sync requests."""

    def __init__(
        self,
        chain,
        send: Callable[[str, dict], None],
        *,
        on_block_applied: Callable[[Block], None] | None = None,
        on_orphans: Callable[[Sequence], None] | None = None,
        max_headers: int = MAX_HEADERS_PER_REQUEST,
        max_blocks: int = MAX_BLOCKS_PER_REQUEST,
        max_response_bytes: int = MAX_SYNC_RESPONSE_BYTES,
        locator_size: int = MAX_LOCATOR_HASHES,
        max_fork_headers: int = MAX_FORK_HEADERS,
        max_request_id_bytes: int = MAX_SYNC_REQUEST_ID_BYTES,
        request_timeout: float = SYNC_REQUEST_TIMEOUT_SECONDS,
        retry_seconds: float = SYNC_REQUEST_RETRY_SECONDS,
        max_retries: int = SYNC_REQUEST_MAX_RETRIES,
        rate_limiter: PeerRateLimiter | None = None,
        outgoing_rate_limiter: PeerRateLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
        request_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ):
        if max_headers <= 0 or max_blocks <= 0:
            raise ValueError("sync batch sizes must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if locator_size <= 0:
            raise ValueError("locator_size must be positive")
        if max_fork_headers <= 0:
            raise ValueError("max_fork_headers must be positive")
        if max_request_id_bytes <= 0:
            raise ValueError("max_request_id_bytes must be positive")
        if request_timeout <= 0 or retry_seconds <= 0 or max_retries < 0:
            raise ValueError("invalid sync retry policy")
        self.chain = chain
        self._send = send
        self._on_block_applied = on_block_applied
        self._on_orphans = on_orphans
        self.max_headers = max_headers
        self.max_blocks = max_blocks
        self.max_response_bytes = max_response_bytes
        self.locator_size = locator_size
        self.max_fork_headers = max_fork_headers
        self.max_request_id_bytes = max_request_id_bytes
        self.request_timeout = request_timeout
        self.retry_seconds = retry_seconds
        self.max_retries = max_retries
        self._rate_limiter = rate_limiter or PeerRateLimiter(clock=clock)
        # Request pacing is separate from the inbound limiter: one bounds what
        # we serve, the other keeps our requests inside the peer's budget.
        self._outgoing_rate_limiter = outgoing_rate_limiter or PeerRateLimiter(
            clock=clock
        )
        self._clock = clock
        self._request_id_factory = request_id_factory
        self._pending: dict[str, _PendingRequest] = {}
        self._deferred: dict[str, _DeferredRequest] = {}
        self._sessions: dict[str, _SyncSession] = {}
        # Cached hash -> block/position maps for the current main chain. They
        # are extended as blocks are appended and rebuilt after a reorg.
        self._hash_index: dict[str, Block] = {}
        self._hash_positions: dict[str, int] = {}
        self._indexed_length = 0
        self._indexed_tip: str | None = None

    @staticmethod
    def handles(message_type: str) -> bool:
        return message_type in SYNC_MESSAGE_TYPES

    # ── outbound sync ────────────────────────────────────────────────────────

    def request_sync(self, peer_addr: str) -> None:
        """Ask a peer for headers that follow our current best block."""
        # Expire stale requests first: otherwise a request that timed out (for
        # example because the peer rate-limited it) blocks every retry until
        # some unrelated message happens to arrive.
        self._prune_pending()
        if peer_addr in self._sessions:
            return
        if self._has_pending(peer_addr, "headers"):
            return
        self._send_headers_request(peer_addr)

    def allow_request(self, peer_addr: str) -> bool:
        """Apply the per-peer sync request budget to any sync-style request."""
        return self._rate_limiter.allow(peer_addr)

    def maintain(self) -> None:
        """Bound limiter state and recover requests that were never answered."""
        self._rate_limiter.prune()
        self._outgoing_rate_limiter.prune()
        self._prune_pending()
        self._flush_deferred()

    def _has_pending(self, peer_addr: str, kind: str) -> bool:
        return any(
            pending.peer_addr == peer_addr and pending.kind == kind
            for pending in self._pending.values()
        )

    def _send_headers_request(
        self,
        peer_addr: str,
        *,
        locator: Sequence[str] | None = None,
        attempts: int = 1,
        expires_at: float | None = None,
    ) -> None:
        now = self._clock()
        request_id = self._new_request_id()
        self._pending[request_id] = _PendingRequest(
            peer_addr=peer_addr,
            kind="headers",
            created_at=now,
            expires_at=(
                now + self.request_timeout if expires_at is None else expires_at
            ),
            attempts=attempts,
        )
        self._dispatch_sync(
            peer_addr,
            {
                "type": "getheaders",
                "data": {
                    "request_id": request_id,
                    "locator": list(locator) if locator is not None else self.locator(),
                    "max": self.max_headers,
                },
            },
        )

    def _dispatch_sync(self, peer_addr: str, message: dict) -> None:
        """Send a sync request, or queue it until the peer has a token."""
        if self._outgoing_rate_limiter.allow(peer_addr):
            self._deferred.pop(peer_addr, None)
            self._send(peer_addr, message)
            return
        delay = self._outgoing_rate_limiter.retry_after(peer_addr)
        self._deferred[peer_addr] = _DeferredRequest(
            message=message,
            not_before=self._clock() + delay,
        )
        logger.debug("Deferring %s to %s for %.3fs", message["type"], peer_addr, delay)

    def _flush_deferred(self) -> None:
        now = self._clock()
        for peer_addr, deferred in list(self._deferred.items()):
            if now < deferred.not_before:
                continue
            if not self._outgoing_rate_limiter.allow(peer_addr):
                deferred.not_before = now + self._outgoing_rate_limiter.retry_after(
                    peer_addr
                )
                continue
            self._deferred.pop(peer_addr, None)
            self._send(peer_addr, deferred.message)

    def locator(self) -> list[str]:
        """Return recent block hashes, newest first, with exponential spacing."""
        chain = self.chain.chain
        if not chain:
            return []

        hashes: list[str] = []
        index = len(chain) - 1
        step = 1
        while index >= 0 and len(hashes) < self.locator_size:
            hashes.append(chain[index].hash)
            if len(hashes) >= 10:
                step *= 2
            index -= step

        genesis_hash = chain[0].hash
        if genesis_hash not in hashes:
            if len(hashes) >= self.locator_size:
                hashes[-1] = genesis_hash
            else:
                hashes.append(genesis_hash)
        return hashes

    # ── inbound message handling ─────────────────────────────────────────────

    def handle_message(
        self, peer_addr: str, message_type: str, payload
    ) -> ValidationStatus | None:
        """Handle one sync message. Returns a validation status for P2P scoring."""
        self._prune_pending()
        if not isinstance(payload, dict):
            return ValidationStatus.MALFORMED

        if message_type in ("getheaders", "getblocks"):
            if not self.allow_request(peer_addr):
                # A request can be dropped during a fast, honest bulk sync
                # (for example after the burst is exhausted), so do not score
                # the requester as failed. The client retries unanswered
                # requests through maintain().
                logger.warning("Rate-limited sync request from %s", peer_addr)
                return None
            try:
                response = (
                    self.build_headers_response(payload)
                    if message_type == "getheaders"
                    else self.build_blocks_response(payload)
                )
            except SyncError as exc:
                logger.warning("Rejected %s from %s: %s", message_type, peer_addr, exc)
                return exc.status
            self._send(peer_addr, response)
            return None

        if message_type == "headers":
            try:
                return self._handle_headers(peer_addr, payload)
            except SyncError as exc:
                logger.warning("Rejected headers from %s: %s", peer_addr, exc)
                self._drop_session(peer_addr)
                return exc.status

        if message_type == "blocks":
            try:
                return self._handle_blocks(peer_addr, payload)
            except SyncError as exc:
                logger.warning("Rejected blocks from %s: %s", peer_addr, exc)
                self._drop_session(peer_addr)
                return exc.status

        return ValidationStatus.MALFORMED

    def build_headers_response(self, payload: dict) -> dict:
        locator = _validate_hashes(
            payload.get("locator"),
            maximum=self.locator_size,
            field_name="locator",
        )
        max_headers = _bounded_int(
            payload.get("max"),
            default=self.max_headers,
            minimum=1,
            maximum=self.max_headers,
        )
        stop_hash = payload.get("stop_hash")
        if stop_hash is not None and not isinstance(stop_hash, str):
            raise SyncError("stop_hash must be a string or null")
        request_id = self._validated_request_id(payload)

        start_block = self.find_common_ancestor(locator)
        start_index = self._index_of(start_block) if start_block is not None else -1
        budget = self._payload_budget("headers", "headers", request_id)
        used = 0
        headers: list[dict] = []
        for block in self.chain.chain[start_index + 1 : start_index + 1 + max_headers]:
            if stop_hash is not None and block.hash == stop_hash:
                break
            header = BlockHeader.from_block(block).to_dict()
            size = _encoded_size(header) + 1
            if used + size > budget:
                break
            headers.append(header)
            used += size

        return self._with_request_id(
            {"type": "headers", "data": {"headers": headers}}, request_id
        )

    def build_blocks_response(self, payload: dict) -> dict:
        hashes = _validate_hashes(
            payload.get("hashes"),
            maximum=self.max_blocks,
            field_name="hashes",
        )
        request_id = self._validated_request_id(payload)
        by_hash = self._chain_hash_index()[0]
        selected = []
        for block_hash in hashes:
            block = by_hash.get(block_hash)
            if block is None:
                # Stop at the first gap: the requester applies a contiguous
                # prefix of its requested batch.
                break
            selected.append(block)

        return self._with_request_id(
            {
                "type": "blocks",
                "data": {
                    "blocks": self._bounded_block_payloads(
                        selected, response_type="blocks", request_id=request_id
                    )
                },
            },
            request_id,
        )

    def build_legacy_blocks_response(self, start_index: int, limit: int) -> dict:
        """Build a bounded ``chain_response`` for pre-headers-first peers."""
        blocks = self.chain.chain[start_index : start_index + limit]
        payloads = self._bounded_block_payloads(
            blocks,
            response_type="chain_response",
            extra={"requested_limit": 0},
        )
        return {
            "type": "chain_response",
            "data": {
                "blocks": payloads,
                # Legacy peers continue only when the batch is exactly
                # requested_limit, so report what we actually sent.
                "requested_limit": len(payloads),
            },
        }

    def _validated_request_id(self, payload: dict) -> str | None:
        request_id = payload.get("request_id")
        if request_id is None:
            return None
        if not isinstance(request_id, str) or not request_id:
            raise SyncError("request_id must be a non-empty string")
        if len(request_id.encode("utf-8")) > self.max_request_id_bytes:
            raise SyncError(
                f"request_id exceeds the maximum of {self.max_request_id_bytes} bytes"
            )
        return request_id

    def _response_base(
        self,
        response_type: str,
        collection_key: str,
        request_id: str | None,
        extra: dict | None = None,
    ) -> int:
        base = {"type": response_type, "data": {collection_key: []}}
        if request_id is not None:
            base["data"]["request_id"] = request_id
        if extra:
            base["data"].update(extra)
        return _encoded_size(base)

    def _payload_budget(
        self,
        response_type: str,
        collection_key: str,
        request_id: str | None,
        extra: dict | None = None,
    ) -> int:
        envelope = self._response_base(response_type, collection_key, request_id, extra)
        return max(0, self.max_response_bytes - envelope)

    @staticmethod
    def _with_request_id(response: dict, request_id: str | None) -> dict:
        if request_id is not None:
            response["data"]["request_id"] = request_id
        return response

    def _bounded_block_payloads(
        self,
        blocks: Sequence[Block],
        *,
        response_type: str = "blocks",
        request_id: str | None = None,
        extra: dict | None = None,
    ) -> list[dict]:
        budget = self._payload_budget(response_type, "blocks", request_id, extra=extra)
        used = 0
        payloads: list[dict] = []
        for block in blocks:
            payload = block.to_dict()
            size = _encoded_size(payload) + 1
            if used + size > budget:
                # A valid block can be larger than the soft response budget
                # yet still fit in one framed message. Keep it rather than
                # stalling sync on a block that fits the network limit. The
                # encoded overhead is only needed for this single payload.
                if not payloads and self._fits_frame(
                    response_type, payload, request_id, extra
                ):
                    payloads.append(payload)
                break
            payloads.append(payload)
            used += size
        return payloads

    def _fits_frame(
        self,
        response_type: str,
        payload: dict,
        request_id: str | None,
        extra: dict | None,
    ) -> bool:
        response = {"type": response_type, "data": {"blocks": [payload]}}
        if request_id is not None:
            response["data"]["request_id"] = request_id
        if extra:
            response["data"].update(extra)
        # +1 for the newline that frames the message on the wire.
        return _encoded_size(response) + 1 <= MAX_FRAME_BYTES

    # ── inbound responses ────────────────────────────────────────────────────

    def _handle_headers(self, peer_addr: str, payload: dict) -> ValidationStatus | None:
        pending = self._take_pending(peer_addr, "headers", payload.get("request_id"))
        if pending is None:
            # Unsolicited responses are ignored rather than punished: a peer may
            # have answered a request that timed out locally.
            return None

        raw_headers = payload.get("headers")
        if not isinstance(raw_headers, list):
            raise SyncError("headers must be a list")
        if len(raw_headers) > self.max_headers:
            raise SyncError(f"too many headers: {len(raw_headers)}")

        if not raw_headers:
            self._drop_session(peer_addr)
            return None

        try:
            headers = [BlockHeader.from_dict(raw) for raw in raw_headers]
        except (TypeError, ValueError, OverflowError) as exc:
            raise SyncError(f"invalid header payload: {exc}") from exc

        batch_work = _total_work(headers)
        session = self._sessions.get(peer_addr)
        if session is not None:
            # A fork with more than one header batch keeps requesting headers
            # until the branch proves more work than our own suffix. Otherwise a
            # deep reorg would be rejected after the first 1,000 headers.
            if not session.fork or session.next_index != 0:
                raise SyncError("unexpected headers response during body download")
            previous = session.headers[-1]
            if headers[0].previous_hash != previous.hash:
                raise SyncError("headers do not continue the advertised fork")
            validate_header_chain(headers, previous)
            if len(session.headers) + len(headers) > self.max_fork_headers:
                raise SyncError(
                    f"fork headers exceed the maximum of {self.max_fork_headers}"
                )
            session.headers.extend(headers)
            session.work += batch_work
        else:
            start_block = self._find_block(headers[0].previous_hash)
            if start_block is None:
                raise SyncError("headers do not connect to a known block")
            validate_header_chain(headers, start_block)
            session = _SyncSession(
                peer_addr=peer_addr,
                headers=headers,
                common_ancestor=start_block,
                fork=start_block.hash != self._tip_hash(),
                work=batch_work,
            )
            self._sessions[peer_addr] = session

        if session.fork and session.work <= self._suffix_work(session.common_ancestor):
            self._request_next_headers(session)
            return None

        self._request_next_blocks(session)
        return None

    def _handle_blocks(self, peer_addr: str, payload: dict) -> ValidationStatus | None:
        pending = self._take_pending(peer_addr, "blocks", payload.get("request_id"))
        if pending is None:
            return None

        session = self._sessions.get(peer_addr)
        if session is None:
            return None

        raw_blocks = payload.get("blocks")
        if not isinstance(raw_blocks, list):
            raise SyncError("blocks must be a list")
        if len(raw_blocks) > self.max_blocks:
            raise SyncError(f"too many blocks: {len(raw_blocks)}")

        expected_headers = session.headers[
            session.next_index : session.next_index + self.max_blocks
        ]
        if not raw_blocks:
            raise SyncError("empty blocks response")
        if len(raw_blocks) > len(expected_headers):
            raise SyncError(
                f"expected at most {len(expected_headers)} blocks, received {len(raw_blocks)}"
            )

        try:
            blocks = [Block.from_dict(raw) for raw in raw_blocks]
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError) as exc:
            raise SyncError(f"invalid block payload: {exc}") from exc

        for block, header in zip(blocks, expected_headers):
            if block.hash != header.hash:
                raise SyncError(f"received block {block.hash}, expected {header.hash}")

        if not session.fork and session.local_tip != self._tip_hash():
            # A broadcast advanced our chain while this body request was in
            # flight, so the response is validated against an old tip. Start
            # over from the current chain instead of punishing an honest peer.
            logger.info("Local tip changed during sync with %s; restarting", peer_addr)
            self._drop_session(peer_addr)
            self.request_sync(peer_addr)
            return None

        if session.fork:
            session.blocks.extend(blocks)
        else:
            for block in blocks:
                status = self.chain.add_block(block)
                if status != ValidationStatus.VALID:
                    self._drop_session(peer_addr)
                    return status
                self._notify_block_applied(block)

        session.next_index += len(blocks)

        if session.next_index < len(session.headers):
            self._request_next_blocks(session)
            return None

        if session.fork:
            success, orphans = self.chain.resolve_conflicts(session.blocks)
            if not success:
                self._drop_session(peer_addr)
                return ValidationStatus.INVALID
            for block in session.blocks:
                self._notify_block_applied(block)
            self._notify_orphans(orphans)

        self._finish_session(session)
        return None

    # ── session helpers ──────────────────────────────────────────────────────

    def _request_next_headers(
        self,
        session: _SyncSession,
        *,
        attempts: int = 1,
        expires_at: float | None = None,
    ) -> None:
        """Continue a fork's header range from its last known hash."""
        self._send_headers_request(
            session.peer_addr,
            locator=[session.headers[-1].hash],
            attempts=attempts,
            expires_at=expires_at,
        )

    def _request_next_blocks(
        self,
        session: _SyncSession,
        *,
        attempts: int = 1,
        expires_at: float | None = None,
    ) -> None:
        batch = session.headers[
            session.next_index : session.next_index + self.max_blocks
        ]
        if not batch:
            self._finish_session(session)
            return

        now = self._clock()
        session.local_tip = self._tip_hash()
        request_id = self._new_request_id()
        self._pending[request_id] = _PendingRequest(
            peer_addr=session.peer_addr,
            kind="blocks",
            created_at=now,
            expires_at=(
                now + self.request_timeout if expires_at is None else expires_at
            ),
            attempts=attempts,
        )
        self._dispatch_sync(
            session.peer_addr,
            {
                "type": "getblocks",
                "data": {
                    "request_id": request_id,
                    "hashes": [header.hash for header in batch],
                },
            },
        )

    def _finish_session(self, session: _SyncSession) -> None:
        self._sessions.pop(session.peer_addr, None)
        # After every non-empty header batch, ask for what follows the (possibly
        # newly adopted) tip. The peer returns an empty batch when we are caught
        # up, which stops the loop. This also covers batches shortened by the
        # peer's response byte budget rather than its header count.
        if session.headers:
            self.request_sync(session.peer_addr)

    def _drop_session(self, peer_addr: str) -> None:
        self._sessions.pop(peer_addr, None)
        self._deferred.pop(peer_addr, None)
        for request_id, pending in list(self._pending.items()):
            if pending.peer_addr == peer_addr:
                self._pending.pop(request_id, None)

    def _take_pending(
        self, peer_addr: str, kind: str, request_id
    ) -> _PendingRequest | None:
        if not isinstance(request_id, str):
            return None
        pending = self._pending.get(request_id)
        if pending is None or pending.peer_addr != peer_addr or pending.kind != kind:
            return None
        return self._pending.pop(request_id)

    def _prune_pending(self) -> None:
        now = self._clock()
        for request_id, pending in list(self._pending.items()):
            if now - pending.created_at <= self.retry_seconds:
                continue
            if pending.attempts <= self.max_retries:
                self._pending.pop(request_id, None)
                self._retry_request(pending)
            elif now >= pending.expires_at:
                self._pending.pop(request_id, None)
                self._drop_session(pending.peer_addr)

    def _retry_request(self, pending: _PendingRequest) -> None:
        session = self._sessions.get(pending.peer_addr)
        attempts = pending.attempts + 1
        if pending.kind == "headers":
            if session is None:
                self._send_headers_request(
                    pending.peer_addr,
                    attempts=attempts,
                    expires_at=pending.expires_at,
                )
            elif session.fork and session.next_index == 0:
                self._request_next_headers(
                    session,
                    attempts=attempts,
                    expires_at=pending.expires_at,
                )
            else:
                self._drop_session(pending.peer_addr)
        elif pending.kind == "blocks" and session is not None:
            self._request_next_blocks(
                session,
                attempts=attempts,
                expires_at=pending.expires_at,
            )
        else:
            self._drop_session(pending.peer_addr)

    def _new_request_id(self) -> str:
        return self._request_id_factory()

    # ── chain helpers ────────────────────────────────────────────────────────

    def _tip_hash(self) -> str | None:
        chain = self.chain.chain
        return chain[-1].hash if chain else None

    def _chain_hash_index(self) -> tuple[dict[str, Block], dict[str, int]]:
        """Return cached hash/position maps for the current main chain."""
        chain = self.chain.chain
        reorged = self._indexed_length > 0 and (
            len(chain) < self._indexed_length
            or chain[self._indexed_length - 1].hash != self._indexed_tip
        )
        if reorged:
            self._hash_index = {}
            self._hash_positions = {}
            self._indexed_length = 0
            self._indexed_tip = None
        for index in range(self._indexed_length, len(chain)):
            block = chain[index]
            self._hash_index[block.hash] = block
            self._hash_positions[block.hash] = index
        if len(chain) != self._indexed_length:
            self._indexed_length = len(chain)
            self._indexed_tip = chain[-1].hash if chain else None
        return self._hash_index, self._hash_positions

    def _find_block(self, block_hash: str) -> Block | None:
        return self._chain_hash_index()[0].get(block_hash)

    def _index_of(self, block: Block) -> int:
        position = self._chain_hash_index()[1].get(block.hash)
        if position is None:
            raise SyncError("block is not part of the local chain")
        return position

    def _suffix_work(self, start_block: Block) -> int:
        start_index = self._index_of(start_block)
        return _total_work(self.chain.chain[start_index + 1 :])

    def find_common_ancestor(self, locator: Sequence[str]) -> Block | None:
        by_hash = self._chain_hash_index()[0]
        for block_hash in locator:
            block = by_hash.get(block_hash)
            if block is not None:
                return block
        return None

    def _notify_block_applied(self, block: Block) -> None:
        if self._on_block_applied is None:
            return
        try:
            self._on_block_applied(block)
        except Exception:
            logger.exception("on_block_applied callback failed")

    def _notify_orphans(self, orphans: Sequence) -> None:
        if self._on_orphans is None:
            return
        try:
            self._on_orphans(orphans)
        except Exception:
            logger.exception("on_orphans callback failed")
