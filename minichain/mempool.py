import logging
import threading

from .node_config import MEMPOOL_MAX_SIZE, MEMPOOL_TX_PER_BLOCK, ACCEPTED_FEE_ASSETS

logger = logging.getLogger(__name__)

class Mempool:
    def __init__(self, max_size=None, transactions_per_block=None, accepted_fee_assets=None):
        self._list = []  # Sorted list of transactions (highest fee first, then oldest)
        self.max_size = max_size if max_size is not None else MEMPOOL_MAX_SIZE
        self.transactions_per_block = transactions_per_block if transactions_per_block is not None else MEMPOOL_TX_PER_BLOCK
        self.accepted_fee_assets = set(accepted_fee_assets) if accepted_fee_assets is not None else set(ACCEPTED_FEE_ASSETS)
        self._lock = threading.Lock()

    @staticmethod
    def _keys(tx):
        """The (signer, nonce) slots a transaction occupies."""
        return set(tx.nonces.items())

    def add_transaction(self, tx):
        if not tx.verify():
            logger.warning("Mempool: Invalid signature rejected")
            return False

        fee_asset = getattr(tx, "fee_asset", "") or ""
        if self.accepted_fee_assets is not None and fee_asset not in self.accepted_fee_assets:
            logger.warning("Mempool: Fee asset '%s' not accepted by miner allowlist", fee_asset)
            return False

        keys = self._keys(tx)
        with self._lock:
            conflicts = []
            i_min, i_max = 0, len(self._list)

            for i, existing in enumerate(self._list):
                existing_keys = self._keys(existing)
                if existing_keys & keys:
                    conflicts.append(i)
                for signer, nonce in existing_keys:
                    if signer not in tx.nonces:
                        continue
                    if nonce < tx.nonces[signer]:
                        # Must be placed AFTER the lower-nonce transaction
                        i_min = max(i_min, i + 1)
                    elif nonce > tx.nonces[signer]:
                        # Must be placed BEFORE the higher-nonce transaction
                        i_max = min(i_max, i)

            # RBF: the new tx must beat every transaction it displaces.
            new_fee = tx.fee_per_gas
            for i in conflicts:
                existing = self._list[i]
                if existing.tx_id == tx.tx_id:
                    logger.warning("Mempool: Duplicate transaction rejected %s", tx.tx_id)
                    return False
                if tx.timestamp <= existing.timestamp:
                    logger.warning("Mempool: Ignoring older replacement %s", tx.tx_id)
                    return False
                if new_fee <= existing.fee_per_gas * 1.1:
                    logger.warning("Mempool: Replacement fee too low for %s", tx.tx_id)
                    return False

            if len(self._list) - len(conflicts) >= self.max_size:
                logger.warning("Mempool: Full, rejecting transaction")
                return False

            # Positions are indices into the list as it is now; drop displaced txs and shift.
            displaced = set(conflicts)
            shift = lambda idx: idx - sum(1 for c in displaced if c < idx)
            i_min, i_max = shift(i_min), shift(i_max)
            if i_min > i_max:
                logger.warning("Mempool: Conflicting nonce ordering across signers for %s", tx.tx_id)
                return False
            self._list = [t for i, t in enumerate(self._list) if i not in displaced]

            # Insert before the first tx in [i_min, i_max) that pays a lower fee_per_gas
            insert_idx = i_max
            for j in range(i_min, i_max):
                if self._list[j].fee_per_gas < new_fee:
                    insert_idx = j
                    break

            self._list.insert(insert_idx, tx)
            return True

    def get_transactions_for_block(self):
        with self._lock:
            # O(k) retrieval, where k = transactions_per_block! The list is strictly ordered upon insertion.
            return list(self._list[:self.transactions_per_block])

    def remove_transactions(self, transactions):
        """Drop mined transactions and anything else that used one of their (signer, nonce) slots."""
        with self._lock:
            spent = set().union(*(self._keys(tx) for tx in transactions)) if transactions else set()
            self._list = [tx for tx in self._list if not self._keys(tx) & spent]

    def __len__(self):
        with self._lock:
            return len(self._list)
