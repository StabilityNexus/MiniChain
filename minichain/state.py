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
                import copy
                self.cache[key] = copy.deepcopy(self.backing[key])
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
        # { address: {'balances': {asset: int}, 'nonce': int, 'code': str|None, 'storage': dict, 'registry': [ticker, ...]} }
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
            if not acc.get('balances') and acc.get('nonce', 0) == 0 and not acc.get('code') and not acc.get('storage'):
                continue
            trie.put(addr, json.dumps(acc, sort_keys=True))
        return trie.root_hash()

    DEFAULT_MINING_REWARD = DEFAULT_MINING_REWARD

    def get_account(self, address):
        if address not in self.accounts:
            self.accounts[address] = {
                'balances': {},
                'nonce': 0,
                'code': None,
                'storage': {},
                'registry': []
            }
        return self.accounts[address]

    @staticmethod
    def classify_tx(tx):
        """Infers the transaction kind from its fields (no explicit type field)."""
        if getattr(tx, "ticker", None):
            return "create_asset"
        if tx.receiver is None or tx.receiver == "":
            return "deploy"
        if tx.data:
            return "call"
        return "transfer"

    def verify_transaction_logic(self, tx):
        from .validators import ValidationStatus
        if not tx.verify():
            logger.error("Error: Invalid signature for tx from %s...", tx.sender[:8])
            return ValidationStatus.INVALID

        if getattr(tx, "chain_id", None) != self.chain_id:
            logger.error("Error: Invalid chain_id in tx from %s...", tx.sender[:8])
            return ValidationStatus.INVALID

        kind = self.classify_tx(tx)
        if kind == "create_asset" and (tx.receiver is None or tx.receiver == "" or tx.data):
            logger.error("Error: malformed create_asset tx from %s...", tx.sender[:8])
            return ValidationStatus.MALFORMED

        sender_acc = self.get_account(tx.sender)
        gas_cost = getattr(tx, 'gas_limit', 0) * getattr(tx, 'fee_per_gas', 0)
        native_cost = gas_cost if kind == "create_asset" else tx.amount + gas_cost

        if sender_acc['balances'].get('', 0) < native_cost:
            logger.warning("Invalid tx %s: insufficient balance", tx.tx_id)
            return ValidationStatus.FAILED

        if kind == "transfer" and tx.assets:
            for asset, amt in tx.assets.items():
                if sender_acc['balances'].get(asset, 0) < amt:
                    logger.warning("Invalid tx %s: insufficient balance for asset %s", tx.tx_id, asset)
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
        """Semantic guard: amount, fee, ticker and assets must be well-formed."""
        if not isinstance(tx.amount, int) or tx.amount < 0:
            return False
        gas_limit = getattr(tx, "gas_limit", 0)
        fee_per_gas = getattr(tx, "fee_per_gas", 0)
        if not (isinstance(gas_limit, int) and gas_limit >= 0 and isinstance(fee_per_gas, int) and fee_per_gas >= 0):
            return False

        ticker = getattr(tx, "ticker", None)
        if ticker is not None and (not isinstance(ticker, str) or not ticker or "." in ticker):
            return False

        assets = getattr(tx, "assets", None)
        if assets is not None:
            if not isinstance(assets, dict):
                return False
            for asset, amt in assets.items():
                if not isinstance(asset, str) or not isinstance(amt, int) or amt < 0:
                    return False

        return True

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

        kind = self.classify_tx(tx)
        sender = self.accounts[tx.sender]
        gas_cost = getattr(tx, 'gas_limit', 0) * getattr(tx, 'fee_per_gas', 0)
        native_cost = gas_cost if kind == "create_asset" else tx.amount + gas_cost

        sender['balances'][''] = sender['balances'].get('', 0) - native_cost
        sender['nonce'] += 1

        def rollback_and_refund(error_message, gas_used):
            journal.rollback()
            self.accounts = original_accounts
            refund_acc = self.accounts[tx.sender]
            refund_acc['balances'][''] = refund_acc['balances'].get('', 0) - (gas_used * getattr(tx, 'fee_per_gas', 0))
            refund_acc['nonce'] += 1
            return Receipt(tx.tx_id, status=0, error_message=error_message, gas_used=gas_used)

        # LOGIC BRANCH 0: Create Asset
        if kind == "create_asset":
            gas_used = getattr(tx, 'gas_limit', 0)
            if tx.ticker in sender['registry']:
                return rollback_and_refund("Ticker already exists", gas_used)

            full_name = f"{tx.sender}.{tx.ticker}"
            sender['registry'] = sender['registry'] + [tx.ticker]
            recipient = self.get_account(tx.receiver)
            recipient['balances'][full_name] = recipient['balances'].get(full_name, 0) + tx.amount

            journal.commit()
            self.accounts = original_accounts
            return Receipt(tx.tx_id, status=1, gas_used=gas_used)

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
                self.accounts[tx.sender]['balances'][''] += (gas_refund * getattr(tx, 'fee_per_gas', 0))

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
                self.accounts[tx.sender]['balances'][''] += (gas_refund * getattr(tx, 'fee_per_gas', 0))

            journal.commit()
            self.accounts = original_accounts
            return Receipt(tx.tx_id, status=1, gas_used=gas_used)

        # LOGIC BRANCH 3: Regular Transfer
        receiver = self.get_account(tx.receiver)
        receiver['balances'][''] = receiver['balances'].get('', 0) + tx.amount
        if tx.assets:
            for asset, amt in tx.assets.items():
                sender['balances'][asset] = sender['balances'].get(asset, 0) - amt
                receiver['balances'][asset] = receiver['balances'].get(asset, 0) + amt
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
            if sender_acc['balances'].get('', 0) < amount:
                return {"success": False, "error": "Insufficient balance", "gas_used": gas_limit}
            sender_acc['balances'][''] -= amount

        receiver['balances'][''] = receiver['balances'].get('', 0) + amount

        result = self.contract_machine.execute(
            contract_address=receiver_address,
            sender_address=sender,
            payload=payload,
            amount=amount,
            gas_limit=gas_limit,
            depth=depth
        )

        if not result.get("success"):
            receiver['balances'][''] -= amount
            if not is_top_level:
                sender_acc['balances'][''] += amount
            return result

        transfers = result.get("transfers", [])
        total_transferred_out = sum(t["amount"] for t in transfers)
        if total_transferred_out > receiver['balances']['']:
            receiver['balances'][''] -= amount
            if not is_top_level:
                sender_acc['balances'][''] += amount
            return {"success": False, "error": "Insufficient contract balance for transfers", "gas_used": result.get("gas_used", gas_limit)}

        self.update_contract_storage(receiver_address, result["storage"])

        receiver['balances'][''] -= total_transferred_out
        for t in transfers:
            target_acc = self.get_account(t["to"])
            target_acc['balances'][''] = target_acc['balances'].get('', 0) + t["amount"]

        return result

    def derive_contract_address(self, sender, nonce):
        raw = f"{sender}:{nonce}".encode()
        return sha256(raw, encoder=HexEncoder).decode()[:40]

    def create_contract(self, contract_address, code, initial_balance=0):
        existing_balance = self.accounts.get(contract_address, {}).get('balances', {}).get('', 0)
        self.accounts[contract_address] = {
            'balances': {'': existing_balance + initial_balance},
            'nonce': 0,
            'code': code,
            'storage': {},
            'registry': []
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
        account['balances'][''] = account['balances'].get('', 0) + reward
