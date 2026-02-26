from .pow import calculate_hash
import logging
import threading

logger = logging.getLogger(__name__)

class Mempool:
    def __init__(self, max_size=1000):
        self._pending_txs = []
        self._seen_tx_ids = set()  # Dedup tracking
        self._lock = threading.Lock()
        self.max_size = max_size

    def _get_tx_id(self, tx):
        """
        Compute a unique deterministic ID for a transaction.
        Uses full serialized tx (payload + signature).
        """
        return calculate_hash(tx.to_dict())

    def add_transaction(self, tx):
        """
        Adds a transaction to the pool if:
        - Signature is valid
        - Transaction is not a duplicate
        """

        tx_id = self._get_tx_id(tx)

        if not tx.verify():
            logger.warning("Mempool: Invalid signature rejected")
            return False

        with self._lock:
            if tx_id in self._seen_tx_ids:
                logger.warning(f"Mempool: Duplicate transaction rejected {tx_id}")
                return False

            if len(self._pending_txs) >= self.max_size:
                # Simple eviction: drop oldest or reject. Here we reject.
                logger.warning("Mempool: Full, rejecting transaction")
                return False

            self._pending_txs.append(tx)
            self._seen_tx_ids.add(tx_id)

            return True

    def get_transactions_for_block(self):
        """
        Returns pending transactions and clears the pool.
        """

        with self._lock:
            txs = self._pending_txs[:]

            # Clear both list and dedup set to stay in sync
            self._pending_txs = []
            confirmed_ids = {self._get_tx_id(tx) for tx in txs}
            self._seen_tx_ids.difference_update(confirmed_ids)

            return txs
