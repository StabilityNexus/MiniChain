import copy
import json
import logging
from nacl.hash import sha256
from nacl.encoding import HexEncoder
from .contract import ContractMachine
from .mpt import Trie
from .receipt import Receipt

logger = logging.getLogger(__name__)


class State:
    def __init__(self):
        # { address: {'balance': int, 'nonce': int, 'code': str|None, 'storage': dict} }
        self.accounts = {}
        self.contract_machine = ContractMachine(self)

    def state_root(self) -> str:
        """
        Dynamically builds the Merkle Patricia Trie from the current state dictionary
        and returns the cryptographic state root hash.
        """
        trie = Trie()
        # Sort items to ensure deterministic insertion order if necessary (though MPT is order-independent)
        for addr, acc in sorted(self.accounts.items()):
            if acc.get('balance', 0) == 0 and acc.get('nonce', 0) == 0 and not acc.get('code') and not acc.get('storage'):
                continue
            trie.put(addr, json.dumps(acc, sort_keys=True))
        return trie.root_hash()

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
            logger.error("Error: Invalid signature for tx from %s...", tx.sender[:8])
            return False

        sender_acc = self.get_account(tx.sender)

        total_cost = tx.amount + getattr(tx, 'fee', 0)
        if sender_acc['balance'] < total_cost:
            logger.warning("Invalid tx %s: insufficient balance", tx.tx_id)
            return False

        if sender_acc['nonce'] != tx.nonce:
            logger.error("Error: Invalid nonce. Expected %s, got %s", sender_acc['nonce'], tx.nonce)
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
            return None
        # Further checks can be added here
        return self.apply_transaction(tx)

    def apply_transaction(self, tx):
        """
        Applies transaction and mutates state.
        Returns: Receipt object if mathematically valid, None if invalid.
        """
        if not self.verify_transaction_logic(tx):
            return None

        sender = self.accounts[tx.sender]

        total_cost = tx.amount + getattr(tx, 'fee', 0)
        
        # Deduct funds and increment nonce
        sender['balance'] -= total_cost
        sender['nonce'] += 1

        # LOGIC BRANCH 1: Contract Deployment
        if tx.receiver is None or tx.receiver == "":
            contract_address = self.derive_contract_address(tx.sender, tx.nonce)
            gas_used = getattr(tx, 'fee', 0)

            # Prevent redeploy collision
            existing = self.accounts.get(contract_address)
            if existing and existing.get("code"):
                # Restore sender balance on failure, but keep nonce incremented
                sender['balance'] += tx.amount
                return Receipt(tx.tx_id, status=0, error_message="Contract collision", gas_used=gas_used)

            self.create_contract(contract_address, tx.data, initial_balance=tx.amount)
            return Receipt(tx.tx_id, status=1, contract_address=contract_address, gas_used=gas_used)

        # LOGIC BRANCH 2: Contract Call
        # If data is provided (non-empty), treat as contract call
        if tx.data:
            receiver = self.accounts.get(tx.receiver)
            gas_limit = getattr(tx, 'fee', 0)

            # Fail if contract does not exist or has no code
            if not receiver or not receiver.get("code"):
                # Rollback sender balance on failure, but keep nonce incremented
                sender['balance'] += tx.amount # Refund amount
                return Receipt(tx.tx_id, status=0, error_message="Contract not found", gas_used=gas_limit)

            # Credit contract balance
            receiver['balance'] += tx.amount

            result = self.contract_machine.execute(
                contract_address=tx.receiver,
                sender_address=tx.sender,
                payload=tx.data,
                amount=tx.amount,
                gas_limit=gas_limit
            )

            gas_used = result.get("gas_used", gas_limit)
            gas_refund = gas_limit - gas_used
            if gas_refund > 0:
                sender['balance'] += gas_refund

            if not result.get("success"):
                # Rollback transfer if execution fails, but keep nonce incremented
                receiver['balance'] -= tx.amount
                sender['balance'] += tx.amount # Refund amount
                return Receipt(tx.tx_id, status=0, error_message=result.get("error", "Execution failed"), gas_used=gas_used)

            return Receipt(tx.tx_id, status=1, gas_used=gas_used)

        # LOGIC BRANCH 3: Regular Transfer
        receiver = self.get_account(tx.receiver)
        receiver['balance'] += tx.amount
        return Receipt(tx.tx_id, status=1, gas_used=getattr(tx, 'fee', 0))

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
