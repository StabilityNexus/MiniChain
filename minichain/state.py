import copy
import json
import logging
from nacl.hash import sha256
from nacl.encoding import HexEncoder
from .contract import ContractMachine
from .mpt import Trie
from .receipt import Receipt
from .network_config import DEFAULT_MINING_REWARD, MAX_TX_COMPONENTS
from .transaction import ALLOW_MINT, ALLOW_BURN, BURN_ADDRESS, _normalize_amount

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
    def _mints(asset, signer, flags):
        """True if `signer` may create `asset` out of thin air under `flags`."""
        return bool(flags & ALLOW_MINT) and asset.startswith(f"{signer}.")

    @staticmethod
    def _add(balances, asset, delta):
        balances[asset] = balances.get(asset, 0) + delta

    def _peek(self, address):
        """Read an account without creating it."""
        return self.accounts.get(address) or {'balances': {}, 'nonce': 0}

    def verify_transaction_logic(self, tx):
        from .validators import ValidationStatus
        if not tx.verify():
            logger.error("Error: Invalid signature for tx from %s...", (tx.sender or "")[:8])
            return ValidationStatus.INVALID

        if tx.chain_id != self.chain_id:
            logger.error("Error: Invalid chain_id in tx from %s...", (tx.sender or "")[:8])
            return ValidationStatus.INVALID

        flags = tx.flags
        signers = tx.required_signers

        if not flags & ALLOW_BURN and any(d.receiver == BURN_ADDRESS for d in tx.deposits):
            logger.warning("Invalid tx %s: deposit to burn address requires ALLOW_BURN flag", tx.tx_id)
            return ValidationStatus.FAILED

        for signer in signers:
            expected = self._peek(signer)['nonce']
            if tx.nonces.get(signer) != expected:
                logger.error("Error: Invalid nonce for %s. Expected %s, got %s", signer[:8], expected, tx.nonces.get(signer))
                return ValidationStatus.FAILED

        # Per-signer solvency: withdrawals (minted assets exempt) plus gas for the fee payer.
        needed = {tx.fee_payer: {tx.fee_asset: tx.gas_limit * tx.fee_per_gas}}
        total_withdrawn = {}
        for w in tx.withdrawals:
            for asset, amt in w.amount.items():
                total_withdrawn[asset] = total_withdrawn.get(asset, 0) + amt
                if not self._mints(asset, w.sender, flags):
                    self._add(needed.setdefault(w.sender, {}), asset, amt)

        for signer, assets in needed.items():
            balances = self._peek(signer)['balances']
            for asset, amt in assets.items():
                if balances.get(asset, 0) < amt:
                    logger.warning("Invalid tx %s: insufficient balance for %s on %s", tx.tx_id, asset, signer[:8])
                    return ValidationStatus.FAILED

        # Balance pool: everything deposited or sent to contracts must come from withdrawals.
        total_distributed = {}
        for item in (*tx.deposits, *tx.calls):
            for asset, amt in item.amount.items():
                total_distributed[asset] = total_distributed.get(asset, 0) + amt

        for asset, needed_amt in total_distributed.items():
            if any(self._mints(asset, s, flags) for s in signers):
                continue
            if total_withdrawn.get(asset, 0) < needed_amt:
                logger.warning("Invalid tx %s: deficit in pool for %s (available %s < needed %s)", tx.tx_id, asset, total_withdrawn.get(asset, 0), needed_amt)
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
    def _valid_amount(amount):
        return isinstance(amount, dict) and all(
            isinstance(asset, str) and isinstance(amt, int) and not isinstance(amt, bool) and amt >= 0
            for asset, amt in amount.items()
        )

    @staticmethod
    def _amounts_well_formed(tx):
        """Semantic guard: components, amounts, flags, and gas parameters must be well-formed."""
        if not all(isinstance(x, list) for x in (tx.withdrawals, tx.deposits, tx.calls)):
            return False
        if len(tx.withdrawals) + len(tx.deposits) + len(tx.calls) > MAX_TX_COMPONENTS:
            return False
        parts = [(w.sender, w.amount) for w in tx.withdrawals]
        parts += [(d.receiver, d.amount) for d in tx.deposits]
        parts += [(c.caller, c.amount) for c in tx.calls]
        if not all(party and State._valid_amount(amount) for party, amount in parts):
            return False

        for value in (tx.flags, tx.gas_limit, tx.fee_per_gas):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return False

        return isinstance(tx.fee_asset, str)

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

        payer = tx.fee_payer
        fee_asset = tx.fee_asset
        gas_limit = tx.gas_limit
        flags = tx.flags
        signers = tx.required_signers

        self._add(self.get_account(payer)['balances'], fee_asset, -gas_limit * tx.fee_per_gas)
        for signer in signers:
            self.get_account(signer)['nonce'] += 1

        def rollback_and_refund(error_message, gas_used):
            journal.rollback()
            self.accounts = original_accounts
            self._add(self.get_account(payer)['balances'], fee_asset, -gas_used * tx.fee_per_gas)
            for signer in signers:
                self.get_account(signer)['nonce'] += 1
            return Receipt(tx.tx_id, status=0, error_message=error_message, gas_used=gas_used)

        for w in tx.withdrawals:
            balances = self.get_account(w.sender)['balances']
            for asset, amt in w.amount.items():
                if amt > 0 and not self._mints(asset, w.sender, flags):
                    self._add(balances, asset, -amt)

        for d in tx.deposits:
            balances = self.get_account(d.receiver)['balances']
            for asset, amt in d.amount.items():
                if amt > 0:
                    self._add(balances, asset, amt)

        total_gas_used = 0
        deployed_contracts = []
        for index, c in enumerate(tx.calls):
            remaining_gas = gas_limit - total_gas_used
            if not c.address:
                from .network_config import GAS_PER_BYTE
                nonce = tx.nonces[c.caller]
                contract_address = self.derive_contract_address(c.caller, nonce if index == 0 else f"{nonce}:{index}")
                code_gas = (len(c.data.encode('utf-8')) if c.data else 0) * GAS_PER_BYTE

                if code_gas > remaining_gas:
                    return rollback_and_refund("Out of gas (Code size exceeded limit)", gas_limit)

                existing = self.accounts.get(contract_address)
                if existing and existing.get("code"):
                    return rollback_and_refund("Contract collision", gas_limit)

                self.create_contract(contract_address, c.data, initial_balance=c.amount)
                total_gas_used += code_gas
                deployed_contracts.append(contract_address)
            else:
                balances = self.get_account(c.address)['balances']
                for asset, amt in c.amount.items():
                    if amt > 0:
                        self._add(balances, asset, amt)

                result = self.execute_internal_call(
                    sender=c.caller,
                    receiver_address=c.address,
                    amount=c.amount,
                    payload=c.data,
                    gas_limit=remaining_gas,
                    depth=0,
                    is_top_level=True,
                    flags=flags,
                    skip_transfer=True
                )
                total_gas_used += result.get("gas_used", remaining_gas)

                if not result.get("success"):
                    return rollback_and_refund(result.get("error", "Execution failed"), total_gas_used)

        if not tx.calls:
            total_gas_used = gas_limit
        if gas_limit > total_gas_used:
            self._add(self.get_account(payer)['balances'], fee_asset, (gas_limit - total_gas_used) * tx.fee_per_gas)

        journal.commit()
        self.accounts = original_accounts
        return Receipt(tx.tx_id, status=1, contract_address=deployed_contracts[0] if deployed_contracts else "", gas_used=total_gas_used)

    def execute_internal_call(self, sender, receiver_address, amount, payload, gas_limit, depth, is_top_level=False, flags=0, skip_transfer=False):
        receiver = self.accounts.get(receiver_address)
        if not receiver or not receiver.get("code"):
            return {"success": False, "error": "Contract not found", "gas_used": gas_limit}

        sender_acc = self.get_account(sender)

        amount = _normalize_amount(amount)
        if not isinstance(amount, dict):
            amount = {}

        can_mint = bool(flags & ALLOW_MINT)
        sender_prefix = f"{sender}."

        pre_sender_balances = dict(sender_acc['balances'])
        pre_receiver_balances = dict(receiver['balances'])

        if not skip_transfer:
            for asset, amt in amount.items():
                if not isinstance(asset, str) or isinstance(amt, bool) or not isinstance(amt, int) or amt < 0:
                    return {"success": False, "error": "Invalid amount", "gas_used": gas_limit}
                if amt > 0:
                    if can_mint and asset.startswith(sender_prefix):
                        continue
                    if sender_acc['balances'].get(asset, 0) < amt:
                        return {"success": False, "error": "Insufficient balance", "gas_used": gas_limit}

            for asset, amt in amount.items():
                if amt > 0:
                    if not (can_mint and asset.startswith(sender_prefix)):
                        sender_acc['balances'][asset] = sender_acc['balances'].get(asset, 0) - amt
                    receiver['balances'][asset] = receiver['balances'].get(asset, 0) + amt

        result = self.contract_machine.execute(
            contract_address=receiver_address,
            sender_address=sender,
            payload=payload,
            amount=amount,
            gas_limit=gas_limit,
            depth=depth
        )

        if not result.get("success"):
            if not skip_transfer:
                sender_acc['balances'] = dict(pre_sender_balances)
                receiver['balances'] = dict(pre_receiver_balances)
            return result

        transfers = result.get("transfers", [])
        total_transferred_by_asset = {}
        for t in transfers:
            t_amount = _normalize_amount(t.get("amount", {}))
            if not isinstance(t_amount, dict):
                t_amount = {}
            t["amount"] = t_amount
            for asset, amt in t_amount.items():
                if amt > 0:
                    total_transferred_by_asset[asset] = total_transferred_by_asset.get(asset, 0) + amt

        for asset, total_amt in total_transferred_by_asset.items():
            if receiver['balances'].get(asset, 0) < total_amt:
                sender_acc['balances'] = dict(pre_sender_balances)
                receiver['balances'] = dict(pre_receiver_balances)
                return {
                    "success": False,
                    "error": "Insufficient contract balance for transfers",
                    "gas_used": result.get("gas_used", gas_limit)
                }

        self.update_contract_storage(receiver_address, result["storage"])

        for asset, total_amt in total_transferred_by_asset.items():
            receiver['balances'][asset] = receiver['balances'].get(asset, 0) - total_amt

        for t in transfers:
            target_acc = self.get_account(t["to"])
            t_amount = t["amount"]
            for asset, amt in t_amount.items():
                if amt > 0:
                    target_acc['balances'][asset] = target_acc['balances'].get(asset, 0) + amt

        return result

    def derive_contract_address(self, sender, nonce):
        raw = f"{sender}:{nonce}".encode()
        return sha256(raw, encoder=HexEncoder).decode()[:40]

    def create_contract(self, contract_address, code, initial_balance=0):
        init_dict = _normalize_amount(initial_balance)
        if not isinstance(init_dict, dict):
            init_dict = {}

        balances = dict(self.accounts.get(contract_address, {}).get('balances', {}))
        for asset, amt in init_dict.items():
            if amt > 0 or asset == '':
                balances[asset] = balances.get(asset, 0) + amt

        self.accounts[contract_address] = {
            'balances': balances,
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
