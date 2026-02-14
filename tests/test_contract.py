import unittest
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import State, Transaction
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

        # Deploy contract
        tx_deploy = Transaction(self.pk, None, 0, 0, data=code)
        tx_deploy.sign(self.sk)

        contract_addr = self.state.apply_transaction(tx_deploy)
        self.assertTrue(isinstance(contract_addr, str))

        # Call contract
        tx_call = Transaction(self.pk, contract_addr, 0, 1, data="increment")
        tx_call.sign(self.sk)

        success = self.state.apply_transaction(tx_call)
        self.assertTrue(success)

        contract_acc = self.state.get_account(contract_addr)
        self.assertEqual(contract_acc["storage"]["counter"], 1)

    def test_deploy_insufficient_balance(self):
        """Deploy should fail if sender balance is insufficient."""

        poor_sk = SigningKey.generate()
        poor_pk = poor_sk.verify_key.encode(encoder=HexEncoder).decode()

        code = "storage['x'] = 1"

        tx = Transaction(poor_pk, None, 1000, 0, data=code)
        tx.sign(poor_sk)

        result = self.state.apply_transaction(tx)
        self.assertFalse(result)

    def test_call_non_existent_contract(self):
        """Calling unknown contract should fail."""

        tx = Transaction(self.pk, "nonexistent_address", 0, 0, data="increment")
        tx.sign(self.sk)

        result = self.state.apply_transaction(tx)
        self.assertFalse(result)

    def test_contract_runtime_exception(self):
        """Contract raising exception should fail and not mutate storage."""

        code = """
raise Exception("boom")
"""

        # Deploy contract
        tx_deploy = Transaction(self.pk, None, 0, 0, data=code)
        tx_deploy.sign(self.sk)

        contract_addr = self.state.apply_transaction(tx_deploy)
        self.assertTrue(isinstance(contract_addr, str))

        # Call contract that raises
        tx_call = Transaction(self.pk, contract_addr, 0, 1, data="anything")
        tx_call.sign(self.sk)

        result = self.state.apply_transaction(tx_call)
        self.assertFalse(result)

        contract_acc = self.state.get_account(contract_addr)
        self.assertEqual(contract_acc["storage"], {})

    def test_redeploy_same_address(self):
        """Redeploying same contract address should fail."""

        code = "storage['x'] = 1"

        # First deploy
        tx1 = Transaction(self.pk, None, 0, 0, data=code)
        tx1.sign(self.sk)

        addr = self.state.apply_transaction(tx1)
        self.assertTrue(isinstance(addr, str))

        # Use correct next nonce to bypass nonce validation
        sender_after = self.state.get_account(self.pk)
        next_nonce = sender_after["nonce"]

        tx2 = Transaction(self.pk, None, 0, next_nonce, data=code)
        tx2.sign(self.sk)

        result = self.state.apply_transaction(tx2)
        self.assertFalse(result)

    def test_balance_and_nonce_updates(self):
        """Verify sender balance and nonce after deploy and call."""

        sender_before = self.state.get_account(self.pk)
        initial_balance = sender_before["balance"]
        initial_nonce = sender_before["nonce"]

        code = "storage['x'] = 1"

        # Deploy contract
        tx_deploy = Transaction(self.pk, None, 10, initial_nonce, data=code)
        tx_deploy.sign(self.sk)

        contract_addr = self.state.apply_transaction(tx_deploy)
        self.assertTrue(isinstance(contract_addr, str))

        sender_after_deploy = self.state.get_account(self.pk)
        self.assertEqual(sender_after_deploy["nonce"], initial_nonce + 1)
        self.assertEqual(sender_after_deploy["balance"], initial_balance - 10)

        # Call contract
        tx_call = Transaction(
            self.pk,
            contract_addr,
            5,
            sender_after_deploy["nonce"],
            data="anything"
        )
        tx_call.sign(self.sk)

        result = self.state.apply_transaction(tx_call)
        self.assertTrue(result)

        sender_after_call = self.state.get_account(self.pk)
        self.assertEqual(sender_after_call["nonce"], initial_nonce + 2)
        self.assertEqual(sender_after_call["balance"], initial_balance - 15)


if __name__ == "__main__":
    unittest.main()
