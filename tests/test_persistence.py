"""
Tests for chain persistence (save / load round-trip).
"""

import os
import tempfile
import unittest

from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Blockchain, Transaction, Block, mine_block
from minichain.persistence import save, load


def _make_keypair():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


class TestPersistence(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

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

    # Tests

    def test_save_creates_files(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)

        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "blockchain.json")))
        self.assertTrue(os.path.exists(os.path.join(self.tmpdir, "state.json")))

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

    def test_account_balances_preserved(self):
        bc, alice_pk, bob_pk = self._chain_with_tx()
        save(bc, path=self.tmpdir)

        restored = load(path=self.tmpdir)
        self.assertEqual(
            bc.state.get_account(alice_pk)["balance"],
            restored.state.get_account(alice_pk)["balance"],
        )
        self.assertEqual(
            bc.state.get_account(bob_pk)["balance"],
            restored.state.get_account(bob_pk)["balance"],
        )

    def test_account_nonces_preserved(self):
        bc, alice_pk, _ = self._chain_with_tx()
        save(bc, path=self.tmpdir)

        restored = load(path=self.tmpdir)
        self.assertEqual(
            bc.state.get_account(alice_pk)["nonce"],
            restored.state.get_account(alice_pk)["nonce"],
        )

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

    def test_loaded_chain_can_add_new_block(self):
        """Restored chain must still accept new valid blocks."""
        bc, alice_pk, bob_pk = self._chain_with_tx()
        save(bc, path=self.tmpdir)

        restored = load(path=self.tmpdir)

        # Build a second transfer on top of the loaded chain
        alice_sk, alice_pk2 = _make_keypair()
        _, carol_pk = _make_keypair()
        restored.state.credit_mining_reward(alice_pk2, 50)

        tx2 = Transaction(alice_pk2, carol_pk, 10, 0)
        tx2.sign(alice_sk)

        block2 = Block(
            index=len(restored.chain),
            previous_hash=restored.last_block.hash,
            transactions=[tx2],
            difficulty=1,
        )
        mine_block(block2, difficulty=1)

        self.assertTrue(restored.add_block(block2))
        self.assertEqual(len(restored.chain), len(bc.chain) + 1)

    def test_load_missing_file_raises(self):
        with self.assertRaises(FileNotFoundError):
            load(path=self.tmpdir)  # nothing saved yet

    def test_genesis_only_chain(self):
        bc = Blockchain()
        save(bc, path=self.tmpdir)
        restored = load(path=self.tmpdir)

        self.assertEqual(len(restored.chain), 1)
        self.assertEqual(restored.chain[0].hash, "0" * 64)

    def test_contract_storage_preserved(self):
        """Contract accounts and storage survive a save/load cycle."""
        from minichain import State, Transaction as Tx
        bc = Blockchain()

        deployer_sk, deployer_pk = _make_keypair()
        bc.state.credit_mining_reward(deployer_pk, 100)

        code = "storage['hits'] = storage.get('hits', 0) + 1"
        tx_deploy = Tx(deployer_pk, None, 0, 0, data=code)
        tx_deploy.sign(deployer_sk)
        contract_addr = bc.state.apply_transaction(tx_deploy)
        self.assertIsInstance(contract_addr, str)

        save(bc, path=self.tmpdir)
        restored = load(path=self.tmpdir)

        contract = restored.state.get_account(contract_addr)
        self.assertEqual(contract["code"], code)
        self.assertEqual(contract["storage"]["hits"], 1)


if __name__ == "__main__":
    unittest.main()
