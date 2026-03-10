"""
tests/test_transaction_signing.py

Unit tests for MiniChain transaction signing and verification.

Covers:
  1. Valid transaction — properly signed tx verifies successfully.
  2. Modified transaction data — tampering after signing breaks verification.
  3. Invalid public key — wrong sender key fails verification.
  4. Replay protection — duplicate nonce is rejected by state validation.
"""

import unittest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Transaction, State


class TestTransactionSigning(unittest.TestCase):

    def setUp(self):
        """Create two wallets and a fresh state before each test."""
        self.alice_sk = SigningKey.generate()
        self.alice_pk = self.alice_sk.verify_key.encode(encoder=HexEncoder).decode()

        self.bob_sk = SigningKey.generate()
        self.bob_pk = self.bob_sk.verify_key.encode(encoder=HexEncoder).decode()

        self.state = State()
        # Fund Alice so state-level tests have a balance to work with
        self.state.credit_mining_reward(self.alice_pk, 100)

    # ------------------------------------------------------------------
    # 1. Valid transaction
    # ------------------------------------------------------------------

    def test_valid_signature_verifies(self):
        """A properly signed transaction must pass signature verification."""
        tx = Transaction(self.alice_pk, self.bob_pk, 10, nonce=0)
        tx.sign(self.alice_sk)

        self.assertTrue(
            tx.verify(),
            "A correctly signed transaction should verify successfully.",
        )

    # ------------------------------------------------------------------
    # 2. Modified transaction data
    # ------------------------------------------------------------------

    def test_tampered_amount_fails_verification(self):
        """Changing `amount` after signing must invalidate the signature."""
        tx = Transaction(self.alice_pk, self.bob_pk, 10, nonce=0)
        tx.sign(self.alice_sk)

        tx.amount = 9999  # tamper

        self.assertFalse(
            tx.verify(),
            "A transaction with a tampered amount must not verify.",
        )

    def test_tampered_receiver_fails_verification(self):
        """Changing `receiver` after signing must invalidate the signature."""
        tx = Transaction(self.alice_pk, self.bob_pk, 10, nonce=0)
        tx.sign(self.alice_sk)

        # Replace receiver with a freshly generated key
        attacker_sk = SigningKey.generate()
        tx.receiver = attacker_sk.verify_key.encode(encoder=HexEncoder).decode()

        self.assertFalse(
            tx.verify(),
            "A transaction with a tampered receiver must not verify.",
        )

    def test_tampered_nonce_fails_verification(self):
        """Changing `nonce` after signing must invalidate the signature."""
        tx = Transaction(self.alice_pk, self.bob_pk, 10, nonce=0)
        tx.sign(self.alice_sk)

        tx.nonce = 99  # tamper

        self.assertFalse(
            tx.verify(),
            "A transaction with a tampered nonce must not verify.",
        )

    # ------------------------------------------------------------------
    # 3. Invalid public key
    # ------------------------------------------------------------------

    def test_wrong_sender_key_fails_verification(self):
        """
        A transaction whose `sender` field does not match the signing key
        should raise ValueError (enforced in Transaction.sign).
        """
        tx = Transaction(self.alice_pk, self.bob_pk, 10, nonce=0)

        with self.assertRaises(ValueError, msg="Signing with a mismatched key must raise ValueError"):
            tx.sign(self.bob_sk)  # Bob's key ≠ Alice's public key

    def test_forged_sender_field_fails_verification(self):
        """
        Manually setting a different public key as `sender` after signing
        must cause verify() to return False.
        """
        tx = Transaction(self.alice_pk, self.bob_pk, 10, nonce=0)
        tx.sign(self.alice_sk)

        # Swap sender to Bob's key after signing
        tx.sender = self.bob_pk

        self.assertFalse(
            tx.verify(),
            "A transaction with a forged sender field must not verify.",
        )

    def test_unsigned_transaction_fails_verification(self):
        """A transaction that was never signed must fail verification."""
        tx = Transaction(self.alice_pk, self.bob_pk, 10, nonce=0)
        # No call to tx.sign()

        self.assertFalse(
            tx.verify(),
            "An unsigned transaction must not verify.",
        )

    # ------------------------------------------------------------------
    # 4. Replay protection (nonce enforcement in State)
    # ------------------------------------------------------------------

    def test_replay_attack_same_nonce_rejected(self):
        """
        Submitting the same transaction twice (same nonce) should succeed
        the first time and fail the second time.
        """
        tx = Transaction(self.alice_pk, self.bob_pk, 10, nonce=0)
        tx.sign(self.alice_sk)

        first = self.state.apply_transaction(tx)
        self.assertTrue(first, "First submission must succeed.")

        second = self.state.apply_transaction(tx)
        self.assertFalse(second, "Replaying the same transaction must be rejected.")

    def test_out_of_order_nonce_rejected(self):
        """
        Submitting a transaction with nonce=5 when the account nonce is 0
        (i.e., skipping nonces) must be rejected.
        """
        tx = Transaction(self.alice_pk, self.bob_pk, 10, nonce=5)
        tx.sign(self.alice_sk)

        result = self.state.apply_transaction(tx)
        self.assertFalse(result, "A transaction with a skipped nonce must be rejected.")

    def test_sequential_nonces_accepted(self):
        """
        Sending two transactions with consecutive nonces (0 then 1)
        must both succeed and update the balance correctly.
        """
        tx0 = Transaction(self.alice_pk, self.bob_pk, 10, nonce=0)
        tx0.sign(self.alice_sk)
        self.assertTrue(self.state.apply_transaction(tx0))

        tx1 = Transaction(self.alice_pk, self.bob_pk, 10, nonce=1)
        tx1.sign(self.alice_sk)
        self.assertTrue(self.state.apply_transaction(tx1))

        self.assertEqual(
            self.state.get_account(self.alice_pk)["balance"],
            80,
            "Alice's balance should be 80 after two 10-coin transfers.",
        )
        self.assertEqual(
            self.state.get_account(self.bob_pk)["balance"],
            20,
            "Bob's balance should be 20 after receiving two transfers.",
        )


if __name__ == "__main__":
    unittest.main()