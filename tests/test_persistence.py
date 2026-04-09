"""
Tests for chain persistence (save / load round-trip).
"""

import json
import os
import shutil
import tempfile
import unittest

from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from minichain import Block, Blockchain, Transaction, mine_block
from minichain.persistence import load, save


def _make_keypair():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


class TestPersistence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # Helpers

    def _chain_with_tx(self):
        """Return a Blockchain that has one mined block with a transfer."""
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

    # --- Basic save/load ---

    def test_save_creates_file(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "data.json")))

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

    # --- State recomputation ---

    def test_state_recomputed_from_blocks(self):
        """Balances must be recomputed by replaying blocks, not from a file."""
        bc, alice_pk, bob_pk = self._chain_with_tx()
        save(bc, path=self.tmpdir)

        restored = load(path=self.tmpdir)
        # Alice started with 100, sent 30 → 70
        self.assertEqual(
            restored.state.get_account(alice_pk)["balance"],
            bc.state.get_account(alice_pk)["balance"],
        )
        # Bob received 30
        self.assertEqual(
            restored.state.get_account(bob_pk)["balance"],
            bc.state.get_account(bob_pk)["balance"],
        )

    # --- Integrity verification ---

    def test_tampered_hash_rejected(self):
        """Loading a chain with a tampered block hash must raise ValueError."""
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)

        # Tamper with block hash
        chain_path = os.path.join(self.tmpdir, "data.json")
        with open(chain_path, "r") as f:
            data = json.load(f)
        data["chain"][1]["hash"] = "deadbeef" * 8
        with open(chain_path, "w") as f:
            json.dump(data, f)

        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    def test_broken_linkage_rejected(self):
        """Loading a chain with broken previous_hash linkage must raise."""
        bc, _, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)

        chain_path = os.path.join(self.tmpdir, "data.json")
        with open(chain_path, "r") as f:
            data = json.load(f)
        data["chain"][1]["previous_hash"] = "0" * 64 + "ff"
        with open(chain_path, "w") as f:
            json.dump(data, f)

        with self.assertRaises(ValueError):
            load(path=self.tmpdir)

    # --- Crash safety ---

    def test_corrupted_json_raises(self):
        """Half-written JSON must raise an error, not silently corrupt."""
        bc = Blockchain()
        save(bc, path=self.tmpdir)

        # Corrupt the file
        chain_path = os.path.join(self.tmpdir, "data.json")
        with open(chain_path, "w") as f:
            f.write('{"truncated": ')  # invalid JSON

        with self.assertRaises(json.JSONDecodeError):
            load(path=self.tmpdir)

    def test_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load(path=self.tmpdir)  # nothing saved yet

    # --- Chain continuity after load ---

    def test_loaded_chain_can_add_new_block(self):
        """Restored chain must still accept new valid blocks."""
        bc, alice_pk, bob_pk = self._chain_with_tx()
        save(bc, path=self.tmpdir)

        restored = load(path=self.tmpdir)

        # Build a second transfer using a new key
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


if __name__ == "__main__":
    unittest.main()
