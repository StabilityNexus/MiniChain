import unittest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder
from minichain.state import State
from minichain.block import Transaction

class TestContractTransfers(unittest.TestCase):
    def setUp(self):
        self.state = State()
        self.sender_sk = SigningKey.generate()
        self.sender_pk = self.sender_sk.verify_key.encode(encoder=HexEncoder).decode()
        
        self.target_pk = SigningKey.generate().verify_key.encode(encoder=HexEncoder).decode()
        
        # Credit sender with enough balance to deploy and call
        self.state.credit_mining_reward(self.sender_pk, 10000)

    def _sign(self, tx):
        tx.sign(self.sender_sk)
        return tx

    def test_successful_transfer_out(self):
        # 1. Deploy Contract
        code = """
target = msg['data']['target']
transfer_out(target, 50)
transfer_out(target, 25)
"""
        deploy_tx = self._sign(Transaction(self.sender_pk, None, amount=100, nonce=0, data=code, gas_limit=1000, fee_per_gas=1))
        receipt = self.state.apply_transaction(deploy_tx)
        self.assertEqual(receipt.status, 1)
        contract_addr = receipt.contract_address

        # Sender sent 100 to contract, plus 1000 fee
        self.assertEqual(self.state.get_account(contract_addr)['balance'], 100)
        self.assertEqual(self.state.get_account(self.target_pk)['balance'], 0)

        # 2. Call Contract to transfer out 75 coins
        call_tx = self._sign(Transaction(self.sender_pk, contract_addr, amount=0, nonce=1, data={"target": self.target_pk}, gas_limit=1000, fee_per_gas=1))
        receipt2 = self.state.apply_transaction(call_tx)
        
        self.assertEqual(receipt2.status, 1)
        
        # Contract balance should be 100 - 75 = 25
        self.assertEqual(self.state.get_account(contract_addr)['balance'], 25)
        
        # Target should have 75
        self.assertEqual(self.state.get_account(self.target_pk)['balance'], 75)

    def test_failed_transfer_out_insufficient_balance(self):
        # 1. Deploy Contract
        code = """
target = msg['data']['target']
# Try to transfer 500, but contract only has 100
transfer_out(target, 500)
storage['malicious_state'] = 'corrupted'
"""
        deploy_tx = self._sign(Transaction(self.sender_pk, None, amount=100, nonce=0, data=code, gas_limit=1000, fee_per_gas=1))
        receipt = self.state.apply_transaction(deploy_tx)
        self.assertEqual(receipt.status, 1)
        contract_addr = receipt.contract_address

        # 2. Call Contract
        call_tx = self._sign(Transaction(self.sender_pk, contract_addr, amount=50, nonce=1, data={"target": self.target_pk}, gas_limit=1000, fee_per_gas=1))
        receipt2 = self.state.apply_transaction(call_tx)
        
        # Should fail with status 0
        self.assertEqual(receipt2.status, 0)
        self.assertEqual(receipt2.error_message, "Insufficient contract balance for transfers")

        # State should be completely rolled back (target balance 0, contract balance remains 100)
        self.assertEqual(self.state.get_account(contract_addr)['balance'], 100)
        self.assertEqual(self.state.get_account(self.target_pk)['balance'], 0)
        
        # Sender's balance should have decreased by only the fee amount (or gas_used if refunded) as the 50 amount was refunded
        # Starting balance 10000, minus (100+1000) for deploy = 8900
        # Call tx net cost is receipt2.gas_used
        self.assertEqual(self.state.get_account(self.sender_pk)['balance'], 8900 - receipt2.gas_used)

        # Storage should NOT be updated
        self.assertEqual(self.state.get_account(contract_addr)['storage'], {})

    def test_transfer_with_incoming_funds(self):
        # 1. Deploy Contract (0 initial balance)
        code = """
target = msg['data']['target']
# We use the incoming funds to instantly transfer out!
transfer_out(target, msg['value'])
"""
        deploy_tx = self._sign(Transaction(self.sender_pk, None, amount=0, nonce=0, data=code, gas_limit=1000, fee_per_gas=1))
        receipt = self.state.apply_transaction(deploy_tx)
        self.assertEqual(receipt.status, 1)
        contract_addr = receipt.contract_address

        self.assertEqual(self.state.get_account(contract_addr)['balance'], 0)

        # 2. Call Contract sending 50 coins
        call_tx = self._sign(Transaction(self.sender_pk, contract_addr, amount=50, nonce=1, data={"target": self.target_pk}, gas_limit=1000, fee_per_gas=1))
        receipt2 = self.state.apply_transaction(call_tx)
        
        self.assertEqual(receipt2.status, 1)
        
        # Contract balance should be 0 (received 50, sent 50)
        self.assertEqual(self.state.get_account(contract_addr)['balance'], 0)
        
        # Target should have exactly 50
        self.assertEqual(self.state.get_account(self.target_pk)['balance'], 50)

if __name__ == '__main__':
    unittest.main()
