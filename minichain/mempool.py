import logging
import threading

logger = logging.getLogger(__name__)

class Mempool:
    def __init__(self, max_size=1000, transactions_per_block=100):
        self._list = []  # Single sorted list
        self._lock = threading.Lock()
        self.max_size = max_size
        self.transactions_per_block = transactions_per_block

    def add_transaction(self, tx):
        if not tx.verify():
            logger.warning("Mempool: Invalid signature rejected")
            return False

        with self._lock:
            existing_idx = None
            i_min = 0
            i_max = len(self._list)
            
            for i, existing_tx in enumerate(self._list):
                if existing_tx.sender == tx.sender:
                    if existing_tx.nonce == tx.nonce:
                        existing_idx = i
                    elif existing_tx.nonce < tx.nonce:
                        # Must insert AFTER the largest lower-nonce transaction
                        i_min = max(i_min, i + 1)
                    elif existing_tx.nonce > tx.nonce:
                        # Must insert BEFORE the smallest higher-nonce transaction
                        i_max = min(i_max, i)

            if existing_idx is not None:
                existing_tx = self._list[existing_idx]
                if existing_tx.tx_id == tx.tx_id:
                    logger.warning("Mempool: Duplicate transaction rejected %s", tx.tx_id)
                    return False
                if tx.timestamp <= existing_tx.timestamp:
                    logger.warning("Mempool: Ignoring older replacement %s", tx.tx_id)
                    return False
                
                self._list.pop(existing_idx)
                if i_max > existing_idx:
                    i_max -= 1
                if i_min > existing_idx:
                    i_min -= 1
            else:
                if len(self._list) >= self.max_size:
                    logger.warning("Mempool: Full, rejecting transaction")
                    return False

            i_min = min(i_min, i_max)

            # Insert before the first tx in [i_min, i_max] that has a lower max_fee_per_gas
            insert_idx = i_max
            for j in range(i_min, i_max):
                if getattr(self._list[j], 'max_fee_per_gas', 0) < getattr(tx, 'max_fee_per_gas', 0):
                    insert_idx = j
                    break
            
            self._list.insert(insert_idx, tx)
            return True

    def get_transactions_for_block(self):
        with self._lock:
            # O(k) retrieval, where k = transactions_per_block! The list is strictly ordered upon insertion.
            return list(self._list[:self.transactions_per_block])

    def remove_transactions(self, transactions):
        with self._lock:
            keys_to_remove = {(tx.sender, tx.nonce) for tx in transactions}
            self._list = [tx for tx in self._list if (tx.sender, tx.nonce) not in keys_to_remove]

    def __len__(self):
        with self._lock:
            return len(self._list)
