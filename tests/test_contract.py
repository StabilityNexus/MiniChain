import unittest
import sys
import os

from minichain import State, Transaction
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder


class TestSmartContract(unittest.TestCase):

    def setUp(self):
        self.state = State()
        self.sk = SigningKey.generate()
        self.pk = self.sk.verify_key.encode(encoder=HexEncoder).decode()
        self.state.credit_mining_reward(self.pk, 100)

    def test_deploy_and_execute(self):
        """Happy path: deploy and increment counter."""

        code = """
if msg['data'] == 'increment':
    storage['counter'] = storage.get('counter', 0) + 1
"""

        tx_deploy = Transaction(self.pk, None, 0, 0, data=code)
        tx_deploy.sign(self.sk)

        receipt_deploy = self.state.apply_transaction(tx_deploy)
        self.assertIsNotNone(receipt_deploy)
        self.assertEqual(receipt_deploy.status, 1)
        contract_addr = receipt_deploy.contract_address
        self.assertTrue(isinstance(contract_addr, str))

        tx_call = Transaction(self.pk, contract_addr, 0, 1, data="increment")
        tx_call.sign(self.sk)

        receipt_call = self.state.apply_transaction(tx_call)
        self.assertIsNotNone(receipt_call)
        self.assertEqual(receipt_call.status, 1)

        contract_acc = self.state.get_account(contract_addr)
        self.assertEqual(contract_acc["storage"]["counter"], 1)

    def test_deploy_insufficient_balance(self):
        """Deploy should fail if sender balance is insufficient."""

        poor_sk = SigningKey.generate()
        poor_pk = poor_sk.verify_key.encode(encoder=HexEncoder).decode()

        code = "storage['x'] = 1"

        tx = Transaction(poor_pk, None, 1000, 0, data=code)
        tx.sign(poor_sk)

        receipt = self.state.apply_transaction(tx)
        # deploy with insufficient balance should fail mathematical validation entirely
        self.assertIsNone(receipt)

    def test_call_non_existent_contract(self):
        """Calling unknown contract should fail with valid hex receiver."""

        fake_sk = SigningKey.generate()
        fake_receiver = fake_sk.verify_key.encode(encoder=HexEncoder).decode()

        tx = Transaction(self.pk, fake_receiver, 0, 0, data="increment")
        tx.sign(self.sk)

        receipt = self.state.apply_transaction(tx)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, 0)
        self.assertEqual(receipt.error_message, "Contract not found")

    def test_contract_runtime_exception(self):
        """Contract raising exception should fail and not mutate storage."""

        code = """
raise Exception("boom")
"""

        tx_deploy = Transaction(self.pk, None, 0, 0, data=code)
        tx_deploy.sign(self.sk)

        receipt_deploy = self.state.apply_transaction(tx_deploy)
        self.assertIsNotNone(receipt_deploy)
        self.assertEqual(receipt_deploy.status, 1)
        contract_addr = receipt_deploy.contract_address
        self.assertTrue(isinstance(contract_addr, str))

        tx_call = Transaction(self.pk, contract_addr, 0, 1, data="anything")
        tx_call.sign(self.sk)

        receipt_call = self.state.apply_transaction(tx_call)
        self.assertIsNotNone(receipt_call)
        self.assertEqual(receipt_call.status, 0)
        self.assertEqual(receipt_call.error_message, "Execution failed")

        contract_acc = self.state.get_account(contract_addr)
        self.assertEqual(contract_acc["storage"], {})

    def test_redeploy_same_address(self):
        """Deploying to an already-occupied contract address should fail."""

        code = "storage['x'] = 1"

        # First deploy
        tx1 = Transaction(self.pk, None, 0, 0, data=code)
        tx1.sign(self.sk)

        receipt1 = self.state.apply_transaction(tx1)
        self.assertIsNotNone(receipt1)
        self.assertEqual(receipt1.status, 1)
        addr = receipt1.contract_address
        self.assertTrue(isinstance(addr, str))

        # Compute the address that a second deploy would use
        next_nonce = self.state.get_account(self.pk)["nonce"]
        collision_addr = self.state.derive_contract_address(self.pk, next_nonce)

        # Pre-place contract to simulate collision
        self.state.create_contract(collision_addr, "storage['y'] = 2")

        # Attempt redeploy
        tx2 = Transaction(self.pk, None, 0, next_nonce, data=code)
        tx2.sign(self.sk)

        receipt2 = self.state.apply_transaction(tx2)
        self.assertIsNotNone(receipt2)
        self.assertEqual(receipt2.status, 0)
        self.assertEqual(receipt2.error_message, "Contract collision")

    def test_balance_and_nonce_updates(self):
        """Verify sender balance and nonce after deploy and call."""

        sender_before = self.state.get_account(self.pk)
        initial_balance = sender_before["balance"]
        initial_nonce = sender_before["nonce"]

        code = "storage['x'] = 1"

        tx_deploy = Transaction(self.pk, None, 10, initial_nonce, data=code)
        tx_deploy.sign(self.sk)

        receipt = self.state.apply_transaction(tx_deploy)
        self.assertIsNotNone(receipt)
        self.assertEqual(receipt.status, 1)
        contract_addr = receipt.contract_address 
        self.assertTrue(isinstance(contract_addr, str))

        # Verify balance and nonce after deploy
        sender_after = self.state.get_account(self.pk)
        self.assertEqual(sender_after["balance"], initial_balance - 10)
        self.assertEqual(sender_after["nonce"], initial_nonce + 1)

        # Further test calls if needed
