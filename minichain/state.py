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
        self.chain_id = "minichain-default"

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
        from .validators import ValidationStatus
        if not tx.verify():
            logger.error("Error: Invalid signature for tx from %s...", tx.sender[:8])
            return ValidationStatus.INVALID

        if getattr(tx, "chain_id", None) != self.chain_id:
            logger.error("Error: Invalid chain_id in tx from %s...", tx.sender[:8])
            return ValidationStatus.INVALID

        sender_acc = self.get_account(tx.sender)

        total_cost = tx.amount + (getattr(tx, 'gas_limit', 0) * getattr(tx, 'max_fee_per_gas', 0))
        if sender_acc['balance'] < total_cost:
            logger.warning("Invalid tx %s: insufficient balance", tx.tx_id)
            return ValidationStatus.FAILED

        if sender_acc['nonce'] != tx.nonce:
            logger.error("Error: Invalid nonce. Expected %s, got %s", sender_acc['nonce'], tx.nonce)
            return ValidationStatus.FAILED

        return ValidationStatus.VALID

    def copy(self):
        """
        Return an independent copy of state for transactional validation.
        """
        new_state = copy.deepcopy(self)
        new_state.contract_machine = ContractMachine(new_state) # Reinitialize contract_machine
        new_state.chain_id = self.chain_id
        return new_state

    def snapshot(self):
        """
        Returns a deep copy of the current accounts dictionary for rollback safety.
        """
        return copy.deepcopy(self.accounts)

    def restore(self, snapshot_data):
        """
        Restores the state's accounts dictionary from a snapshot.
        """
        self.accounts = copy.deepcopy(snapshot_data)

    @staticmethod
    def _amounts_well_formed(tx):
        """Semantic guard: amount and fee must be non-negative integers."""
        if not isinstance(tx.amount, int) or tx.amount < 0:
            return False
        gas_limit = getattr(tx, "gas_limit", 0)
        max_fee = getattr(tx, "max_fee_per_gas", 0)
        return isinstance(gas_limit, int) and gas_limit >= 0 and isinstance(max_fee, int) and max_fee >= 0

    def validate_and_apply_with_status(self, tx):
        """
        Validate and apply a transaction, bubbling up the precise ValidationStatus.
        This is the single core path; the other entry points delegate to it.
        Returns: (ValidationStatus, Receipt|None)
        """
        from .validators import ValidationStatus
        if not self._amounts_well_formed(tx):
            return ValidationStatus.MALFORMED, None

        status = self.verify_transaction_logic(tx)
        if status != ValidationStatus.VALID:
            return status, None

        return ValidationStatus.VALID, self._apply_validated_tx(tx)

    def apply_transaction(self, tx):
        """
        Validates and applies a transaction.
        Returns: Receipt object if valid, None if invalid.
        """
        return self.validate_and_apply_with_status(tx)[1]

    # Backwards-compatible alias for the receipt-only entry point.
    validate_and_apply = apply_transaction

    def _apply_validated_tx(self, tx):
        """
        Apply a transaction that has already passed verify_transaction_logic.
        Mutates state and returns a Receipt.  Never call this directly — use
        apply_transaction() or validate_and_apply_with_status() instead.
        """
        sender = self.accounts[tx.sender]

        total_cost = tx.amount + (getattr(tx, 'gas_limit', 0) * getattr(tx, 'max_fee_per_gas', 0))
        
        # Deduct funds and increment nonce
        sender['balance'] -= total_cost
        sender['nonce'] += 1

        # LOGIC BRANCH 1: Contract Deployment
        if tx.receiver is None or tx.receiver == "":
            contract_address = self.derive_contract_address(tx.sender, tx.nonce)
            gas_used = getattr(tx, 'gas_limit', 0)

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
            gas_limit = getattr(tx, 'gas_limit', 0)

            # Fail if contract does not exist or has no code
            if not receiver or not receiver.get("code"):
                # Rollback sender balance on failure, but keep nonce incremented
                sender['balance'] += tx.amount # Refund amount
                return Receipt(tx.tx_id, status=0, error_message="Contract not found", gas_used=gas_limit)

            # Credit contract balance
            receiver['balance'] += tx.amount

            # Undo the value transfer while keeping the nonce increment (used on failure paths).
            def revert_transfer():
                receiver['balance'] -= tx.amount
                sender['balance'] += tx.amount

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
                sender['balance'] += (gas_refund * getattr(tx, 'max_fee_per_gas', 0))

            if not result.get("success"):
                revert_transfer()
                return Receipt(tx.tx_id, status=0, error_message=result.get("error", "Execution failed"), gas_used=gas_used)

            transfers = result.get("transfers", [])
            total_transferred_out = sum(t["amount"] for t in transfers)

            if total_transferred_out > receiver['balance']:
                revert_transfer()
                return Receipt(tx.tx_id, status=0, error_message="Insufficient contract balance for transfers", gas_used=gas_used)

            # Execution & transfers valid: commit state changes atomically
            self.update_contract_storage(tx.receiver, result["storage"])
            
            receiver['balance'] -= total_transferred_out
            for t in transfers:
                target_acc = self.get_account(t["to"])
                target_acc['balance'] += t["amount"]

            return Receipt(tx.tx_id, status=1, gas_used=gas_used)

        # LOGIC BRANCH 3: Regular Transfer
        receiver = self.get_account(tx.receiver)
        receiver['balance'] += tx.amount
        gas_used = getattr(tx, 'gas_limit', 0)
        return Receipt(tx.tx_id, status=1, gas_used=gas_used)

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
