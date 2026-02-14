from nacl.hash import sha256
from nacl.encoding import HexEncoder
from core.contract import ContractMachine


class State:
    def __init__(self):
        # { address: {'balance': int, 'nonce': int, 'code': str|None, 'storage': dict} }
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
        """
        Applies transaction and mutates state.
        Returns:
            - Contract address (str) if deployment
            - True if successful execution
            - False if failed
        """
        if not self.verify_transaction_logic(tx):
            return False

        sender = self.accounts[tx.sender]

        # Deduct funds and increment nonce
        sender['balance'] -= tx.amount
        sender['nonce'] += 1

        # LOGIC BRANCH 1: Contract Deployment
        if tx.receiver is None or tx.receiver == "":
            contract_address = self.derive_contract_address(tx.sender, tx.nonce)

            # Prevent redeploy collision
            existing = self.accounts.get(contract_address)
            if existing and existing.get("code"):
                return False

            return self.create_contract(contract_address, tx.data)

        # LOGIC BRANCH 2: Contract Call
        # If data is provided, treat as contract call
        if tx.data is not None:
            receiver = self.accounts.get(tx.receiver)

            # Fail if contract does not exist or has no code
            if not receiver or not receiver.get("code"):
                # Rollback sender changes
                sender['balance'] += tx.amount
                sender['nonce'] -= 1
                return False

            # Credit contract balance
            receiver['balance'] += tx.amount

            success = self.contract_machine.execute(
                contract_address=tx.receiver,
                sender_address=tx.sender,
                payload=tx.data,
                amount=tx.amount
            )

            if not success:
                # Rollback transfer if execution fails
                receiver['balance'] -= tx.amount
                sender['balance'] += tx.amount
                sender['nonce'] -= 1
                return False

            return True

        # LOGIC BRANCH 3: Regular Transfer
        receiver = self.get_account(tx.receiver)
        receiver['balance'] += tx.amount
        return True

    def derive_contract_address(self, sender, nonce):
        raw = f"{sender}{nonce}".encode()
        return sha256(raw, encoder=HexEncoder).decode()[:40]

    def create_contract(self, contract_address, code):
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
