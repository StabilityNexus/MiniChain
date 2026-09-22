import asyncio
import unittest

from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from minichain import Block, Mempool, P2PNetwork, State, Transaction, calculate_hash
from minichain.node_config import SEEN_CACHE_MAX
from minichain.serialization import canonical_json_dumps
from minichain.validators import ValidationStatus


class TestDeterministicConsensus(unittest.TestCase):
    def test_canonical_json_is_order_independent(self):
        left = {"b": 2, "a": 1, "nested": {"z": 3, "x": 4}}
        right = {"nested": {"x": 4, "z": 3}, "a": 1, "b": 2}

        self.assertEqual(canonical_json_dumps(left), canonical_json_dumps(right))
        self.assertEqual(calculate_hash(left), calculate_hash(right))

    def test_block_hash_matches_compute_hash(self):
        block = Block(index=1, previous_hash="abc", target=2, transactions=[], timestamp=1234567890)
        block.nonce = 7

        self.assertEqual(block.compute_hash(), calculate_hash(block.to_header_dict()))


class TestMempoolQueue(unittest.TestCase):
    def setUp(self):
        self.state = State()
        self.sender_sk = SigningKey.generate()
        self.sender_pk = self.sender_sk.verify_key.encode(encoder=HexEncoder).decode()
        self.receiver_pk = SigningKey.generate().verify_key.encode(encoder=HexEncoder).decode()
        self.state.credit_mining_reward(self.sender_pk, 100)

    def _signed_tx(self, nonce, amount=1, timestamp=None, fee_per_gas=0) -> Transaction:
        tx = Transaction(
            sender=self.sender_pk,
            receiver=self.receiver_pk,
            amount=amount,
            nonce=nonce,
            timestamp=timestamp,
            fee_per_gas=fee_per_gas,
        )
        tx.sign(self.sender_sk)
        return tx

    def test_transactions_for_block_are_sorted_and_capped(self):
        mempool = Mempool()
        for nonce in range(mempool.transactions_per_block + 5):
            self.assertTrue(mempool.add_transaction(self._signed_tx(nonce, timestamp=5000 + nonce)))

        selected = mempool.get_transactions_for_block()

        self.assertEqual(len(selected), mempool.transactions_per_block)
        self.assertEqual(len(mempool), mempool.transactions_per_block + 5)
        self.assertEqual(
            [tx.timestamp for tx in selected],
            sorted(tx.timestamp for tx in selected),
        )

    def test_same_nonce_replaces_pending_transaction(self):
        mempool = Mempool()
        original_tx = self._signed_tx(0, amount=1, timestamp=1000, fee_per_gas=10)
        replacement_tx = self._signed_tx(0, amount=2, timestamp=2000, fee_per_gas=15)

        self.assertTrue(mempool.add_transaction(original_tx))
        self.assertTrue(mempool.add_transaction(replacement_tx))

        selected = mempool.get_transactions_for_block()
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].amount, 2)

    def test_remove_transactions_keeps_other_pending(self):
        mempool = Mempool()
        tx0 = self._signed_tx(0, timestamp=1000)
        tx1 = self._signed_tx(1, timestamp=2000)

        self.assertTrue(mempool.add_transaction(tx0))
        self.assertTrue(mempool.add_transaction(tx1))
        mempool.remove_transactions([tx0])
        selected = mempool.get_transactions_for_block()

        self.assertEqual(len(mempool), 1)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].tx_id, tx1.tx_id)

    def test_remove_transactions_by_sender_nonce_when_tx_id_differs(self):
        mempool = Mempool()
        local_tx = self._signed_tx(0, amount=1, timestamp=1000)
        remote_confirmed_tx = self._signed_tx(0, amount=2, timestamp=2000)

        self.assertTrue(mempool.add_transaction(local_tx))
        mempool.remove_transactions([remote_confirmed_tx])

        self.assertEqual(len(mempool), 0)


class TestP2PValidationAndDedup(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_message_schema_is_rejected(self):
        invalid_payload = {"sender": "abc"}
        with self.assertRaises(Exception):
            Transaction.from_dict(invalid_payload)

    async def test_block_schema_accepts_current_block_wire_format(self):
        sender_sk = SigningKey.generate()
        sender_pk = sender_sk.verify_key.encode(encoder=HexEncoder).decode()
        receiver_pk = SigningKey.generate().verify_key.encode(encoder=HexEncoder).decode()

        tx = Transaction(sender_pk, receiver_pk, 1, 0, timestamp=1600000000000)
        tx.sign(sender_sk)

        from minichain.receipt import Receipt
        from minichain.block import calculate_receipt_root
        receipt = Receipt(tx_hash=tx.tx_id, status=1)

        block = Block(
            index=1, 
            previous_hash="0" * 64, 
            transactions=[tx], 
            timestamp=1600000000000, 
            target=int("F"*64, 16), 
            state_root="0"*64,
            receipts=[receipt],
            receipt_root=calculate_receipt_root([receipt])
        )
        block.nonce = 9
        block.hash = block.compute_hash()

        parsed_block = Block.from_dict(block.to_dict())
        self.assertEqual(parsed_block.hash, block.hash)

    async def test_duplicate_tx_and_block_detection(self):
        network = P2PNetwork()

        tx_message = {
            "type": "tx",
            "data": {
                "sender": "a" * 64,
                "receiver": "b" * 64,
                "amount": 1,
                "nonce": 0,
                "data": None,
                "timestamp": 123,
                "signature": "c" * 128,
            },
        }
        block_message = {
            "type": "block",
            "data": {
                "index": 1,
                "previous_hash": "0" * 64,
                "transactions": [tx_message["data"]],
                "timestamp": 123,
                "target": int("F"*64, 16),
                "nonce": 1,
                "hash": "f" * 64,
            },
        }

        self.assertFalse(network._is_duplicate("tx", tx_message["data"]))
        network._mark_seen("tx", tx_message["data"])
        self.assertTrue(network._is_duplicate("tx", tx_message["data"]))

        self.assertFalse(network._is_duplicate("block", block_message["data"]))
        network._mark_seen("block", block_message["data"])
        self.assertTrue(network._is_duplicate("block", block_message["data"]))



class TestP2PRelayAndPeers(unittest.IsolatedAsyncioTestCase):
    """
    A node relays content it accepts, so gossip travels past one hop, but never
    back to the peer it came from and never anything it rejected.
    """

    SOURCE = "peer:source"
    TX_DATA = {
        "sender": "a" * 64, "receiver": "b" * 64, "amount": 1, "nonce": 0,
        "data": None, "timestamp": 123, "signature": "c" * 128,
    }

    def _network(self, status):
        network = P2PNetwork(malformed_threshold=1000, failed_threshold=1000, invalid_threshold=1000)
        network.loop = asyncio.get_running_loop()

        async def handler(_data):
            return status

        network.register_handler(handler)
        return network

    async def _deliver(self, network, msg_type, data):
        network._to_asyncio.put(("MSG", {"type": msg_type, "data": data, "_peer_addr": self.SOURCE}))
        task = asyncio.create_task(network._asyncio_reader())
        for _ in range(30):
            await asyncio.sleep(0.01)
            if not network._to_trio.empty():
                break
        task.cancel()
        network._to_asyncio.put(("MALFORMED", self.SOURCE))
        try:
            await task
        except asyncio.CancelledError:
            pass
        commands = []
        while not network._to_trio.empty():
            commands.append(network._to_trio.get_nowait())
        return [arg for cmd, arg in commands if cmd == "BROADCAST"]

    async def test_accepted_tx_is_relayed_excluding_sender(self):
        network = self._network(ValidationStatus.VALID)

        broadcasts = await self._deliver(network, "tx", self.TX_DATA)

        self.assertEqual(len(broadcasts), 1)
        payload, exclude = broadcasts[0]
        self.assertEqual(payload, {"type": "tx", "data": self.TX_DATA})
        self.assertEqual(exclude, self.SOURCE)

    async def test_rejected_content_is_never_relayed(self):
        network = self._network(ValidationStatus.INVALID)

        broadcasts = await self._deliver(network, "tx", self.TX_DATA)

        self.assertEqual(broadcasts, [])

    async def test_control_messages_are_never_relayed(self):
        network = self._network(None)

        broadcasts = await self._deliver(network, "hello", {"some": "payload"})

        self.assertEqual(broadcasts, [])

    async def test_duplicate_is_relayed_only_once(self):
        network = self._network(ValidationStatus.VALID)

        first = await self._deliver(network, "tx", self.TX_DATA)
        second = await self._deliver(network, "tx", self.TX_DATA)

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])


class TestSeenCacheIsBounded(unittest.TestCase):
    def test_oldest_entry_is_evicted_past_the_cap(self):
        network = P2PNetwork()

        for i in range(SEEN_CACHE_MAX + 10):
            network._mark_seen("block", {"hash": f"h{i}"})

        self.assertEqual(len(network._seen_block_hashes), SEEN_CACHE_MAX)
        self.assertFalse(network._is_duplicate("block", {"hash": "h0"}))
        self.assertTrue(network._is_duplicate("block", {"hash": f"h{SEEN_CACHE_MAX + 9}"}))
