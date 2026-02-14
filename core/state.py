from nacl.hash import sha256
from nacl.encoding import HexEncoder
from core.contract import ContractMachine

class State:
    def __init__(self):
        # Format: { address_hex: {'balance': 0, 'nonce': 0, 'code': None, 'storage': {}} }
        self.accounts = {}
        self.contract_machine = ContractMachine(self)

    def get_account(self, address):
        if address not in self.accounts:
            self.accounts[address] = {
                'balance': 0, 
                'nonce': 0, 
                'code': None, 
                'storage': {}
            }
        return self.accounts[address]

    def verify_transaction_logic(self, tx):
        sender_acc = self.get_account(tx.sender)
        if sender_acc['balance'] < tx.amount:
            print(f"Error: Insufficient balance for {tx.sender[:8]}...")
            return False
        if sender_acc['nonce'] != tx.nonce:
            print(f"Error: Invalid nonce. Expected {sender_acc['nonce']}, got {tx.nonce}")
            return False
        return True

    def apply_transaction(self, tx):
        """
        Updates state. Returns 'True' for success, or the new Contract Address if deployment.
        """
        if not self.verify_transaction_logic(tx):
            return False

        sender = self.accounts[tx.sender]
        
        # Deduct funds and increment nonce
        sender['balance'] -= tx.amount
        sender['nonce'] += 1

        # LOGIC BRANCH 1: Contract Deployment
        if tx.receiver is None or tx.receiver == "":
            contract_address = self.create_contract(tx.sender, tx.nonce, tx.data)
            return contract_address

        # LOGIC BRANCH 2: Contract Call or Regular Transfer
        receiver = self.get_account(tx.receiver)
        receiver['balance'] += tx.amount
        
        # If receiver has code, execute it
        if receiver['code']:
            success = self.contract_machine.execute(
                contract_address=tx.receiver,
                sender_address=tx.sender,
                payload=tx.data,
                amount=tx.amount
            )
            return success
            
        return True

    def create_contract(self, sender, nonce, code):
        """Generates a contract address and stores the code."""
        # Address = Hash(sender + nonce)
        raw_str = f"{sender}{nonce}".encode()
        contract_address = sha256(raw_str, encoder=HexEncoder).decode()[:40]
        
        self.accounts[contract_address] = {
            'balance': 0,
            'nonce': 0,
            'code': code,
            'storage': {}
        }
        return contract_address

    def update_contract_storage(self, address, new_storage):
        if address in self.accounts:
            self.accounts[address]['storage'] = new_storage

    def credit_mining_reward(self, miner_address, reward=50):
        account = self.get_account(miner_address)
        account['balance'] += reward