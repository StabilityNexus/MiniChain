"""Tests for chain persistence (save / load round-trip)."""

import json
import os
import shutil
import sqlite3
import tempfile
import unittest

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
        bc = Blockchain()
        alice_sk, alice_pk = _make_keypair()
        _, bob_pk = _make_keypair()

        bc.state.credit_mining_reward(alice_pk, 100)

        tx = Transaction(alice_pk, bob_pk, 30, 0)
        tx.sign(alice_sk)

        block = Block(
            index=1,
            previous_hash=bc.last_block.hash,
            transactions=[tx],
            difficulty=1,
        )
        mine_block(block, difficulty=1)
        bc.add_block(block)
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
        original_tx = bc.chain[1].transactions[0]
        loaded_tx = restored.chain[1].transactions[0]
        self.assertEqual(original_tx.sender, loaded_tx.sender)
        self.assertEqual(original_tx.receiver, loaded_tx.receiver)
        self.assertEqual(original_tx.amount, loaded_tx.amount)
        self.assertEqual(original_tx.nonce, loaded_tx.nonce)
        self.assertEqual(original_tx.signature, loaded_tx.signature)

    def test_genesis_only_chain(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        restored = load(path=self.tmpdir)
        self.assertEqual(len(restored.chain), 1)
        self.assertEqual(restored.chain[0].hash, "0" * 64)

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
        restored.state.credit_mining_reward(new_pk, 50)

        tx2 = Transaction(new_pk, bob_pk, 10, 0)
        tx2.sign(new_sk)

        block2 = Block(
            index=len(restored.chain),
            previous_hash=restored.last_block.hash,
            transactions=[tx2],
            difficulty=1,
        )
        mine_block(block2, difficulty=1)

        self.assertTrue(restored.add_block(block2))
        self.assertEqual(len(restored.chain), len(bc.chain) + 1)

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


if __name__ == "__main__":
    unittest.main()
