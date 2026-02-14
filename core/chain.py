import copy
from nacl.hash import sha256
from nacl.encoding import HexEncoder
from core.contract import ContractMachine


class State:
    def __init__(self):
        # Stores account state including balance, nonce, code, and storage
        self.accounts = {}
        self.contract_machine = ContractMachine(self)

    def copy(self):
        # Return deep copy of state for safe block validation
        new_state = State()
        new_state.accounts = copy.deepcopy(self.accounts)
        new_state.contract_machine = ContractMachine(new_state)
        return new_state

    def validate_and_apply(self, tx):
        # Entry point used by Blockchain to validate and execute tx
        return self.apply_transaction(tx)

    def get_account(self, address):
        # Lazily initialize account if not present
        if address not in self.accounts:
            self.accounts[address] = {
                'balance': 0,
                'nonce': 0,
                'code': None,
                'storage': {}
            }
        return self.accounts[address]

    def verify_transaction_logic(self, tx):
        # Validate signature, balance, and nonce
        if not tx.verify():
            print(f"Error: Invalid signature for tx from {tx.sender[:8]}...")
            return False

        sender_acc = self.get_account(tx.sender)

        if sender_acc['balance'] < tx.amount:
            print(f"Error: Insufficient balance for {tx.sender[:8]}...")
            return False

        if sender_acc['nonce'] != tx.nonce:
            print(f"Error: Invalid nonce. Expected {sender_acc['nonce']}, got {tx.nonce}")
            return False

        return True

    def apply_transaction(self, tx):
        # Apply transaction and mutate state
        if not self.verify_transaction_logic(tx):
            return False

        sender = self.accounts[tx.sender]

        # Deduct balance and increment nonce
        sender['balance'] -= tx.amount
        sender['nonce'] += 1

        # Handle contract deployment
        if tx.receiver is None or tx.receiver == "":
            contract_address = self.derive_contract_address(tx.sender, tx.nonce)
            existing = self.accounts.get(contract_address)

            if existing and existing.get("code"):
                sender['balance'] += tx.amount
                sender['nonce'] -= 1
                return False

            return self.create_contract(contract_address, tx.data)

        # Handle contract call
        if tx.data is not None:
            receiver = self.accounts.get(tx.receiver)

            if not receiver or not receiver.get("code"):
                sender['balance'] += tx.amount
                sender['nonce'] -= 1
                return False

            receiver['balance'] += tx.amount

            success = self.contract_machine.execute(
                contract_address=tx.receiver,
                sender_address=tx.sender,
                payload=tx.data,
                amount=tx.amount
            )

            if not success:
                receiver['balance'] -= tx.amount
                sender['balance'] += tx.amount
                sender['nonce'] -= 1
                return False

            return True

        # Handle regular transfer
        receiver = self.get_account(tx.receiver)
        receiver['balance'] += tx.amount
        return True

    def derive_contract_address(self, sender, nonce):
        # Deterministically derive contract address
        raw = f"{sender}{nonce}".encode()
        return sha256(raw, encoder=HexEncoder).decode()[:40]

    def create_contract(self, contract_address, code):
        # Create new contract account
        self.accounts[contract_address] = {
            'balance': 0,
            'nonce': 0,
            'code': code,
            'storage': {}
        }
        return contract_address

    def update_contract_storage(self, address, new_storage):
        # Update storage for contract
        if address in self.accounts:
            self.accounts[address]['storage'] = new_storage

    def credit_mining_reward(self, miner_address, reward=50):
        # Credit mining reward to account
        account = self.get_account(miner_address)
        account['balance'] += reward
