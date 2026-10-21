import copy
import json
import logging
from nacl.hash import sha256
from nacl.encoding import HexEncoder
from .contract import ContractMachine
from .mpt import Trie
from .receipt import Receipt
from .network_config import DEFAULT_MINING_REWARD

logger = logging.getLogger(__name__)

class StateJournal:
    """
    An in-memory proxy dictionary that caches reads and writes to avoid
    expensive deep copies of the entire state dictionary during transactions.
    """
    def __init__(self, backing_dict):
        self.backing = backing_dict
        self.cache = {}

    def __getitem__(self, key):
        if key not in self.cache:
            if key in self.backing:
                # Read without marking the backing entry changed: we only keep a copy of it.
                raw = dict.__getitem__ if isinstance(self.backing, dict) else type(self.backing).__getitem__
                self.cache[key] = copy.deepcopy(raw(self.backing, key))
            else:
                raise KeyError(key)
        return self.cache[key]

    def __setitem__(self, key, value):
        self.cache[key] = value

    def __delitem__(self, key):
        raise NotImplementedError("Account deletion not supported in StateJournal")

    def __contains__(self, key):
        return key in self.cache or key in self.backing

    def get(self, key, default=None):
        try:
            return self.__getitem__(key)
        except KeyError:
            return default

    def items(self):
        res = self.backing.copy()
        res.update(self.cache)
        return res.items()

    def update(self, other_dict):
        if hasattr(other_dict, 'items'):
            for k, v in other_dict.items():
                self[k] = v
        else:
            for k, v in other_dict:
                self[k] = v

    def copy(self):
        res = self.backing.copy()
        res.update(self.cache)
        return res

    def commit(self):
        """Flushes cached modifications to the backing dictionary."""
        self.backing.update(self.cache)
        self.cache.clear()

    def rollback(self):
        """Discards modifications."""
        self.cache.clear()


def _is_empty_account(acc):
    return acc.get('balance', 0) == 0 and acc.get('nonce', 0) == 0 and not acc.get('code') and not acc.get('storage')


def _sync_leaf(trie, address, acc):
    """Make `trie` reflect `acc` for `address` (empty accounts are not part of the state)."""
    if acc is None or _is_empty_account(acc):
        trie.delete(address)
    else:
        trie.put(address, json.dumps(acc, sort_keys=True))


class AccountStore(dict):
    """
    The live accounts dict. It remembers which accounts may have changed since its
    state trie was last brought up to date, so the state root only costs work
    proportional to the accounts a block touched, not to all accounts.

    Any access that hands out an account marks it as possibly changed, so callers
    must fetch an account from the store before mutating it (not keep a stale reference).
    """
    # The trie never forgets replaced nodes; rebuild it once they outnumber live ones.
    _MAX_NODES_PER_ACCOUNT = 6
    _NODE_SLACK = 1000

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._trie = None
        self._dirty = set()

    def __getitem__(self, key):
        value = super().__getitem__(key)
        self._dirty.add(key)
        return value

    def get(self, key, default=None):
        return self[key] if key in self else default

    def __setitem__(self, key, value):
        self._dirty.add(key)
        super().__setitem__(key, value)

    def __delitem__(self, key):
        self._dirty.add(key)
        super().__delitem__(key)

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def pop(self, key, *default):
        self._dirty.add(key)
        return super().pop(key, *default)

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def values(self):
        self._trie = None  # hands out every account at once; cannot track what the caller does
        return super().values()

    def items(self):
        self._trie = None
        return super().items()

    def __deepcopy__(self, memo):
        clone = AccountStore()
        for key, value in dict.items(self):
            dict.__setitem__(clone, key, copy.deepcopy(value, memo))
        return clone

    def synced_trie(self):
        """The state trie, brought up to date with every account changed since the last call."""
        if self._trie is None or self._trie.node_count() > self._MAX_NODES_PER_ACCOUNT * len(self) + self._NODE_SLACK:
            self._trie = Trie()
            self._dirty = set(dict.keys(self))
        for address in self._dirty:
            _sync_leaf(self._trie, address, dict.get(self, address))
        self._dirty.clear()
        return self._trie


class State:
    def __init__(self):
        # { address: {'balance': int, 'nonce': int, 'code': str|None, 'storage': dict} }
        self.accounts = AccountStore()
        self.contract_machine = ContractMachine(self)
        self.chain_id = "minichain-default"

    @property
    def accounts(self):
        return self._accounts

    @accounts.setter
    def accounts(self, value):
        self._accounts = value if isinstance(value, (AccountStore, StateJournal)) else AccountStore(value)

    def state_root(self) -> str:
        """
        Returns the Merkle Patricia Trie root of the current state. The trie is kept
        between calls and only the accounts that changed are updated; a state that is
        a journal over the live store forks that trie and applies just its own changes.
        """
        accounts = self.accounts
        if isinstance(accounts, StateJournal) and isinstance(accounts.backing, AccountStore):
            trie = accounts.backing.synced_trie().fork()
            for address, acc in accounts.cache.items():
                _sync_leaf(trie, address, acc)
            return trie.root_hash()
        if isinstance(accounts, AccountStore):
            return accounts.synced_trie().root_hash()
        return self._full_state_root()

    def _full_state_root(self) -> str:
        """Rebuild the trie from every account. Reference implementation for state_root()."""
        trie = Trie()
        for addr, acc in sorted(dict(self.accounts.items()).items()):
            _sync_leaf(trie, addr, acc)
        return trie.root_hash()

    DEFAULT_MINING_REWARD = DEFAULT_MINING_REWARD

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

        total_cost = tx.amount + (getattr(tx, 'gas_limit', 0) * getattr(tx, 'fee_per_gas', 0))
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
        Uses StateJournal for O(1) cloning instead of deepcopy.
        """
        new_state = State()
        new_state.accounts = StateJournal(self.accounts)
        new_state.contract_machine = ContractMachine(new_state)
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
        fee_per_gas = getattr(tx, "fee_per_gas", 0)
        return isinstance(gas_limit, int) and gas_limit >= 0 and isinstance(fee_per_gas, int) and fee_per_gas >= 0

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
        original_accounts = self.accounts
        journal = StateJournal(original_accounts)
        self.accounts = journal

        sender = self.accounts[tx.sender]
        total_cost = tx.amount + (getattr(tx, 'gas_limit', 0) * getattr(tx, 'fee_per_gas', 0))
        
        sender['balance'] -= total_cost
        sender['nonce'] += 1

        def rollback_and_refund(error_message, gas_used):
            journal.rollback()
            self.accounts = original_accounts
            refund_acc = self.accounts[tx.sender]
            refund_acc['balance'] -= (gas_used * getattr(tx, 'fee_per_gas', 0))
            refund_acc['nonce'] += 1
            return Receipt(tx.tx_id, status=0, error_message=error_message, gas_used=gas_used)

        # LOGIC BRANCH 1: Contract Deployment
        if tx.receiver is None or tx.receiver == "":
            contract_address = self.derive_contract_address(tx.sender, tx.nonce)
            gas_used = getattr(tx, 'gas_limit', 0)

            from .network_config import GAS_PER_BYTE
            code_bytes = len(tx.data.encode('utf-8')) if tx.data else 0
            code_gas = code_bytes * GAS_PER_BYTE
            
            if code_gas > gas_used:
                return rollback_and_refund("Out of gas (Code size exceeded limit)", gas_used)

            existing = self.accounts.get(contract_address)
            if existing and existing.get("code"):
                return rollback_and_refund("Contract collision", gas_used)

            self.create_contract(contract_address, tx.data, initial_balance=tx.amount)
            gas_refund = gas_used - code_gas
            if gas_refund > 0:
                self.accounts[tx.sender]['balance'] += (gas_refund * getattr(tx, 'fee_per_gas', 0))
            
            journal.commit()
            self.accounts = original_accounts
            return Receipt(tx.tx_id, status=1, contract_address=contract_address, gas_used=code_gas)

        # LOGIC BRANCH 2: Contract Call
        if tx.data:
            gas_limit = getattr(tx, 'gas_limit', 0)
            
            result = self.execute_internal_call(
                sender=tx.sender,
                receiver_address=tx.receiver,
                amount=tx.amount,
                payload=tx.data,
                gas_limit=gas_limit,
                depth=0,
                is_top_level=True
            )

            gas_used = result.get("gas_used", gas_limit)

            if not result.get("success"):
                return rollback_and_refund(result.get("error", "Execution failed"), gas_used)

            gas_refund = gas_limit - gas_used
            if gas_refund > 0:
                self.accounts[tx.sender]['balance'] += (gas_refund * getattr(tx, 'fee_per_gas', 0))

            journal.commit()
            self.accounts = original_accounts
            return Receipt(tx.tx_id, status=1, gas_used=gas_used)

        # LOGIC BRANCH 3: Regular Transfer
        receiver = self.get_account(tx.receiver)
        receiver['balance'] += tx.amount
        gas_used = getattr(tx, 'gas_limit', 0)
        
        journal.commit()
        self.accounts = original_accounts
        return Receipt(tx.tx_id, status=1, gas_used=gas_used)

    def execute_internal_call(self, sender, receiver_address, amount, payload, gas_limit, depth, is_top_level=False):
        receiver = self.accounts.get(receiver_address)
        if not receiver or not receiver.get("code"):
            return {"success": False, "error": "Contract not found", "gas_used": gas_limit}
            
        sender_acc = self.accounts[sender]
        
        if not is_top_level:
            if sender_acc['balance'] < amount:
                return {"success": False, "error": "Insufficient balance", "gas_used": gas_limit}
            sender_acc['balance'] -= amount
            
        receiver['balance'] += amount
        
        result = self.contract_machine.execute(
            contract_address=receiver_address,
            sender_address=sender,
            payload=payload,
            amount=amount,
            gas_limit=gas_limit,
            depth=depth
        )
        
        if not result.get("success"):
            receiver['balance'] -= amount
            if not is_top_level:
                sender_acc['balance'] += amount
            return result
            
        transfers = result.get("transfers", [])
        total_transferred_out = sum(t["amount"] for t in transfers)
        if total_transferred_out > receiver['balance']:
            receiver['balance'] -= amount
            if not is_top_level:
                sender_acc['balance'] += amount
            return {"success": False, "error": "Insufficient contract balance for transfers", "gas_used": result.get("gas_used", gas_limit)}
            
        self.update_contract_storage(receiver_address, result["storage"])
        
        receiver['balance'] -= total_transferred_out
        for t in transfers:
            target_acc = self.get_account(t["to"])
            target_acc['balance'] += t["amount"]
            
        return result

    def derive_contract_address(self, sender, nonce):
        raw = f"{sender}:{nonce}".encode()
        return sha256(raw, encoder=HexEncoder).decode()[:40]

    def create_contract(self, contract_address, code, initial_balance=0):
        existing_balance = self.accounts.get(contract_address, {}).get('balance', 0)
        self.accounts[contract_address] = {
            'balance': existing_balance + initial_balance,
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
