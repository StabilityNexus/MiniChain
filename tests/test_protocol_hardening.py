import unittest

from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from minichain import Block, Mempool, P2PNetwork, State, Transaction, calculate_hash
from minichain.serialization import canonical_json_dumps


class TestDeterministicConsensus(unittest.TestCase):
    def test_canonical_json_is_order_independent(self):
        left = {"b": 2, "a": 1, "nested": {"z": 3, "x": 4}}
        right = {"nested": {"x": 4, "z": 3}, "a": 1, "b": 2}

        self.assertEqual(canonical_json_dumps(left), canonical_json_dumps(right))
        self.assertEqual(calculate_hash(left), calculate_hash(right))

    def test_block_hash_matches_compute_hash(self):
        block = Block(index=1, previous_hash="abc", transactions=[], timestamp=1234567890)
        block.difficulty = 2
        block.nonce = 7

        self.assertEqual(block.compute_hash(), calculate_hash(block.to_header_dict()))


class TestMempoolNonceQueues(unittest.TestCase):
    def setUp(self):
        self.state = State()
        self.sender_sk = SigningKey.generate()
        self.sender_pk = self.sender_sk.verify_key.encode(encoder=HexEncoder).decode()
        self.receiver_pk = SigningKey.generate().verify_key.encode(encoder=HexEncoder).decode()
        self.state.credit_mining_reward(self.sender_pk, 100)

    def _signed_tx(self, nonce, amount=1, timestamp=None) -> Transaction:
        tx = Transaction(
            sender=self.sender_pk,
            receiver=self.receiver_pk,
            amount=amount,
            nonce=nonce,
            timestamp=timestamp,
        )
        tx.sign(self.sender_sk)
        return tx

    def test_ready_transactions_preserve_sender_nonce_order(self):
        mempool = Mempool()
        late_tx = self._signed_tx(1, timestamp=2000)
        early_tx = self._signed_tx(0, timestamp=1000)

        self.assertTrue(mempool.add_transaction(late_tx))
        self.assertTrue(mempool.add_transaction(early_tx))

        selected = mempool.get_transactions_for_block(self.state)

        self.assertEqual([tx.nonce for tx in selected], [0, 1])
        self.assertEqual(len(mempool), 2)
        mempool.remove_transactions(selected)
        self.assertEqual(len(mempool), 0)

    def test_gap_transactions_stay_waiting(self):
        mempool = Mempool()
        ready_tx = self._signed_tx(0, timestamp=1000)
        waiting_tx = self._signed_tx(2, timestamp=3000)

        self.assertTrue(mempool.add_transaction(ready_tx))
        self.assertTrue(mempool.add_transaction(waiting_tx))

        selected = mempool.get_transactions_for_block(self.state)

        self.assertEqual([tx.nonce for tx in selected], [0])
        mempool.remove_transactions(selected)
        self.assertEqual(len(mempool), 1)

        self.state.apply_transaction(ready_tx)
        middle_tx = self._signed_tx(1, timestamp=2000)
        self.assertTrue(mempool.add_transaction(middle_tx))

        selected = mempool.get_transactions_for_block(self.state)
        self.assertEqual([tx.nonce for tx in selected], [1, 2])

    def test_remove_transactions_keeps_other_pending(self):
        mempool = Mempool()
        tx0 = self._signed_tx(0, timestamp=1000)
        tx1 = self._signed_tx(1, timestamp=2000)

        self.assertTrue(mempool.add_transaction(tx0))
        self.assertTrue(mempool.add_transaction(tx1))
        mempool.remove_transactions([tx0])

        self.assertEqual(len(mempool), 1)
        self.assertFalse(mempool.add_transaction(self._signed_tx(1, timestamp=3000)))

        self.assertEqual(mempool.get_transactions_for_block(self.state), [])
        self.state.apply_transaction(tx0)
        selected = mempool.get_transactions_for_block(self.state)
        self.assertEqual([tx.nonce for tx in selected], [1])
        self.assertEqual(selected[0].tx_id, tx1.tx_id)

    def test_selection_respects_per_block_cap(self):
        mempool = Mempool()
        txs = [self._signed_tx(nonce=i, timestamp=1000 + i) for i in range(4)]
        for tx in txs:
            self.assertTrue(mempool.add_transaction(tx))

        cap = 2
        selected = mempool.get_transactions_for_block(self.state, max_count=cap)
        self.assertEqual(len(selected), cap)
        self.assertEqual([tx.nonce for tx in selected], [0, 1])
        self.assertEqual(len(mempool), 4)

        mempool.remove_transactions(selected)
        self.assertEqual(len(mempool), 2)
        self.state.apply_transaction(txs[0])
        self.state.apply_transaction(txs[1])

        remaining = mempool.get_transactions_for_block(self.state)
        self.assertEqual([tx.nonce for tx in remaining], [2, 3])


class TestP2PValidationAndDedup(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_message_schema_is_rejected(self):
        network = P2PNetwork()

        invalid_message = {"type": "tx", "data": {"sender": "abc"}}
        self.assertFalse(network._validate_message(invalid_message))

    async def test_block_schema_accepts_current_block_wire_format(self):
        sender_sk = SigningKey.generate()
        sender_pk = sender_sk.verify_key.encode(encoder=HexEncoder).decode()
        receiver_pk = SigningKey.generate().verify_key.encode(encoder=HexEncoder).decode()

        tx = Transaction(sender_pk, receiver_pk, 1, 0, timestamp=123)
        tx.sign(sender_sk)

        block = Block(index=1, previous_hash="0" * 64, transactions=[tx], timestamp=456, difficulty=2)
        block.nonce = 9
        block.hash = block.compute_hash()

        network = P2PNetwork()
        message = {"type": "block", "data": block.to_dict()}

        self.assertTrue(network._validate_message(message))

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
                "difficulty": 2,
                "nonce": 1,
                "hash": "f" * 64,
            },
        }

        self.assertFalse(network._is_duplicate("tx", tx_message["data"]))
        network._mark_seen("tx", tx_message["data"])
        tx_equivalent = dict(tx_message["data"])
        self.assertTrue(network._is_duplicate("tx", tx_equivalent))

        self.assertFalse(network._is_duplicate("block", block_message["data"]))
        network._mark_seen("block", block_message["data"])
        block_equivalent = dict(block_message["data"])
        block_equivalent["transactions"] = [dict(tx_message["data"])]
        self.assertTrue(network._is_duplicate("block", block_equivalent))
