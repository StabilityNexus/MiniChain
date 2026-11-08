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
from .network_config import MAX_FUTURE_BLOCK_TIME_MS, MAX_TARGET, MIN_TARGET
from .node_config import (
    MAX_BLOCKS_PER_REQUEST,
    MAX_HEADERS_PER_REQUEST,
    MAX_LOCATOR_HASHES,
    MAX_SYNC_RESPONSE_BYTES,
    SYNC_REQUEST_BURST,
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
    ):
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        self.rate_per_second = float(rate_per_second)
        self.burst = float(burst)
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    def allow(self, peer_addr: str) -> bool:
        now = self._clock()
        tokens, last_seen = self._buckets.get(peer_addr, (self.burst, now))
        elapsed = max(0.0, now - last_seen)
        tokens = min(self.burst, tokens + elapsed * self.rate_per_second)
        if tokens < 1.0:
            self._buckets[peer_addr] = (tokens, now)
            return False
        self._buckets[peer_addr] = (tokens - 1.0, now)
        return True


@dataclass
class _PendingRequest:
    peer_addr: str
    kind: str
    created_at: float


@dataclass
class _SyncSession:
    peer_addr: str
    headers: list[BlockHeader]
    common_ancestor: Block
    fork: bool
    next_index: int = 0
    blocks: list[Block] = field(default_factory=list)


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
        request_timeout: float = SYNC_REQUEST_TIMEOUT_SECONDS,
        rate_limiter: PeerRateLimiter | None = None,
        clock: Callable[[], float] = time.monotonic,
        request_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ):
        if max_headers <= 0 or max_blocks <= 0:
            raise ValueError("sync batch sizes must be positive")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be positive")
        if locator_size <= 0:
            raise ValueError("locator_size must be positive")
        self.chain = chain
        self._send = send
        self._on_block_applied = on_block_applied
        self._on_orphans = on_orphans
        self.max_headers = max_headers
        self.max_blocks = max_blocks
        self.max_response_bytes = max_response_bytes
        self.locator_size = locator_size
        self.request_timeout = request_timeout
        self._rate_limiter = rate_limiter or PeerRateLimiter(clock=clock)
        self._clock = clock
        self._request_id_factory = request_id_factory
        self._pending: dict[str, _PendingRequest] = {}
        self._sessions: dict[str, _SyncSession] = {}

    @staticmethod
    def handles(message_type: str) -> bool:
        return message_type in SYNC_MESSAGE_TYPES

    # ── outbound sync ────────────────────────────────────────────────────────

    def request_sync(self, peer_addr: str) -> None:
        """Ask a peer for headers that follow our current best block."""
        if peer_addr in self._sessions:
            return
        if any(
            pending.peer_addr == peer_addr and pending.kind == "headers"
            for pending in self._pending.values()
        ):
            return

        request_id = self._new_request_id()
        self._pending[request_id] = _PendingRequest(
            peer_addr=peer_addr,
            kind="headers",
            created_at=self._clock(),
        )
        self._send(
            peer_addr,
            {
                "type": "getheaders",
                "data": {
                    "request_id": request_id,
                    "locator": self.locator(),
                    "max": self.max_headers,
                },
            },
        )

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
            if not self._rate_limiter.allow(peer_addr):
                logger.warning("Rate-limited sync request from %s", peer_addr)
                return ValidationStatus.FAILED
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

        start_block = self.find_common_ancestor(locator)
        start_index = self._index_of(start_block) if start_block is not None else -1
        budget = max(1, self.max_response_bytes - 512)
        used = 0
        headers: list[dict] = []
        for block in self.chain.chain[start_index + 1 : start_index + 1 + max_headers]:
            if stop_hash is not None and block.hash == stop_hash:
                break
            header = BlockHeader.from_block(block).to_dict()
            size = len(canonical_json_dumps(header)) + 1
            if used + size > budget:
                break
            headers.append(header)
            used += size

        response = {"type": "headers", "data": {"headers": headers}}
        if isinstance(payload.get("request_id"), str):
            response["data"]["request_id"] = payload["request_id"]
        return response

    def build_blocks_response(self, payload: dict) -> dict:
        hashes = _validate_hashes(
            payload.get("hashes"),
            maximum=self.max_blocks,
            field_name="hashes",
        )
        by_hash = {block.hash: block for block in self.chain.chain}
        selected = []
        for block_hash in hashes:
            block = by_hash.get(block_hash)
            if block is None:
                # Stop at the first gap: the requester applies a contiguous
                # prefix of its requested batch.
                break
            selected.append(block)

        response = {
            "type": "blocks",
            "data": {"blocks": self._bounded_block_payloads(selected)},
        }
        if isinstance(payload.get("request_id"), str):
            response["data"]["request_id"] = payload["request_id"]
        return response

    def build_legacy_blocks_response(self, start_index: int, limit: int) -> dict:
        """Build a bounded ``chain_response`` for pre-headers-first peers."""
        blocks = self.chain.chain[start_index : start_index + limit]
        payloads = self._bounded_block_payloads(blocks)
        return {
            "type": "chain_response",
            "data": {
                "blocks": payloads,
                # Legacy peers continue only when the batch is exactly
                # requested_limit, so report what we actually sent.
                "requested_limit": len(payloads),
            },
        }

    def _bounded_block_payloads(self, blocks: Sequence[Block]) -> list[dict]:
        budget = max(1, self.max_response_bytes - 512)
        used = 0
        payloads: list[dict] = []
        for block in blocks:
            payload = block.to_dict()
            size = len(canonical_json_dumps(payload)) + 1
            if used + size > budget:
                break
            payloads.append(payload)
            used += size
        return payloads

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
        except (TypeError, ValueError) as exc:
            raise SyncError(f"invalid header payload: {exc}") from exc

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
            session.headers.extend(headers)
        else:
            start_block = self._find_block(headers[0].previous_hash)
            if start_block is None:
                raise SyncError("headers do not connect to a known block")
            validate_header_chain(headers, start_block)
            session = _SyncSession(
                peer_addr=peer_addr,
                headers=headers,
                common_ancestor=start_block,
                fork=start_block.hash != self.chain.last_block.hash,
            )
            self._sessions[peer_addr] = session

        if session.fork and _total_work(session.headers) <= self._suffix_work(
            session.common_ancestor
        ):
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
        except (KeyError, TypeError, ValueError) as exc:
            raise SyncError(f"invalid block payload: {exc}") from exc

        for block, header in zip(blocks, expected_headers):
            if block.hash != header.hash:
                raise SyncError(f"received block {block.hash}, expected {header.hash}")

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

    def _request_next_headers(self, session: _SyncSession) -> None:
        """Continue a fork's header range from its last known hash."""
        request_id = self._new_request_id()
        self._pending[request_id] = _PendingRequest(
            peer_addr=session.peer_addr,
            kind="headers",
            created_at=self._clock(),
        )
        self._send(
            session.peer_addr,
            {
                "type": "getheaders",
                "data": {
                    "request_id": request_id,
                    "locator": [session.headers[-1].hash],
                    "max": self.max_headers,
                },
            },
        )

    def _request_next_blocks(self, session: _SyncSession) -> None:
        batch = session.headers[
            session.next_index : session.next_index + self.max_blocks
        ]
        if not batch:
            self._finish_session(session)
            return

        request_id = self._new_request_id()
        self._pending[request_id] = _PendingRequest(
            peer_addr=session.peer_addr,
            kind="blocks",
            created_at=self._clock(),
        )
        self._send(
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
        # A fork may have been adopted after only the prefix needed to prove more
        # work, so always ask for what follows the new tip. A linear sync only
        # needs another request when the peer filled a whole header batch.
        if session.fork or len(session.headers) == self.max_headers:
            self.request_sync(session.peer_addr)

    def _drop_session(self, peer_addr: str) -> None:
        self._sessions.pop(peer_addr, None)
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
            if now - pending.created_at > self.request_timeout:
                self._pending.pop(request_id, None)
                self._drop_session(pending.peer_addr)

    def _new_request_id(self) -> str:
        return self._request_id_factory()

    # ── chain helpers ────────────────────────────────────────────────────────

    def _find_block(self, block_hash: str) -> Block | None:
        for block in reversed(self.chain.chain):
            if block.hash == block_hash:
                return block
        return None

    def _index_of(self, block: Block) -> int:
        for index, candidate in enumerate(self.chain.chain):
            if candidate.hash == block.hash:
                return index
        raise SyncError("block is not part of the local chain")

    def _suffix_work(self, start_block: Block) -> int:
        start_index = self._index_of(start_block)
        return _total_work(self.chain.chain[start_index + 1 :])

    def find_common_ancestor(self, locator: Sequence[str]) -> Block | None:
        by_hash = {block.hash: block for block in self.chain.chain}
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
