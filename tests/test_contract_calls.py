import unittest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder
from minichain.state import State
from minichain.block import Transaction

class TestCrossContractCalls(unittest.TestCase):
    def setUp(self):
        self.state = State()
        self.sender_sk = SigningKey.generate()
        self.sender_pk = self.sender_sk.verify_key.encode(encoder=HexEncoder).decode()
        
        # Credit sender with enough balance to deploy and call
        self.state.credit_mining_reward(self.sender_pk, 1000000)

    def _sign(self, tx):
        tx.sign(self.sender_sk)
        return tx

    def test_successful_cross_contract_call(self):
        # 1. Deploy target contract
        code_target = """
msg_data = msg.get('data')
if msg_data == 'increment':
    storage['count'] = storage.get('count', 0) + 1
    # Returning the current count doesn't exist natively, 
    # but we can set the success return value in theory, but python exec doesn't return values directly. 
    # We will just verify it updated storage!
"""
        deploy_tx1 = self._sign(Transaction(self.sender_pk, None, amount=0, nonce=0, data=code_target, gas_limit=50000, max_fee_per_gas=1))
        receipt1 = self.state.apply_transaction(deploy_tx1)
        self.assertEqual(receipt1.status, 1)
        target_addr = receipt1.contract_address

        # 2. Deploy caller contract
        code_caller = f"""
target = "{target_addr}"
# Call target contract
call_contract(target, 'increment', 0)
storage['called'] = True
"""
        deploy_tx2 = self._sign(Transaction(self.sender_pk, None, amount=0, nonce=1, data=code_caller, gas_limit=50000, max_fee_per_gas=1))
        receipt2 = self.state.apply_transaction(deploy_tx2)
        self.assertEqual(receipt2.status, 1)
        caller_addr = receipt2.contract_address

        # 3. Call caller contract
        call_tx = self._sign(Transaction(self.sender_pk, caller_addr, amount=0, nonce=2, data="go", gas_limit=50000, max_fee_per_gas=1))
        receipt3 = self.state.apply_transaction(call_tx)
        
        self.assertEqual(receipt3.status, 1)
        
        # Verify Caller Storage
        self.assertEqual(self.state.get_account(caller_addr)['storage']['called'], True)
        
        # Verify Target Storage
        self.assertEqual(self.state.get_account(target_addr)['storage']['count'], 1)

    def test_cross_contract_call_failure_reverts(self):
        # 1. Deploy target contract that fails
        code_target = """
storage['count'] = storage.get('count', 0) + 1
raise Exception("Deliberate failure")
"""
        deploy_tx1 = self._sign(Transaction(self.sender_pk, None, amount=0, nonce=0, data=code_target, gas_limit=50000, max_fee_per_gas=1))
        receipt1 = self.state.apply_transaction(deploy_tx1)
        self.assertEqual(receipt1.status, 1)
        target_addr = receipt1.contract_address

        # 2. Deploy caller contract
        code_caller = f"""
target = "{target_addr}"
storage['called'] = True
# This should fail and revert everything
call_contract(target, 'increment', 0)
"""
        deploy_tx2 = self._sign(Transaction(self.sender_pk, None, amount=0, nonce=1, data=code_caller, gas_limit=50000, max_fee_per_gas=1))
        receipt2 = self.state.apply_transaction(deploy_tx2)
        self.assertEqual(receipt2.status, 1)
        caller_addr = receipt2.contract_address

        # 3. Call caller contract
        call_tx = self._sign(Transaction(self.sender_pk, caller_addr, amount=0, nonce=2, data="go", gas_limit=50000, max_fee_per_gas=1))
        receipt3 = self.state.apply_transaction(call_tx)
        
        # Must fail
        self.assertEqual(receipt3.status, 0)
        self.assertIn("Cross-contract call failed", receipt3.error_message)
        
        # Verify Caller Storage reverted (or wasn't applied)
        self.assertEqual(self.state.get_account(caller_addr)['storage'], {})
        
        # Verify Target Storage reverted (or wasn't applied)
        self.assertEqual(self.state.get_account(target_addr)['storage'], {})

if __name__ == '__main__':
    unittest.main()
