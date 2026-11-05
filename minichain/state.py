import copy
import json
import logging
from collections.abc import Mapping
from nacl.hash import sha256
from nacl.encoding import HexEncoder
from .contract import ContractMachine
from .mpt import Trie
from .receipt import Receipt
from .network_config import DEFAULT_MINING_REWARD

logger = logging.getLogger(__name__)

_MIN_SNAPSHOT_DELTA_ENTRIES = 1024
_MAX_SNAPSHOT_DELTA_DEPTH = 10_000


class StateSnapshot(Mapping):
    """
    An immutable view of account state, stored as a delta from a parent
    snapshot. Only accounts touched since the parent are copied, so taking a
    snapshot costs time proportional to the block's journal rather than to the
    total number of accounts.

    Snapshots form a linked history. Materializing one replays the deltas from
    its nearest materialized ancestor, or from the root when none is cached.
    """

    __slots__ = ("_parent", "_changes", "_deletions", "_depth", "_total_changes", "_state")

    def __init__(self, parent=None, changes=None, deletions=None):
        self._parent = parent
        self._changes = changes if changes is not None else {}
        self._deletions = frozenset(deletions or ())
        self._depth = 0 if parent is None else parent._depth + 1
        self._total_changes = 0
        if parent is not None:
            self._total_changes = (
                parent._total_changes + len(self._changes) + len(self._deletions)
            )
        self._state = None

    @classmethod
    def full(cls, accounts):
        """Build a self-contained snapshot from a mapping of accounts."""
        changes = {
            address: copy.deepcopy(account)
            for address, account in dict(accounts).items()
        }
        return cls(parent=None, changes=changes)

    @property
    def depth(self):
        return self._depth

    @property
    def total_changes(self):
        return self._total_changes

    def materialize(self):
        """Return the snapshot's accounts as a plain dictionary."""
        if self._state is not None:
            return self._state

        chain = []
        node = self
        while node is not None and node._state is None:
            chain.append(node)
            node = node._parent

        state = dict(node._state) if node is not None else {}
        for snapshot in reversed(chain):
            state.update(snapshot._changes)
            for address in snapshot._deletions:
                state.pop(address, None)

        self._state = state
        return state

    def to_dict(self):
        """Return an independent deep copy of the snapshot's accounts."""
        return {
            address: copy.deepcopy(account)
            for address, account in self.materialize().items()
        }

    def copy(self):
        return self.to_dict()

    def __getitem__(self, key):
        return copy.deepcopy(self.materialize()[key])

    def __iter__(self):
        return iter(self.materialize())

    def __len__(self):
        return len(self.materialize())

    def __deepcopy__(self, memo):
        return self.to_dict()

    def __repr__(self):
        return f"StateSnapshot(depth={self._depth}, changes={len(self._changes)})"


class AccountStore(dict):
    """
    The committed accounts dictionary. It records which accounts may have
    changed since the last snapshot, allowing snapshots to copy only those
    entries. Reads also mark an account because callers mutate account dicts
    in place (e.g. ``state.accounts[addr]['balance'] += 1``).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._dirty = set()
        self._deleted = set()
        self._snapshot = None

    def __getitem__(self, key):
        value = dict.__getitem__(self, key)
        self._dirty.add(key)
        return value

    def __setitem__(self, key, value):
        self._dirty.add(key)
        self._deleted.discard(key)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        self._dirty.add(key)
        self._deleted.add(key)
        dict.__delitem__(self, key)

    def __contains__(self, key):
        return dict.__contains__(self, key)

    def get(self, key, default=None):
        if dict.__contains__(self, key):
            return self[key]
        return default

    def setdefault(self, key, default=None):
        if not dict.__contains__(self, key):
            self[key] = default
        return self[key]

    def pop(self, key, *default):
        if dict.__contains__(self, key):
            value = dict.pop(self, key)
            self._dirty.add(key)
            self._deleted.add(key)
            return value
        if default:
            return default[0]
        raise KeyError(key)

    def popitem(self):
        key, value = dict.popitem(self)
        self._dirty.add(key)
        self._deleted.add(key)
        return key, value

    def clear(self):
        self._dirty.update(dict.keys(self))
        self._deleted.update(dict.keys(self))
        dict.clear(self)

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def items(self):
        self._dirty.update(dict.keys(self))
        return dict.items(self)

    def values(self):
        self._dirty.update(dict.keys(self))
        return dict.values(self)

    def copy(self):
        return dict.copy(self)

    def __deepcopy__(self, memo):
        clone = AccountStore()
        for key, value in dict.items(self):
            dict.__setitem__(clone, key, copy.deepcopy(value, memo))
        return clone


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
                if isinstance(self.backing, dict):
                    raw_value = dict.__getitem__(self.backing, key)
                else:
                    raw_value = self.backing[key]
                self.cache[key] = copy.deepcopy(raw_value)
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


class State:
    def __init__(self):
        # { address: {'balance': int, 'nonce': int, 'code': str|None, 'storage': dict} }
        self._accounts = AccountStore()
        self.contract_machine = ContractMachine(self)
        self.chain_id = "minichain-default"

    @property
    def accounts(self):
        return self._accounts

    @accounts.setter
    def accounts(self, value):
        if isinstance(value, (AccountStore, StateJournal)):
            self._accounts = value
        else:
            self._accounts = AccountStore(value)

    def state_root(self) -> str:
        """
        Dynamically builds the Merkle Patricia Trie from the current state dictionary
        and returns the cryptographic state root hash.
        """
        trie = Trie()
        accounts = self.accounts
        if isinstance(accounts, dict):
            items = dict.items(accounts)
        else:
            items = accounts.items()
        # Sort items to ensure deterministic insertion order if necessary (though MPT is order-independent)
        for addr, acc in sorted(items):
            if acc.get('balance', 0) == 0 and acc.get('nonce', 0) == 0 and not acc.get('code') and not acc.get('storage'):
                continue
            trie.put(addr, json.dumps(acc, sort_keys=True))
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
        Capture the current accounts for rollback/reorg safety.

        For a committed AccountStore this stores only the accounts touched since
        the previous snapshot. The first snapshot (or one taken over a journal)
        is self-contained, and the delta chain is periodically compacted so it
        cannot grow without bound or depend on unbounded history.
        """
        accounts = self.accounts
        if not isinstance(accounts, AccountStore):
            return StateSnapshot.full(accounts.copy())

        parent = accounts._snapshot
        dirty = accounts._dirty
        deleted = accounts._deleted

        if parent is not None and not dirty and not deleted:
            return parent

        compact = parent is not None and (
            parent._depth >= _MAX_SNAPSHOT_DELTA_DEPTH
            or parent._total_changes
            > max(_MIN_SNAPSHOT_DELTA_ENTRIES, len(accounts))
        )
        if parent is None or compact:
            snapshot = StateSnapshot.full(dict.items(accounts))
            accounts._dirty.clear()
            accounts._deleted.clear()
        else:
            changes = {}
            deletions = set()
            for address in dirty:
                if dict.__contains__(accounts, address):
                    changes[address] = copy.deepcopy(dict.__getitem__(accounts, address))
                else:
                    deletions.add(address)
            snapshot = StateSnapshot(parent, changes, deletions)
            accounts._dirty.clear()
            accounts._deleted.clear()

        accounts._snapshot = snapshot
        return snapshot

    def restore(self, snapshot_data):
        """
        Restores the state's accounts dictionary from a snapshot.
        """
        if isinstance(snapshot_data, StateSnapshot):
            self.accounts = AccountStore(snapshot_data.to_dict())
            self.accounts._snapshot = snapshot_data
        else:
            self.accounts = AccountStore(copy.deepcopy(snapshot_data))

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
