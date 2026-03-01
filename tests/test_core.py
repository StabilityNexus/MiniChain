import unittest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Transaction, Blockchain, State # Removed unused imports

class TestCore(unittest.TestCase):
    def setUp(self):
        self.state = State()
        self.chain = Blockchain()
        
        # Setup Alice
        self.alice_sk = SigningKey.generate()
        self.alice_pk = self.alice_sk.verify_key.encode(encoder=HexEncoder).decode()
        
        # Setup Bob
        self.bob_sk = SigningKey.generate()
        self.bob_pk = self.bob_sk.verify_key.encode(encoder=HexEncoder).decode()

    def test_genesis_block(self):
        """Check if genesis block is created correctly."""
        self.assertEqual(len(self.chain.chain), 1)
        self.assertEqual(self.chain.last_block.index, 0)
        self.assertEqual(self.chain.last_block.previous_hash, "0")

    def test_transaction_signature(self):
        """Check that valid signatures pass and invalid ones fail."""
        tx = Transaction(self.alice_pk, self.bob_pk, 10, 0)
        tx.sign(self.alice_sk)
        self.assertTrue(tx.verify())

        # Tamper with amount
        tx.amount = 100
        self.assertFalse(tx.verify())

    def test_state_transfer(self):
        """Test simple balance transfer."""
        # 1. Credit Alice
        self.state.credit_mining_reward(self.alice_pk, 100)
        
        # 2. Transfer
        tx = Transaction(self.alice_pk, self.bob_pk, 40, 0)
        tx.sign(self.alice_sk)
        
        result = self.state.apply_transaction(tx)
        self.assertTrue(result)
        
        # 3. Check Balances
        self.assertEqual(self.state.get_account(self.alice_pk)['balance'], 60)
        self.assertEqual(self.state.get_account(self.bob_pk)['balance'], 40)

    def test_insufficient_funds(self):
        """Test that you cannot spend more than you have."""
        self.state.credit_mining_reward(self.alice_pk, 10)
        
        tx = Transaction(self.alice_pk, self.bob_pk, 50, 0)
        tx.sign(self.alice_sk)
        
        result = self.state.apply_transaction(tx)
        self.assertFalse(result)
        
        self.assertEqual(self.state.get_account(self.alice_pk)['balance'], 10)
        self.assertEqual(self.state.get_account(self.bob_pk)['balance'], 0)

    def test_transaction_wrong_signer(self):
        """Test that a transaction signed with the wrong key is invalid."""
        tx = Transaction(self.alice_pk, self.bob_pk, 10, 0) # Alice is sender
        # Attempt to sign with Bob's key, which should raise ValueError
        with self.assertRaises(ValueError) as cm:
            tx.sign(self.bob_sk)
        self.assertIn("Signing key does not match sender", str(cm.exception))


if __name__ == '__main__':
    unittest.main()