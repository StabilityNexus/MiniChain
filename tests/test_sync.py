"""Tests for headers-first synchronization and the wire format it uses."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from main import make_network_handler, make_sync_manager, mine_and_process_block
from minichain.block import Block, BlockHeader
from minichain.chain import Blockchain
from minichain.mempool import Mempool
from minichain.network_config import (
    MAX_FRAME_BYTES,
    MAX_FUTURE_BLOCK_TIME_MS,
    MAX_TARGET,
)
from minichain.serialization import canonical_json_dumps
from minichain.sync import (
    PeerRateLimiter,
    SyncError,
    SyncManager,
    validate_header_chain,
)
from minichain.transaction import Transaction
from minichain.validators import ValidationStatus


@pytest.fixture
def genesis_file(tmp_path):
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    data = {
        "chain_id": "minichain-default",
        "timestamp": 1_700_000_000_000,
        "target": hex(MAX_TARGET),
        "target_block_time": 1000,
        "alloc": {pk: {"balance": 10_000}},
    }
    path = tmp_path / "genesis_sync.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path), sk, pk


def _rehash(header, **changes):
    """Return ``header`` with ``changes`` applied and a matching hash."""
    updated = replace(header, hash=None, **changes)
    return replace(updated, hash=updated.compute_hash())


def _fake_chain(length, *, timestamp_base=1_700_000_000_000):
    blocks = []
    for index in range(length):
        block = Block(
            index=index,
            previous_hash="0" * 64 if index == 0 else blocks[-1].hash,
            target=MAX_TARGET,
            timestamp=timestamp_base + index,
        )
        block.hash = block.compute_hash()
        blocks.append(block)
    return SimpleNamespace(chain=blocks)


def _fake_branch(genesis, length):
    """A competing chain from ``genesis`` with a different timestamp sequence."""
    blocks = [genesis]
    for index in range(1, length + 1):
        block = Block(
            index=index,
            previous_hash=blocks[-1].hash,
            target=MAX_TARGET,
            timestamp=genesis.timestamp + 1000 + index,
        )
        block.hash = block.compute_hash()
        blocks.append(block)
    return SimpleNamespace(chain=blocks)


def _mine_empty_block(chain, miner, timestamp):
    """Mine an empty block with an explicit timestamp for deterministic EMA."""
    temp_state = chain.state.copy()
    temp_state.chain_id = chain.chain_id
    temp_state.credit_mining_reward(miner, reward=temp_state.DEFAULT_MINING_REWARD)
    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=[],
        timestamp=timestamp,
        state_root=temp_state.state_root(),
        receipt_root=None,
        receipts=[],
        miner=miner,
        target=chain.current_target,
    )
    from minichain.pow import mine_block

    mined = mine_block(block)
    assert chain.add_block(mined) == ValidationStatus.VALID
    return mined


def _make_sync_pair(
    receiver_chain, sender_chain, *, server_kwargs=None, **client_kwargs
):
    receiver_outbox = []
    sender_outbox = []
    receiver_sent = []

    def receiver_send(peer, message):
        receiver_outbox.append((peer, message))
        receiver_sent.append(message)

    def sender_send(peer, message):
        sender_outbox.append((peer, message))

    client = SyncManager(receiver_chain, receiver_send, **client_kwargs)
    server = SyncManager(sender_chain, sender_send, **(server_kwargs or {}))
    return client, server, receiver_outbox, sender_outbox, receiver_sent


def _pump(client, server, receiver_outbox, sender_outbox, *, max_steps=500):
    """Deliver queued protocol messages until both sides are idle."""
    statuses = []
    for _ in range(max_steps):
        if not receiver_outbox and not sender_outbox:
            return statuses
        if receiver_outbox:
            peer, message = receiver_outbox.pop(0)
            status = server.handle_message(peer, message["type"], message["data"])
        else:
            peer, message = sender_outbox.pop(0)
            status = client.handle_message(peer, message["type"], message["data"])
        statuses.append(status)
    raise AssertionError("sync did not terminate")


class TestBlockHeader:
    def test_round_trip_matches_block_hash(self, genesis_file):
        path, _, pk = genesis_file
        chain = Blockchain(genesis_path=path)
        block = mine_and_process_block(chain, Mempool(), pk)

        header = BlockHeader.from_dict(block.to_dict())

        assert header.hash == block.hash
        assert header.to_dict(include_hash=False) == block.to_header_dict()
        assert header.compute_hash() == block.hash

    def test_missing_hash_is_computed(self, genesis_file):
        path, _, pk = genesis_file
        chain = Blockchain(genesis_path=path)
        block = mine_and_process_block(chain, Mempool(), pk)
        payload = block.to_dict()
        expected_hash = payload.pop("hash")

        header = BlockHeader.from_dict(payload)

        assert header.hash == expected_hash

    @pytest.mark.parametrize(
        "receipt_root,miner,expected_hash",
        [
            (
                None,
                None,
                "ba34a5340ad135c7820c508ea37bbc82f858298a33f8e2abfa26d7d86d1d0085",
            ),
            (
                "33" * 32,
                None,
                "855a744df5013d3c1cbdbe065cdb7cb65d69ba0252474f7a12c3516c51e8e6c4",
            ),
            (
                None,
                "44" * 32,
                "7587aca78c935ccbee746674ac34eddc4d53e61f000441440b4b1718a3d2e653",
            ),
            (
                "33" * 32,
                "44" * 32,
                "b164445a7b61128bb334ffdf8cd11ee4a6eb1b06633964f6df657ba11441d60d",
            ),
        ],
        ids=[
            "no-receipt-no-miner",
            "receipt-no-miner",
            "no-receipt-miner",
            "receipt-miner",
        ],
    )
    def test_legacy_header_vectors_are_pinned(self, receipt_root, miner, expected_hash):
        expected = {
            "index": 7,
            "previous_hash": "11" * 32,
            "merkle_root": None,
            "state_root": "22" * 32,
            "receipt_root": receipt_root,
            "timestamp": 1_700_000_000_123,
            "target": hex(MAX_TARGET),
            "nonce": 42,
        }
        if miner is not None:
            expected["miner"] = miner

        header = BlockHeader(
            index=7,
            previous_hash="11" * 32,
            merkle_root=None,
            state_root="22" * 32,
            receipt_root=receipt_root,
            timestamp=1_700_000_000_123,
            target=MAX_TARGET,
            nonce=42,
            miner=miner,
        )

        assert header.to_dict(include_hash=False) == expected
        assert header.compute_hash() == expected_hash
        assert BlockHeader.from_dict(expected).hash == expected_hash

    def test_tampered_header_is_rejected(self, genesis_file):
        path, _, pk = genesis_file
        chain = Blockchain(genesis_path=path)
        block = mine_and_process_block(chain, Mempool(), pk)
        payload = block.to_dict()
        payload["nonce"] += 1

        with pytest.raises(ValueError, match="hash"):
            BlockHeader.from_dict(payload)


class TestLocator:
    def test_exponential_spacing_and_genesis_anchor(self):
        chain = _fake_chain(100)
        manager = SyncManager(chain, lambda *_: None, locator_size=32)

        locator = manager.locator()

        expected = [
            chain.chain[i].hash for i in (99, 98, 97, 96, 95, 94, 93, 92, 91, 90)
        ]
        expected += [chain.chain[i].hash for i in (88, 84, 76, 60, 28, 0)]
        assert locator == expected
        assert len(locator) <= 32

    def test_single_block_chain_locator(self):
        chain = _fake_chain(1)
        manager = SyncManager(chain, lambda *_: None)

        assert manager.locator() == [chain.chain[0].hash]


class TestHeaderValidation:
    def _headers(self):
        chain = _fake_chain(4)
        headers = [BlockHeader.from_block(block) for block in chain.chain[1:]]
        return chain.chain[0], headers

    def test_valid_chain_passes(self):
        start, headers = self._headers()

        validate_header_chain(headers, start, now_ms=headers[-1].timestamp + 1)

    @pytest.mark.parametrize(
        "changes,message",
        [
            ({"index": 99}, "index"),
            ({"previous_hash": "f" * 64}, "previous hash"),
            ({"timestamp": 1_700_000_000_000}, "timestamp"),
        ],
    )
    def test_bad_linkage_is_rejected(self, changes, message):
        start, headers = self._headers()

        with pytest.raises(SyncError, match=message):
            validate_header_chain([_rehash(headers[0], **changes)], start)

    def test_bad_hash_is_rejected(self):
        start, headers = self._headers()

        with pytest.raises(SyncError, match="hash"):
            validate_header_chain(
                [replace(headers[0], hash="0" * 64)],
                start,
            )

    def test_missing_hash_is_rejected(self):
        start, headers = self._headers()

        with pytest.raises(SyncError, match="hash"):
            validate_header_chain([replace(headers[0], hash=None)], start)

    def test_invalid_proof_of_work_is_rejected(self):
        start, headers = self._headers()
        impossible = _rehash(headers[0], target=1)

        with pytest.raises(SyncError, match="proof of work"):
            validate_header_chain([impossible], start)

    def test_far_future_timestamp_is_rejected(self):
        start, headers = self._headers()
        now_ms = headers[0].timestamp
        future = _rehash(
            headers[0],
            timestamp=now_ms + MAX_FUTURE_BLOCK_TIME_MS + 1,
        )

        with pytest.raises(SyncError, match="future"):
            validate_header_chain([future], start, now_ms=now_ms)


class TestPeerRateLimiter:
    def test_token_bucket_allows_burst_then_refills(self):
        now = [0.0]
        limiter = PeerRateLimiter(
            rate_per_second=1.0,
            burst=2,
            clock=lambda: now[0],
        )

        assert limiter.allow("peer:a")
        assert limiter.allow("peer:a")
        assert not limiter.allow("peer:a")

        assert limiter.allow("peer:b")

        now[0] = 1.0
        assert limiter.allow("peer:a")

    def test_bucket_state_is_bounded_and_idle_peers_expire(self):
        now = [0.0]
        limiter = PeerRateLimiter(
            rate_per_second=1.0,
            burst=1,
            clock=lambda: now[0],
            max_peers=2,
        )

        assert limiter.allow("peer:a")
        assert limiter.allow("peer:b")
        assert not limiter.allow("peer:c")
        assert len(limiter._buckets) <= 2

        # After a full refill window every remaining bucket is indistinguishable
        # from an absent one, so maintenance can drop them all.
        now[0] = 2.0
        limiter.prune()
        assert limiter._buckets == {}


class TestSyncRecovery:
    def test_unanswered_request_is_retried_then_dropped(self):
        now = [0.0]
        sent = []
        manager = SyncManager(
            _fake_chain(3),
            lambda peer, message: sent.append(message),
            clock=lambda: now[0],
            request_timeout=30.0,
            retry_seconds=5.0,
            max_retries=1,
        )

        manager.request_sync("peer:a")
        assert len(sent) == 1

        now[0] = 6.0
        manager.maintain()
        assert len(sent) == 2
        assert sent[-1]["type"] == "getheaders"

        # The retry budget is spent, but the session deadline has not passed.
        manager.maintain()
        assert len(sent) == 2
        assert manager._pending

        now[0] = 31.0
        manager.maintain()
        assert not manager._pending
        assert not manager._sessions

    def test_expired_request_does_not_block_request_sync(self):
        now = [0.0]
        sent = []
        manager = SyncManager(
            _fake_chain(3),
            lambda peer, message: sent.append(message),
            clock=lambda: now[0],
            request_timeout=30.0,
            retry_seconds=5.0,
            max_retries=0,
        )

        manager.request_sync("peer:a")
        assert len(sent) == 1

        now[0] = 31.0
        manager.request_sync("peer:a")
        assert len(sent) == 2

    def test_response_to_an_earlier_attempt_is_accepted(self):
        now = [0.0]
        sent = []
        manager = SyncManager(
            _fake_chain(3),
            lambda peer, message: sent.append(message),
            clock=lambda: now[0],
            retry_seconds=5.0,
            max_retries=1,
        )
        manager.request_sync("peer:a")
        first = sent[0]
        first_id = first["data"]["request_id"]

        now[0] = 6.0
        manager.maintain()
        second = sent[1]
        second_id = second["data"]["request_id"]
        assert first_id != second_id
        assert manager._pending[first_id].retired

        previous = manager.chain.chain[-1]
        header = BlockHeader(
            index=previous.index + 1,
            previous_hash=previous.hash,
            merkle_root=None,
            state_root=None,
            receipt_root=None,
            timestamp=previous.timestamp + 1,
            target=MAX_TARGET,
            nonce=0,
        )
        header = replace(header, hash=header.compute_hash())
        status = manager.handle_message(
            "peer:a",
            "headers",
            {
                "headers": [header.to_dict()],
                "request_id": first_id,
            },
        )

        assert status is None
        assert manager._sessions
        # A response to either attempt retires the whole request group.
        assert first_id not in manager._pending
        assert second_id not in manager._pending
        assert [p.kind for p in manager._pending.values()] == ["blocks"]
        assert sent[-1]["type"] == "getblocks"

    def test_retired_attempts_expire_with_the_session(self):
        now = [0.0]
        manager = SyncManager(
            _fake_chain(3),
            lambda *_: None,
            clock=lambda: now[0],
            retry_seconds=5.0,
            max_retries=1,
        )
        manager.request_sync("peer:a")
        first_id = next(iter(manager._pending))

        now[0] = 6.0
        manager.maintain()
        assert len(manager._pending) == 2
        assert manager._pending[first_id].retired

        now[0] = 31.0
        manager.maintain()
        assert not manager._pending
        assert not manager._sessions

    def test_outgoing_requests_wait_for_a_peer_token(self):
        now = [0.0]
        sent = []
        manager = SyncManager(
            _fake_chain(3),
            lambda peer, message: sent.append(message),
            clock=lambda: now[0],
            outgoing_rate_limiter=PeerRateLimiter(
                rate_per_second=1.0, burst=1, clock=lambda: now[0]
            ),
        )

        manager.request_sync("peer:a")
        assert [message["type"] for message in sent] == ["getheaders"]

        manager._pending.clear()
        manager.request_sync("peer:a")
        assert len(sent) == 1
        assert "peer:a" in manager._deferred

        now[0] = 0.5
        manager.maintain()
        assert len(sent) == 1

        now[0] = 1.0
        manager.maintain()
        assert [message["type"] for message in sent] == ["getheaders", "getheaders"]
        assert manager._deferred == {}

    def test_dropping_a_session_discards_its_deferred_request(self):
        now = [0.0]
        sent = []
        manager = SyncManager(
            _fake_chain(3),
            lambda peer, message: sent.append(message),
            clock=lambda: now[0],
            outgoing_rate_limiter=PeerRateLimiter(
                rate_per_second=1.0, burst=1, clock=lambda: now[0]
            ),
        )

        manager.request_sync("peer:a")
        manager._pending.clear()
        manager.request_sync("peer:a")
        assert "peer:a" in manager._deferred

        manager._drop_session("peer:a")
        now[0] = 1.0
        manager.maintain()

        assert manager._deferred == {}
        assert [message["type"] for message in sent] == ["getheaders"]


class TestSyncResponses:
    def test_default_header_batch_fits_in_one_frame(self):
        chain = _fake_chain(1100)
        manager = SyncManager(chain, lambda *_: None)

        response = manager.build_headers_response(
            {"locator": [chain.chain[0].hash], "max": 1000}
        )

        assert len(response["data"]["headers"]) == 1000
        assert len(canonical_json_dumps(response).encode()) < MAX_FRAME_BYTES

    def test_header_response_honours_count_and_byte_limits(self):
        chain = _fake_chain(10)
        header_size = (
            len(
                canonical_json_dumps(
                    BlockHeader.from_block(chain.chain[1]).to_dict()
                ).encode("utf-8")
            )
            + 1
        )
        envelope = len(
            canonical_json_dumps({"type": "headers", "data": {"headers": []}}).encode(
                "utf-8"
            )
        )
        manager = SyncManager(
            chain,
            lambda *_: None,
            max_headers=5,
            max_response_bytes=envelope + header_size * 2,
        )

        response = manager.build_headers_response(
            {"locator": [chain.chain[0].hash], "max": 5}
        )

        assert len(response["data"]["headers"]) == 2
        assert [h["index"] for h in response["data"]["headers"]] == [1, 2]

    def test_block_response_is_an_ordered_contiguous_prefix(self):
        chain = _fake_chain(5)
        manager = SyncManager(chain, lambda *_: None, max_blocks=4)

        response = manager.build_blocks_response(
            {
                "hashes": [
                    chain.chain[3].hash,
                    chain.chain[1].hash,
                    "f" * 64,
                    chain.chain[4].hash,
                ]
            }
        )

        assert [b["hash"] for b in response["data"]["blocks"]] == [
            chain.chain[3].hash,
            chain.chain[1].hash,
        ]

    def test_block_response_honours_byte_limit(self):
        chain = _fake_chain(6)
        block_size = (
            len(canonical_json_dumps(chain.chain[1].to_dict()).encode("utf-8")) + 1
        )
        envelope = len(
            canonical_json_dumps({"type": "blocks", "data": {"blocks": []}}).encode(
                "utf-8"
            )
        )
        manager = SyncManager(
            chain,
            lambda *_: None,
            max_blocks=4,
            max_response_bytes=envelope + block_size * 2,
        )

        response = manager.build_blocks_response(
            {
                "hashes": [
                    chain.chain[1].hash,
                    chain.chain[2].hash,
                    chain.chain[3].hash,
                    chain.chain[4].hash,
                ]
            }
        )

        assert [b["index"] for b in response["data"]["blocks"]] == [1, 2]
        assert len(canonical_json_dumps(response).encode("utf-8")) <= (
            manager.max_response_bytes
        )

    def test_single_block_may_use_the_soft_budget(self):
        chain = _fake_chain(2)
        manager = SyncManager(
            chain,
            lambda *_: None,
            max_response_bytes=64,
        )

        response = manager.build_blocks_response({"hashes": [chain.chain[1].hash]})

        assert [b["index"] for b in response["data"]["blocks"]] == [1]
        assert len(canonical_json_dumps(response).encode("utf-8")) < MAX_FRAME_BYTES

    def test_single_header_may_use_the_soft_budget(self):
        chain = _fake_chain(2)
        manager = SyncManager(
            chain,
            lambda *_: None,
            max_response_bytes=64,
        )

        response = manager.build_headers_response({"locator": [chain.chain[0].hash]})

        assert [h["index"] for h in response["data"]["headers"]] == [1]
        assert len(canonical_json_dumps(response).encode("utf-8")) < MAX_FRAME_BYTES

    def test_block_byte_limit_counts_utf8(self):
        transaction = Transaction(
            sender="11" * 32,
            receiver="22" * 32,
            amount=1,
            nonce=0,
            data="\u00e9" * 100,
        )
        blocks = []
        for index in range(3):
            block = Block(
                index=index,
                previous_hash="0" * 64 if index == 0 else blocks[-1].hash,
                target=MAX_TARGET,
                timestamp=1_700_000_000_000 + index,
                transactions=[transaction],
            )
            block.hash = block.compute_hash()
            blocks.append(block)
        chain = SimpleNamespace(chain=blocks)
        block_size = max(
            len(canonical_json_dumps(block.to_dict()).encode("utf-8")) + 1
            for block in blocks[1:]
        )
        envelope = len(
            canonical_json_dumps({"type": "blocks", "data": {"blocks": []}}).encode(
                "utf-8"
            )
        )
        manager = SyncManager(
            chain,
            lambda *_: None,
            max_blocks=3,
            max_response_bytes=envelope + block_size,
        )

        response = manager.build_blocks_response(
            {"hashes": [block.hash for block in blocks[1:]]}
        )

        assert [block["index"] for block in response["data"]["blocks"]] == [1]
        encoded = canonical_json_dumps(response).encode("utf-8")
        assert len(encoded) <= manager.max_response_bytes
        # Character-based sizing would miss the extra UTF-8 bytes.
        assert len(canonical_json_dumps(response)) < len(encoded)

    def test_request_bounds_are_enforced(self):
        chain = _fake_chain(5)
        manager = SyncManager(chain, lambda *_: None, max_blocks=2, locator_size=2)

        with pytest.raises(SyncError, match="locator"):
            manager.build_headers_response(
                {"locator": [chain.chain[i].hash for i in range(3)]}
            )

        with pytest.raises(SyncError, match="hashes"):
            manager.build_blocks_response(
                {"hashes": [chain.chain[i].hash for i in range(3)]}
            )

    def test_request_id_is_bounded_and_counted_in_the_budget(self):
        chain = _fake_chain(5)
        manager = SyncManager(chain, lambda *_: None)
        oversized = "a" * (manager.max_request_id_bytes + 1)

        with pytest.raises(SyncError, match="request_id"):
            manager.build_headers_response({"locator": [], "request_id": oversized})
        with pytest.raises(SyncError, match="request_id"):
            manager.build_blocks_response(
                {"hashes": [chain.chain[1].hash], "request_id": oversized}
            )

        request_id = "b" * 32
        header_size = (
            len(
                canonical_json_dumps(
                    BlockHeader.from_block(chain.chain[1]).to_dict()
                ).encode("utf-8")
            )
            + 1
        )
        envelope = len(
            canonical_json_dumps(
                {
                    "type": "headers",
                    "data": {"headers": [], "request_id": request_id},
                }
            ).encode("utf-8")
        )
        tight = SyncManager(
            chain,
            lambda *_: None,
            max_response_bytes=envelope + header_size * 2,
        )

        response = tight.build_headers_response(
            {"locator": [chain.chain[0].hash], "max": 5, "request_id": request_id}
        )

        assert response["data"]["request_id"] == request_id
        assert len(response["data"]["headers"]) == 2
        assert len(canonical_json_dumps(response).encode("utf-8")) <= (
            tight.max_response_bytes
        )

        block_response = manager.build_blocks_response(
            {"hashes": [chain.chain[1].hash], "request_id": request_id}
        )
        assert block_response["data"]["request_id"] == request_id
        assert len(canonical_json_dumps(block_response).encode("utf-8")) <= (
            manager.max_response_bytes
        )

    def test_block_hash_index_is_reused_and_rebuilt_after_reorg(self):
        chain = _fake_chain(5)
        manager = SyncManager(chain, lambda *_: None)

        assert manager.build_blocks_response({"hashes": [chain.chain[4].hash]})["data"][
            "blocks"
        ]
        cached = manager._hash_index

        # Appending extends the cached index in place.
        extension = Block(
            index=5,
            previous_hash=chain.chain[4].hash,
            target=MAX_TARGET,
            timestamp=chain.chain[4].timestamp + 1,
        )
        extension.hash = extension.compute_hash()
        chain.chain.append(extension)

        response = manager.build_blocks_response({"hashes": [extension.hash]})
        assert [block["index"] for block in response["data"]["blocks"]] == [5]
        assert manager._hash_index is cached

        # Replacing the indexed suffix invalidates every stale entry.
        replacement = Block(
            index=4,
            previous_hash=chain.chain[3].hash,
            target=MAX_TARGET,
            timestamp=chain.chain[3].timestamp + 2,
        )
        replacement.hash = replacement.compute_hash()
        chain.chain[4:] = [replacement]

        response = manager.build_blocks_response({"hashes": [replacement.hash]})
        assert [block["index"] for block in response["data"]["blocks"]] == [4]
        response = manager.build_blocks_response({"hashes": [extension.hash]})
        assert response["data"]["blocks"] == []

    def test_rate_limited_peer_gets_no_response(self):
        now = [0.0]
        sent = []
        manager = SyncManager(
            _fake_chain(5),
            lambda peer, message: sent.append(message),
            rate_limiter=PeerRateLimiter(
                rate_per_second=1.0,
                burst=1,
                clock=lambda: now[0],
            ),
        )
        request = {"locator": [], "max": 1}

        assert manager.handle_message("peer:a", "getheaders", request) is None
        # A dropped request is not a protocol failure: the client retries it,
        # so an honest peer must not accumulate FAILED score for a burst.
        assert manager.handle_message("peer:a", "getheaders", request) is None
        assert len(sent) == 1


class TestNetworkHandlerIntegration:
    class FakeNetwork:
        _peer_id = "self"
        data_path = "."

        def __init__(self):
            self.sent = []

        async def _unicast_raw(self, peer, payload):
            self.sent.append((peer, payload))

    def test_legacy_messages_are_bounded_and_ignored(self, tmp_path):
        genesis = tmp_path / "genesis_handler.json"
        genesis.write_text(
            json.dumps(
                {
                    "chain_id": "minichain-default",
                    "timestamp": 1_700_000_000_000,
                    "target": hex(MAX_TARGET),
                    "target_block_time": 1000,
                    "alloc": {},
                }
            ),
            encoding="utf-8",
        )
        chain = Blockchain(genesis_path=str(genesis))

        async def scenario():
            network = self.FakeNetwork()
            sync = make_sync_manager(chain, Mempool(), network)
            handler = make_network_handler(chain, Mempool(), network, sync)

            status = await handler(
                {
                    "type": "chain_request",
                    "data": {"start_index": 0, "limit": 10_000},
                    "_peer_addr": "peer:a",
                }
            )
            assert status is None
            await asyncio.sleep(0)

            assert len(network.sent) == 1
            _, response = network.sent[0]
            assert response["type"] == "chain_response"
            assert len(response["data"]["blocks"]) == 1
            assert response["data"]["requested_limit"] == 1

            status = await handler(
                {
                    "type": "chain_response",
                    "data": {"blocks": [{"invalid": True}]},
                    "_peer_addr": "peer:a",
                }
            )
            assert status is None
            await asyncio.sleep(0)
            assert len(network.sent) == 1

            status = await handler(
                {"type": "getheaders", "data": None, "_peer_addr": "peer:a"}
            )
            assert status == ValidationStatus.MALFORMED

        asyncio.run(scenario())

    def _genesis(self, tmp_path):
        genesis = tmp_path / "genesis_handler.json"
        genesis.write_text(
            json.dumps(
                {
                    "chain_id": "minichain-default",
                    "timestamp": 1_700_000_000_000,
                    "target": hex(MAX_TARGET),
                    "target_block_time": 1000,
                    "alloc": {},
                }
            ),
            encoding="utf-8",
        )
        return Blockchain(genesis_path=str(genesis))

    def test_legacy_response_is_capped_by_count(self, tmp_path):
        chain = self._genesis(tmp_path)
        pk = SigningKey.generate().verify_key.encode(encoder=HexEncoder).decode()
        for _ in range(3):
            assert mine_and_process_block(chain, Mempool(), pk) is not None

        async def scenario():
            network = self.FakeNetwork()
            sync = SyncManager(chain, lambda *_: None, max_blocks=2)
            handler = make_network_handler(chain, Mempool(), network, sync)

            status = await handler(
                {
                    "type": "chain_request",
                    "data": {"start_index": 0, "limit": 10_000},
                    "_peer_addr": "peer:a",
                }
            )
            assert status is None
            await asyncio.sleep(0)

            _, response = network.sent[0]
            assert len(response["data"]["blocks"]) == 2
            assert response["data"]["requested_limit"] == 2

        asyncio.run(scenario())

    def test_legacy_requests_share_the_per_peer_rate_limit(self, tmp_path):
        chain = self._genesis(tmp_path)
        now = [0.0]

        async def scenario():
            network = self.FakeNetwork()
            sync = SyncManager(
                chain,
                lambda *_: None,
                rate_limiter=PeerRateLimiter(
                    rate_per_second=1.0, burst=1, clock=lambda: now[0]
                ),
            )
            handler = make_network_handler(chain, Mempool(), network, sync)
            request = {
                "type": "chain_request",
                "data": {"start_index": 0, "limit": 1},
                "_peer_addr": "peer:a",
            }

            assert await handler(request) is None
            await asyncio.sleep(0)
            assert len(network.sent) == 1

            assert await handler(request) is None
            await asyncio.sleep(0)
            assert len(network.sent) == 1

        asyncio.run(scenario())

    def test_modern_and_legacy_requests_share_the_rate_limit(self, tmp_path):
        chain = self._genesis(tmp_path)
        now = [0.0]

        async def scenario():
            network = self.FakeNetwork()
            sync = SyncManager(
                chain,
                lambda peer, payload: asyncio.create_task(
                    network._unicast_raw(peer, payload)
                ),
                rate_limiter=PeerRateLimiter(
                    rate_per_second=1.0, burst=1, clock=lambda: now[0]
                ),
            )
            handler = make_network_handler(chain, Mempool(), network, sync)

            assert (
                await handler(
                    {
                        "type": "getheaders",
                        "data": {"locator": [chain.chain[0].hash]},
                        "_peer_addr": "peer:a",
                    }
                )
                is None
            )
            await asyncio.sleep(0)
            assert network.sent[0][1]["type"] == "headers"

            assert (
                await handler(
                    {
                        "type": "chain_request",
                        "data": {"start_index": 0, "limit": 1},
                        "_peer_addr": "peer:a",
                    }
                )
                is None
            )
            await asyncio.sleep(0)
            assert len(network.sent) == 1

        asyncio.run(scenario())


class TestHeadersFirstSync:
    def test_linear_sync_paginates_headers_and_bodies(self, genesis_file):
        path, _, pk = genesis_file
        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)
        for _ in range(5):
            assert mine_and_process_block(sender, Mempool(), pk) is not None

        applied = []
        client, server, receiver_out, sender_out, receiver_sent = _make_sync_pair(
            receiver,
            sender,
            max_headers=4,
            max_blocks=2,
            on_block_applied=applied.append,
        )
        client.request_sync("peer:test")

        statuses = _pump(client, server, receiver_out, sender_out)

        assert all(status is None for status in statuses)
        assert receiver.last_block.hash == sender.last_block.hash
        assert [block.index for block in applied] == [1, 2, 3, 4, 5]
        # The final getheaders returns an empty batch, which ends the sync.
        assert [m["type"] for m in receiver_sent].count("getheaders") == 3
        assert [
            len(m["data"]["hashes"]) for m in receiver_sent if m["type"] == "getblocks"
        ] == [2, 2, 1]

    def test_short_header_batches_continue_until_empty(self, genesis_file):
        path, _, pk = genesis_file
        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)
        for _ in range(5):
            assert mine_and_process_block(sender, Mempool(), pk) is not None

        header_size = max(
            len(
                canonical_json_dumps(BlockHeader.from_block(block).to_dict()).encode(
                    "utf-8"
                )
            )
            + 1
            for block in sender.chain[1:]
        )
        request_id = "0" * 32
        envelope = len(
            canonical_json_dumps(
                {
                    "type": "headers",
                    "data": {"headers": [], "request_id": request_id},
                }
            ).encode("utf-8")
        )
        client, server, receiver_out, sender_out, receiver_sent = _make_sync_pair(
            receiver,
            sender,
            max_headers=10,
            max_blocks=2,
            server_kwargs={
                "max_headers": 10,
                "max_response_bytes": envelope + header_size * 2,
            },
        )
        client.request_sync("peer:test")

        statuses = _pump(client, server, receiver_out, sender_out)

        assert all(status is None for status in statuses)
        assert receiver.last_block.hash == sender.last_block.hash
        # Two headers fit per batch, so 5 blocks need 2 + 2 + 1 + an empty end.
        assert [m["type"] for m in receiver_sent].count("getheaders") == 4

    def test_invalid_headers_stop_before_any_body_download(self, genesis_file):
        path, _, pk = genesis_file
        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)
        for _ in range(2):
            assert mine_and_process_block(sender, Mempool(), pk) is not None
        sender.chain[1].hash = "f" * 64

        client, server, receiver_out, sender_out, receiver_sent = _make_sync_pair(
            receiver,
            sender,
        )
        client.request_sync("peer:test")

        peer, request = receiver_out.pop(0)
        assert server.handle_message(peer, request["type"], request["data"]) is None

        peer, response = sender_out.pop(0)
        status = client.handle_message(peer, response["type"], response["data"])

        assert status == ValidationStatus.MALFORMED
        assert [m["type"] for m in receiver_sent] == ["getheaders"]
        assert receiver.last_block.index == 0
        assert not client._sessions

    def test_local_tip_change_restarts_sync_without_penalty(self, genesis_file):
        path, _, pk = genesis_file
        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)
        for _ in range(2):
            assert mine_and_process_block(sender, Mempool(), pk) is not None

        client, server, receiver_out, sender_out, receiver_sent = _make_sync_pair(
            receiver,
            sender,
            max_blocks=2,
        )
        client.request_sync("peer:test")

        peer, request = receiver_out.pop(0)
        assert server.handle_message(peer, request["type"], request["data"]) is None
        peer, response = sender_out.pop(0)
        assert client.handle_message(peer, response["type"], response["data"]) is None

        # A broadcast advances our tip while the body request is outstanding.
        assert mine_and_process_block(receiver, Mempool(), pk) is not None

        peer, request = receiver_out.pop(0)
        assert server.handle_message(peer, request["type"], request["data"]) is None
        peer, response = sender_out.pop(0)
        status = client.handle_message(peer, response["type"], response["data"])

        assert status is None
        assert not client._sessions
        assert [m["type"] for m in receiver_sent].count("getheaders") == 2
        assert receiver_out[-1][1]["type"] == "getheaders"

    def test_null_block_drops_the_session(self, genesis_file):
        path, _, pk = genesis_file
        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)
        assert mine_and_process_block(sender, Mempool(), pk) is not None

        client, server, receiver_out, sender_out, _ = _make_sync_pair(
            receiver,
            sender,
        )
        client.request_sync("peer:test")

        peer, request = receiver_out.pop(0)
        assert server.handle_message(peer, request["type"], request["data"]) is None
        peer, response = sender_out.pop(0)
        assert client.handle_message(peer, response["type"], response["data"]) is None

        peer, request = receiver_out.pop(0)
        assert server.handle_message(peer, request["type"], request["data"]) is None
        peer, response = sender_out.pop(0)
        response["data"]["blocks"] = [None]
        status = client.handle_message(peer, response["type"], response["data"])

        assert status == ValidationStatus.MALFORMED
        assert not client._sessions
        assert not client._pending

    def test_fork_sync_reorgs_and_returns_orphans(self, genesis_file):
        path, sk, pk = genesis_file
        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)

        receiver_pool = Mempool()
        tx_a = Transaction(
            sender=pk,
            receiver="a" * 64,
            amount=10,
            nonce=0,
            gas_limit=1,
            fee_per_gas=1,
        )
        tx_a.sign(sk)
        receiver_pool.add_transaction(tx_a)
        assert mine_and_process_block(receiver, receiver_pool, pk) is not None

        sender_pool = Mempool()
        tx_b = Transaction(
            sender=pk,
            receiver="b" * 64,
            amount=20,
            nonce=0,
            gas_limit=1,
            fee_per_gas=1,
        )
        tx_b.sign(sk)
        sender_pool.add_transaction(tx_b)
        assert mine_and_process_block(sender, sender_pool, pk) is not None

        tx_c = Transaction(
            sender=pk,
            receiver="c" * 64,
            amount=30,
            nonce=1,
            gas_limit=1,
            fee_per_gas=1,
        )
        tx_c.sign(sk)
        sender_pool.add_transaction(tx_c)
        assert mine_and_process_block(sender, sender_pool, pk) is not None

        orphans = []
        client, server, receiver_out, sender_out, _ = _make_sync_pair(
            receiver,
            sender,
            on_orphans=lambda txs: orphans.extend(txs),
        )
        client.request_sync("peer:test")

        statuses = _pump(client, server, receiver_out, sender_out)

        assert all(status is None for status in statuses)
        assert receiver.get_total_work() > 0
        assert receiver.last_block.hash == sender.last_block.hash
        assert receiver.state.accounts == sender.state.accounts
        assert [tx.tx_id for tx in orphans] == [tx_a.tx_id]

    def test_deep_fork_accumulates_headers_across_batches(self, genesis_file):
        path, _, pk = genesis_file
        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)
        for _ in range(6):
            assert mine_and_process_block(receiver, Mempool(), pk) is not None
        for _ in range(8):
            assert mine_and_process_block(sender, Mempool(), pk) is not None

        client, server, receiver_out, sender_out, receiver_sent = _make_sync_pair(
            receiver,
            sender,
            max_headers=2,
            max_blocks=3,
        )
        client.request_sync("peer:test")

        statuses = _pump(client, server, receiver_out, sender_out)

        assert all(status is None for status in statuses)
        assert receiver.last_block.hash == sender.last_block.hash
        assert receiver.get_total_work() == sender.get_total_work()
        assert receiver.state.accounts == sender.state.accounts
        assert [m["type"] for m in receiver_sent].count("getheaders") == 5
        assert [
            len(m["data"]["hashes"]) for m in receiver_sent if m["type"] == "getblocks"
        ] == [3, 3, 2]
        assert not client._sessions
        assert not client._pending

    def test_fork_header_accumulation_is_bounded(self):
        receiver = _fake_chain(10)
        sender = _fake_branch(receiver.chain[0], 12)
        client, server, receiver_out, sender_out, _ = _make_sync_pair(
            receiver,
            sender,
            max_headers=1,
            max_blocks=2,
            max_fork_headers=2,
        )
        client.request_sync("peer:test")

        statuses = _pump(client, server, receiver_out, sender_out)

        # Exceeding the local retention cap is not a peer protocol violation.
        assert all(status is None for status in statuses)
        assert not client._sessions
        assert not client._pending

    def test_fork_header_cap_applies_to_the_first_batch(self):
        receiver = _fake_chain(10)
        sender = _fake_branch(receiver.chain[0], 12)
        client, server, receiver_out, sender_out, receiver_sent = _make_sync_pair(
            receiver,
            sender,
            max_headers=2,
            max_fork_headers=1,
        )
        client.request_sync("peer:test")

        statuses = _pump(client, server, receiver_out, sender_out)

        assert all(status is None for status in statuses)
        assert not client._sessions
        assert not [m for m in receiver_sent if m["type"] == "getblocks"]

    def test_fork_body_memory_is_bounded(self, genesis_file):
        path, _, pk = genesis_file
        template = Blockchain(genesis_path=path)
        shared = _mine_empty_block(template, pk, template.last_block.timestamp + 1000)
        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)
        assert receiver.add_block(shared) == ValidationStatus.VALID
        assert sender.add_block(shared) == ValidationStatus.VALID
        local = _mine_empty_block(receiver, pk, shared.timestamp + 1000)
        sender_2 = _mine_empty_block(sender, pk, shared.timestamp + 1000)
        _mine_empty_block(sender, pk, sender_2.timestamp + 1000)

        client, server, receiver_out, sender_out, receiver_sent = _make_sync_pair(
            receiver,
            sender,
            max_blocks=2,
            max_fork_body_bytes=1,
        )
        client.request_sync("peer:test")

        statuses = _pump(client, server, receiver_out, sender_out)

        assert all(status is None for status in statuses)
        assert not client._sessions
        assert not client._pending
        assert receiver.last_block.hash == local.hash
        assert [m["type"] for m in receiver_sent].count("getblocks") == 1

    def test_fork_race_with_local_tip_restarts_without_penalty(self, genesis_file):
        path, _, pk = genesis_file
        template = Blockchain(genesis_path=path)
        shared = _mine_empty_block(template, pk, template.last_block.timestamp + 1000)
        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)
        assert receiver.add_block(shared) == ValidationStatus.VALID
        assert sender.add_block(shared) == ValidationStatus.VALID

        local = _mine_empty_block(receiver, pk, shared.timestamp + 1000)
        sender_2 = _mine_empty_block(sender, pk, shared.timestamp + 1000)
        _mine_empty_block(sender, pk, sender_2.timestamp + 1000)

        client, server, receiver_out, sender_out, receiver_sent = _make_sync_pair(
            receiver,
            sender,
            max_blocks=1,
        )
        client.request_sync("peer:test")

        peer, request = receiver_out.pop(0)
        assert server.handle_message(peer, request["type"], request["data"]) is None
        peer, response = sender_out.pop(0)
        assert client.handle_message(peer, response["type"], response["data"]) is None

        peer, request = receiver_out.pop(0)
        assert request["type"] == "getblocks"
        assert server.handle_message(peer, request["type"], request["data"]) is None
        peer, response = sender_out.pop(0)
        assert client.handle_message(peer, response["type"], response["data"]) is None

        # The local chain gains more work while the fork bodies download.
        next_local = _mine_empty_block(receiver, pk, local.timestamp + 1000)
        final_local = _mine_empty_block(receiver, pk, next_local.timestamp + 1000)

        peer, request = receiver_out.pop(0)
        assert request["type"] == "getblocks"
        assert server.handle_message(peer, request["type"], request["data"]) is None
        peer, response = sender_out.pop(0)
        status = client.handle_message(peer, response["type"], response["data"])

        assert status is None
        assert not client._sessions
        assert receiver.last_block.hash == final_local.hash
        assert [m["type"] for m in receiver_sent].count("getheaders") == 2
        assert receiver_out[-1][1]["type"] == "getheaders"

    def test_block_retry_preserves_the_batch_tip_and_restarts(self, genesis_file):
        path, _, pk = genesis_file
        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)
        for _ in range(2):
            assert mine_and_process_block(sender, Mempool(), pk) is not None

        now = [0.0]
        client, server, receiver_out, sender_out, _ = _make_sync_pair(
            receiver,
            sender,
            clock=lambda: now[0],
        )
        client.request_sync("peer:test")

        peer, request = receiver_out.pop(0)
        assert server.handle_message(peer, request["type"], request["data"]) is None
        peer, response = sender_out.pop(0)
        assert client.handle_message(peer, response["type"], response["data"]) is None
        peer, request = receiver_out.pop(0)
        assert request["type"] == "getblocks"
        batch = list(request["data"]["hashes"])

        # A broadcast adds the first requested block before the retry.
        assert receiver.add_block(sender.chain[1]) == ValidationStatus.VALID

        now[0] = 6.0
        client.maintain()
        peer, retry = receiver_out.pop(0)
        assert retry["type"] == "getblocks"
        assert retry["data"]["hashes"] == batch

        # Answer the retried batch with the original request's response.
        assert server.handle_message(peer, request["type"], request["data"]) is None
        peer, response = sender_out.pop(0)
        status = client.handle_message(peer, response["type"], response["data"])

        assert status is None
        assert not client._sessions
        assert receiver.last_block.hash == sender.chain[1].hash
        assert receiver_out[-1][1]["type"] == "getheaders"

    def test_fork_sync_uses_the_forks_target_sequence(self, genesis_file):
        path, _, pk = genesis_file
        template = Blockchain(genesis_path=path)
        shared = _mine_empty_block(template, pk, template.last_block.timestamp + 1000)

        receiver = Blockchain(genesis_path=path)
        sender = Blockchain(genesis_path=path)
        assert receiver.add_block(shared) == ValidationStatus.VALID
        assert sender.add_block(shared) == ValidationStatus.VALID

        # The local branch mines faster than the target, lowering its target.
        receiver_2 = _mine_empty_block(receiver, pk, shared.timestamp + 500)
        _mine_empty_block(receiver, pk, receiver_2.timestamp + 1000)

        # The competing branch mines slower, keeping the maximum target, and
        # ends up both longer and heavier.
        sender_2 = _mine_empty_block(sender, pk, shared.timestamp + 2000)
        sender_3 = _mine_empty_block(sender, pk, sender_2.timestamp + 1000)
        _mine_empty_block(sender, pk, sender_3.timestamp + 1000)

        local_target = receiver.current_target
        local_avg = receiver.avg_block_time
        assert local_target != sender.current_target
        assert local_avg != sender.avg_block_time

        client, server, receiver_out, sender_out, _ = _make_sync_pair(
            receiver,
            sender,
        )
        client.request_sync("peer:test")

        statuses = _pump(client, server, receiver_out, sender_out)

        assert all(status is None for status in statuses)
        assert receiver.last_block.hash == sender.last_block.hash
        assert receiver.current_target == sender.current_target
        assert receiver.avg_block_time == sender.avg_block_time
        assert receiver.current_target != local_target
        assert receiver.avg_block_time != local_avg
        assert receiver.state.accounts == sender.state.accounts
