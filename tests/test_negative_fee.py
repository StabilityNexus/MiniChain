import unittest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import State, Transaction


class TestNegativeFeePrevention(unittest.TestCase):
    def setUp(self):
        self.state = State()
        self.state.chain_id = "minichain-default"

        # Setup Alice with a small balance
        self.alice_sk = SigningKey.generate()
        self.alice_pk = self.alice_sk.verify_key.encode(encoder=HexEncoder).decode()
        self.state.credit_mining_reward(self.alice_pk, 10)

        self.bob_pk = "b" * 64

    def _make_tx(self, amount, fee, nonce=0):
        tx = Transaction(
            sender=self.alice_pk,
            receiver=self.bob_pk,
            amount=amount,
            nonce=nonce,
            fee=fee,
            chain_id="minichain-default",
        )
        tx.sign(self.alice_sk)
        return tx

    # ------------------------------------------------------------------
    # Core exploit scenario
    # ------------------------------------------------------------------

    def test_negative_fee_is_rejected(self):
        """Negative fee MUST be rejected; balance must not be inflated."""
        initial_balance = self.state.get_account(self.alice_pk)["balance"]

        tx = self._make_tx(amount=0, fee=-1000)
        receipt = self.state.validate_and_apply(tx)

        self.assertIsNone(receipt, "validate_and_apply should return None for negative fee")
        self.assertEqual(
            self.state.get_account(self.alice_pk)["balance"],
            initial_balance,
            "Alice's balance must remain unchanged after a rejected negative-fee tx",
        )

    def test_negative_fee_does_not_change_nonce(self):
        """Rejected tx must not increment the sender nonce."""
        initial_nonce = self.state.get_account(self.alice_pk)["nonce"]
        tx = self._make_tx(amount=0, fee=-1)
        self.state.validate_and_apply(tx)
        self.assertEqual(
            self.state.get_account(self.alice_pk)["nonce"],
            initial_nonce,
            "Nonce must not be incremented for rejected transactions",
        )

    def test_large_negative_fee_is_rejected(self):
        """Even a very large negative fee (millions of coins) must be rejected."""
        tx = self._make_tx(amount=0, fee=-10_000_000)
        receipt = self.state.validate_and_apply(tx)
        self.assertIsNone(receipt)
        self.assertEqual(self.state.get_account(self.alice_pk)["balance"], 10)

    def test_fee_of_zero_is_accepted(self):
        """Fee of exactly 0 is valid and should be accepted."""
        tx = self._make_tx(amount=5, fee=0)
        receipt = self.state.validate_and_apply(tx)
        self.assertIsNotNone(receipt, "Fee of 0 should be accepted")
        self.assertEqual(receipt.status, 1)

    def test_positive_fee_is_accepted(self):
        """A normal positive fee should be accepted and correctly deducted."""
        tx = self._make_tx(amount=3, fee=2)
        receipt = self.state.validate_and_apply(tx)
        self.assertIsNotNone(receipt, "Positive fee should be accepted")
        self.assertEqual(receipt.status, 1)
        self.assertEqual(self.state.get_account(self.alice_pk)["balance"], 5)

    def test_float_fee_is_rejected(self):
        """Float fees (e.g. -0.5, 1.5) must be rejected as non-integer."""
        for bad_fee in [-0.5, 1.5, 0.1]:
            with self.subTest(fee=bad_fee):
                tx = Transaction(
                    sender=self.alice_pk,
                    receiver=self.bob_pk,
                    amount=0,
                    nonce=0,
                    fee=bad_fee,
                    chain_id="minichain-default",
                )
                tx.sign(self.alice_sk)
                receipt = self.state.validate_and_apply(tx)
                self.assertIsNone(receipt, f"Float fee {bad_fee} should be rejected")

    def test_negative_nonce_is_rejected(self):
        """A negative nonce must also be rejected."""
        tx = Transaction(
            sender=self.alice_pk,
            receiver=self.bob_pk,
            amount=0,
            nonce=-1,
            fee=0,
            chain_id="minichain-default",
        )
        tx.sign(self.alice_sk)
        receipt = self.state.validate_and_apply(tx)
        self.assertIsNone(receipt, "Negative nonce should be rejected")

    def test_bool_fee_is_rejected(self):
        """bool is a subclass of int in Python; True/False must not pass as fee."""
        for bad_fee in [True, False]:
            with self.subTest(fee=bad_fee):
                tx = self._make_tx(amount=0, fee=bad_fee)
                receipt = self.state.validate_and_apply(tx)
                self.assertIsNone(receipt, f"bool fee {bad_fee} should be rejected")

    def test_apply_transaction_directly_rejects_negative_fee(self):
        """Even when validate_and_apply is bypassed, apply_transaction's
        underlying verify_transaction_logic must reject negative fees."""
        initial_balance = self.state.get_account(self.alice_pk)["balance"]
        tx = self._make_tx(amount=0, fee=-1000)
        receipt = self.state.apply_transaction(tx)
        self.assertIsNone(receipt, "apply_transaction must reject negative fee directly")
        self.assertEqual(
            self.state.get_account(self.alice_pk)["balance"],
            initial_balance,
            "Balance must not inflate when apply_transaction is called directly",
        )


if __name__ == "__main__":
    unittest.main()