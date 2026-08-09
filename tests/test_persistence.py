"""Tests for chain persistence (save / load round-trip)."""

import json
import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch, wraps

from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from minichain import Block, Blockchain, Transaction, mine_block
from minichain.persistence import load, persistence_exists, save


DB_FILE = "data.db"
LEGACY_FILE = "data.json"


def _make_keypair():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _chain_with_tx(self):
        """Build a 3-block chain that survives full _apply_block() replay.

        Block 1 (coinbase): alice mines an empty block and earns the mining
        reward — this is the only valid way to introduce fresh funds into a
        chain that will be fully validated during load().

        Block 2 (transfer): alice sends coins to bob.
        """
        bc = Blockchain()
        alice_sk, alice_pk = _make_keypair()
        _, bob_pk = _make_keypair()

        # --- Block 1: coinbase (alice mines, earns mining reward) ---
        from minichain.block import calculate_receipt_root
        from minichain.state import State

        temp_state1 = bc.state.copy()
        temp_state1.chain_id = bc.chain_id
        # Mining reward applied inside _apply_block via block.miner
        temp_state1.credit_mining_reward(alice_pk, reward=temp_state1.DEFAULT_MINING_REWARD)
        coinbase_block = Block(
            index=1,
            previous_hash=bc.last_block.hash,
            transactions=[],
            difficulty=bc.current_difficulty,
            state_root=temp_state1.state_root(),
            receipt_root=None,
            receipts=[],
            timestamp=bc.last_block.timestamp + bc.target_block_time,
            miner=alice_pk,
        )
        mine_block(coinbase_block)
        result = bc.add_block(coinbase_block)
        self.assertEqual(result.name, "VALID", f"Coinbase block rejected: {result}")

        # --- Block 2: alice sends to bob ---
        tx = Transaction(alice_pk, bob_pk, 1, 0)
        tx.sign(alice_sk)

        temp_state2 = bc.state.copy()
        temp_state2.chain_id = bc.chain_id
        receipt = temp_state2.validate_and_apply(tx)
        self.assertIsNotNone(receipt, "Transaction was rejected during block 2 construction")

        total_fees = getattr(receipt, 'gas_used', 0)
        temp_state2.credit_mining_reward(alice_pk, reward=temp_state2.DEFAULT_MINING_REWARD + total_fees)

        tx_block = Block(
            index=2,
            previous_hash=bc.last_block.hash,
            transactions=[tx],
            difficulty=bc.current_difficulty,
            state_root=temp_state2.state_root(),
            receipt_root=calculate_receipt_root([receipt]),
            receipts=[receipt],
            timestamp=bc.last_block.timestamp + bc.target_block_time,
            miner=alice_pk,
        )
        mine_block(tx_block)
        result = bc.add_block(tx_block)
        self.assertEqual(result.name, "VALID", f"Tx block rejected: {result}")

        return bc, alice_pk, bob_pk

    def test_save_creates_sqlite_file(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, DB_FILE)))
        self.assertTrue(persistence_exists(self.tmpdir))

    def test_chain_length_preserved(self):
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)
        restored = load(path=self.tmpdir)
        self.assertEqual(len(restored.chain), len(bc.chain))

    def test_block_hashes_preserved(self):
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)
        restored = load(path=self.tmpdir)
        for original, loaded in zip(bc.chain, restored.chain):
            self.assertEqual(original.hash, loaded.hash)
            self.assertEqual(original.index, loaded.index)
            self.assertEqual(original.previous_hash, loaded.previous_hash)

    def test_transaction_data_preserved(self):
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)
        restored = load(path=self.tmpdir)
        original_tx = bc.chain[2].transactions[0]
        loaded_tx = restored.chain[2].transactions[0]
        self.assertEqual(original_tx.sender, loaded_tx.sender)
        self.assertEqual(original_tx.receiver, loaded_tx.receiver)
        self.assertEqual(original_tx.amount, loaded_tx.amount)
        self.assertEqual(original_tx.nonce, loaded_tx.nonce)
        self.assertEqual(original_tx.signature, loaded_tx.signature)

    def test_receipt_data_preserved(self):
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)
        restored = load(path=self.tmpdir)
        original_receipt = bc.chain[2].receipts[0]
        loaded_receipt = restored.chain[2].receipts[0]
        self.assertEqual(original_receipt.tx_hash, loaded_receipt.tx_hash)
        self.assertEqual(original_receipt.status, loaded_receipt.status)
        self.assertEqual(original_receipt.gas_used, loaded_receipt.gas_used)
        self.assertEqual(original_receipt.error_message, loaded_receipt.error_message)
        self.assertEqual(original_receipt.contract_address, loaded_receipt.contract_address)

    def test_genesis_only_chain(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        restored = load(path=self.tmpdir)
        self.assertEqual(len(restored.chain), 1)
        self.assertEqual(restored.chain[0].hash, bc.chain[0].hash)

    def test_state_snapshot_preserved(self):
        bc, alice_pk, bob_pk = self._chain_with_tx()
        save(bc, path=self.tmpdir)
        restored = load(path=self.tmpdir)
        self.assertEqual(
            restored.state.get_account(alice_pk)["balance"],
            bc.state.get_account(alice_pk)["balance"],
        )
        self.assertEqual(
            restored.state.get_account(bob_pk)["balance"],
            bc.state.get_account(bob_pk)["balance"],
        )

    def test_tampered_hash_rejected(self):
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT block_json FROM blocks WHERE height = 1").fetchone()
            payload = json.loads(row[0])
            payload["hash"] = "deadbeef" * 8
            conn.execute(
                "UPDATE blocks SET block_json = ? WHERE height = 1",
                (json.dumps(payload),),
            )
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_broken_linkage_rejected(self):
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT block_json FROM blocks WHERE height = 1").fetchone()
            payload = json.loads(row[0])
            payload["previous_hash"] = "0" * 64 + "ff"
            conn.execute(
                "UPDATE blocks SET block_json = ? WHERE height = 1",
                (json.dumps(payload),),
            )
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_corrupted_sqlite_payload_raises(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE blocks SET block_json = ? WHERE height = 0", ("{bad-json",))
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_missing_required_sqlite_table_raises(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            conn.execute("DROP TABLE accounts")
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_truncated_chain_rows_raises_value_error(self):
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            conn.execute("DELETE FROM blocks WHERE height = 1")
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_malformed_block_row_raises_value_error(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE blocks SET block_json = ? WHERE height = 0",
                (json.dumps(["not-a-block-dict"]),),
            )
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_block_missing_required_field_raises_value_error(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT block_json FROM blocks WHERE height = 0").fetchone()
            payload = json.loads(row[0])
            payload.pop("hash", None)
            conn.execute(
                "UPDATE blocks SET block_json = ? WHERE height = 0",
                (json.dumps(payload),),
            )
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_malformed_account_row_raises_value_error(self):
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)
        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE accounts SET account_json = ? WHERE address = ?",
                (json.dumps(["not-an-account-dict"]), next(iter(bc.state.accounts))),
            )
        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load(path=self.tmpdir)
        self.assertFalse(persistence_exists(self.tmpdir))

    def test_loaded_chain_can_add_new_block(self):
        bc, _, bob_pk = self._chain_with_tx()
        save(bc, path=self.tmpdir)
        restored = load(path=self.tmpdir)

        new_sk, new_pk = _make_keypair()
        from minichain.block import calculate_receipt_root as crr
        temp_state0 = restored.state.copy()
        temp_state0.chain_id = restored.chain_id
        temp_state0.credit_mining_reward(new_pk, reward=temp_state0.DEFAULT_MINING_REWARD)
        coinbase_block = Block(
            index=len(restored.chain),
            previous_hash=restored.last_block.hash,
            transactions=[],
            difficulty=restored.current_difficulty,
            state_root=temp_state0.state_root(),
            receipt_root=None,
            receipts=[],
            timestamp=restored.last_block.timestamp + restored.target_block_time,
            miner=new_pk,
        )
        mine_block(coinbase_block)
        from minichain.validators import ValidationStatus
        self.assertEqual(restored.add_block(coinbase_block), ValidationStatus.VALID)

        tx2 = Transaction(new_pk, bob_pk, 1, 0)
        tx2.sign(new_sk)

        temp_state2 = restored.state.copy()
        temp_state2.chain_id = restored.chain_id
        receipt2 = temp_state2.validate_and_apply(tx2)
        self.assertIsNotNone(receipt2)
        total_fees2 = getattr(receipt2, 'gas_used', 0)
        temp_state2.credit_mining_reward(new_pk, reward=temp_state2.DEFAULT_MINING_REWARD + total_fees2)

        from minichain.block import calculate_receipt_root
        block2 = Block(
            index=len(restored.chain),
            previous_hash=restored.last_block.hash,
            transactions=[tx2],
            difficulty=restored.current_difficulty,
            state_root=temp_state2.state_root(),
            receipt_root=calculate_receipt_root([receipt2]),
            receipts=[receipt2],
            timestamp=restored.last_block.timestamp + restored.target_block_time,
            miner=new_pk,
        )
        mine_block(block2)

        self.assertEqual(restored.add_block(block2), ValidationStatus.VALID)
        self.assertEqual(len(restored.chain), len(bc.chain) + 2)

    def test_load_uses_apply_block_pipeline(self):
        """_apply_block() must be called for each non-genesis block during load."""
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)

        call_count = []
        import minichain.chain as chain_module
        original_apply = chain_module.Blockchain._apply_block

        def spying_apply(self_inner, prev_block, block, state, difficulty, avg_block_time):
            call_count.append(block.index)
            return original_apply(self_inner, prev_block, block, state, difficulty, avg_block_time)

        with patch.object(chain_module.Blockchain, "_apply_block", spying_apply):
            restored = load(path=self.tmpdir)

        expected = list(range(1, len(bc.chain)))
        self.assertEqual(sorted(call_count), expected,
                         f"Expected _apply_block calls for blocks {expected}, got {call_count}")
        self.assertEqual(len(restored.chain), len(bc.chain))

    def test_load_rejects_invalid_pow_in_persisted_block(self):
        """A persisted block whose hash fails PoW must be rejected during load."""
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)

        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT block_json FROM blocks WHERE height = 2"
            ).fetchone()
            payload = json.loads(row[0])
            payload["hash"] = "f" * 64
            # We need the hash to equal compute_hash() so deserialization passes,
            # but PoW to fail. Use nonce manipulation.
            from minichain.block import Block as B
            from minichain.pow import calculate_hash
            b = B.from_dict(json.loads(row[0]))
            header = b.to_header_dict()
            nonce = 0
            while True:
                header["nonce"] = nonce
                h = calculate_hash(header)
                if not h.startswith("0"):
                    break
                nonce += 1
            payload["nonce"] = nonce
            payload["hash"] = calculate_hash(header)
            conn.execute(
                "UPDATE blocks SET block_json = ? WHERE height = 2",
                (json.dumps(payload),),
            )

        with self.assertRaises(ValueError) as cm:
            load(path=self.tmpdir)
        self.assertIn("failed validation", str(cm.exception))

    def test_load_rejects_wrong_declared_difficulty(self):
        """A persisted block declaring wrong difficulty must be rejected during load.

        Security property: even if an attacker sets block.difficulty=1 and crafts
        a hash that satisfies difficulty 1, load() must reject it because
        expected_difficulty (chain-computed) is greater.
        """
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)

        db_path = os.path.join(self.tmpdir, DB_FILE)
        with sqlite3.connect(db_path) as conn:
            row = conn.execute(
                "SELECT block_json FROM blocks WHERE height = 2"
            ).fetchone()
            payload = json.loads(row[0])
            payload["difficulty"] = 1
            from minichain.block import Block as B
            from minichain.pow import calculate_hash, mine_block as mb
            import copy
            b = B.from_dict(json.loads(row[0]))
            b.difficulty = 1
            header = b.to_header_dict()
            nonce = 0
            while True:
                header["nonce"] = nonce
                h = calculate_hash(header)
                if h.startswith("0"):
                    break
                nonce += 1
            payload["nonce"] = nonce
            payload["hash"] = h
            conn.execute(
                "UPDATE blocks SET block_json = ? WHERE height = 2",
                (json.dumps(payload),),
            )
        with self.assertRaises(ValueError) as cm:
            load(path=self.tmpdir)
        self.assertIn("failed validation", str(cm.exception))

    def test_legacy_json_load_still_supported(self):
        bc = Blockchain()
        snapshot = {
            "chain": [block.to_dict() for block in bc.chain],
            "state": bc.state.accounts,
        }
        with open(os.path.join(self.tmpdir, LEGACY_FILE), "w", encoding="utf-8") as f:
            json.dump(snapshot, f)

        restored = load(path=self.tmpdir)
        self.assertEqual(len(restored.chain), 1)
        self.assertTrue(persistence_exists(self.tmpdir))

    def test_corrupt_sqlite_falls_back_to_legacy_json(self):
        bc = Blockchain()
        snapshot = {
            "chain": [block.to_dict() for block in bc.chain],
            "state": bc.state.accounts,
        }
        with open(os.path.join(self.tmpdir, LEGACY_FILE), "w", encoding="utf-8") as f:
            json.dump(snapshot, f)

        with open(os.path.join(self.tmpdir, DB_FILE), "wb") as f:
            f.write(b"not-a-valid-sqlite-db")

        restored = load(path=self.tmpdir)
        self.assertEqual(len(restored.chain), 1)
        self.assertEqual(restored.chain[0].hash, bc.chain[0].hash)


if __name__ == "__main__":
    unittest.main()
