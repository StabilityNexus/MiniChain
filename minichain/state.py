from nacl.hash import sha256
from nacl.encoding import HexEncoder
from .contract import ContractMachine
import copy
import logging

logger = logging.getLogger(__name__)


class State:
    def __init__(self):
        # { address: {'balance': int, 'nonce': int, 'code': str|None, 'storage': dict} }
        self.accounts = {}
        self.contract_machine = ContractMachine(self)

    DEFAULT_MINING_REWARD = 50

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
            logger.error(f"Error: Invalid signature for tx from {tx.sender[:8]}...")
            return False

        sender_acc = self.get_account(tx.sender)

        if sender_acc['balance'] < tx.amount:
            logger.error(f"Error: Insufficient balance for {tx.sender[:8]}...")
            return False

        if sender_acc['nonce'] != tx.nonce:
            logger.error(f"Error: Invalid nonce. Expected {sender_acc['nonce']}, got {tx.nonce}")
            return False

        return True

    def copy(self):
        """
        Return an independent copy of state for transactional validation.
        """
        new_state = copy.deepcopy(self)
        new_state.contract_machine = ContractMachine(new_state) # Reinitialize contract_machine
        return new_state

    def validate_and_apply(self, tx):
        """
        Validate and apply a transaction.
        Returns the same success/failure shape as apply_transaction().
        NOTE: Delegates to apply_transaction. Callers should use this for
        semantic validation entry points.
        """
        # Semantic validation: amount must be an integer and non-negative
        if not isinstance(tx.amount, int) or tx.amount < 0:
            return False
        # Further checks can be added here
        return self.apply_transaction(tx)

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
                # Restore sender state on failure
                sender['balance'] += tx.amount
                sender['nonce'] -= 1
                return False

            return self.create_contract(contract_address, tx.data, initial_balance=tx.amount)

        # LOGIC BRANCH 2: Contract Call
        # If data is provided (non-empty), treat as contract call
        if tx.data:
            receiver = self.accounts.get(tx.receiver)

            # Fail if contract does not exist or has no code
            if not receiver or not receiver.get("code"):
                # Rollback sender balance and nonce on failure
                sender['balance'] += tx.amount # Refund amount
                sender['nonce'] -= 1
                return False

            # Credit contract balance
            receiver['balance'] += tx.amount

            success = self.contract_machine.execute(
                contract_address=tx.receiver, # Pass receiver as contract_address
                sender_address=tx.sender,
                payload=tx.data,
                amount=tx.amount
            )

            if not success:
                # Rollback transfer and nonce if execution fails
                receiver['balance'] -= tx.amount
                sender['balance'] += tx.amount # Refund amount
                sender['nonce'] -= 1
                return False

            return True

        # LOGIC BRANCH 3: Regular Transfer
        receiver = self.get_account(tx.receiver)
        receiver['balance'] += tx.amount
        return True

    def derive_contract_address(self, sender, nonce):
        raw = f"{sender}:{nonce}".encode()
        return sha256(raw, encoder=HexEncoder).decode()[:40]

    def create_contract(self, contract_address, code, initial_balance=0):
        self.accounts[contract_address] = {
            'balance': initial_balance,
            'nonce': 0,
            'code': code,
            'storage': {}
        }
        return contract_address

    def update_contract_storage(self, address, new_storage):
        if address in self.accounts:
            self.accounts[address]['storage'] = new_storage
        else:
            raise KeyError(f"Contract address not found: {address}")

    def update_contract_storage_partial(self, address, updates):
        if address not in self.accounts:
            raise KeyError(f"Contract address not found: {address}")
        if isinstance(updates, dict):
            self.accounts[address]['storage'].update(updates)
        else:
            raise ValueError("Updates must be a dictionary")

    def credit_mining_reward(self, miner_address, reward=None):
        reward = reward if reward is not None else self.DEFAULT_MINING_REWARD
        account = self.get_account(miner_address)
        account['balance'] += reward
