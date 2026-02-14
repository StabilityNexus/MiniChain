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
        """Test deploying a contract and interacting with it."""
        
        # 1. Deploy Contract
        code = """
if msg['data'] == 'increment':
    storage['counter'] = storage.get('counter', 0) + 1
"""
        nonce = 0
        tx_deploy = Transaction(self.pk, None, 0, nonce, data=code)
        tx_deploy.sign(self.sk)
        
        # Apply deployment
        contract_addr = self.state.apply_transaction(tx_deploy)
        self.assertTrue(isinstance(contract_addr, str))
        
        # 2. Interact (Call 'increment')
        nonce += 1
        tx_call = Transaction(self.pk, contract_addr, 0, nonce, data="increment")
        tx_call.sign(self.sk)
        
        success = self.state.apply_transaction(tx_call)
        self.assertTrue(success)
        
        # 3. Check Storage
        contract_acc = self.state.get_account(contract_addr)
        self.assertEqual(contract_acc['storage']['counter'], 1)

if __name__ == '__main__':
    unittest.main()