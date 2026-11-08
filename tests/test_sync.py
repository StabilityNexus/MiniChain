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


def _fake_chain(length):
    blocks = []
    for index in range(length):
        block = Block(
            index=index,
            previous_hash="0" * 64 if index == 0 else blocks[-1].hash,
            target=MAX_TARGET,
            timestamp=1_700_000_000_000 + index,
        )
        block.hash = block.compute_hash()
        blocks.append(block)
    return SimpleNamespace(chain=blocks)


def _make_sync_pair(receiver_chain, sender_chain, **client_kwargs):
    receiver_outbox = []
    sender_outbox = []
    receiver_sent = []

    def receiver_send(peer, message):
        receiver_outbox.append((peer, message))
        receiver_sent.append(message)

    def sender_send(peer, message):
        sender_outbox.append((peer, message))

    client = SyncManager(receiver_chain, receiver_send, **client_kwargs)
    server = SyncManager(sender_chain, sender_send)
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
            len(canonical_json_dumps(BlockHeader.from_block(chain.chain[1]).to_dict()))
            + 1
        )
        manager = SyncManager(
            chain,
            lambda *_: None,
            max_headers=5,
            max_response_bytes=512 + header_size * 2 + 10,
        )

        response = manager.build_headers_response(
            {"locator": [chain.chain[0].hash], "max": 5}
        )

        assert len(response["data"]["headers"]) == 2
        assert [h["index"] for h in response["data"]["headers"]] == [1, 2]

    def test_block_response_is_an_ordered_contiguous_prefix(self):
        chain = _fake_chain(5)
        block_size = len(canonical_json_dumps(chain.chain[1].to_dict())) + 1
        manager = SyncManager(
            chain,
            lambda *_: None,
            max_blocks=4,
            max_response_bytes=512 + block_size * 3 + 10,
        )

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
        assert manager.handle_message("peer:a", "getheaders", request) == (
            ValidationStatus.FAILED
        )
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
        assert [m["type"] for m in receiver_sent].count("getheaders") == 2
        assert [
            len(m["data"]["hashes"]) for m in receiver_sent if m["type"] == "getblocks"
        ] == [2, 2, 1]

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
