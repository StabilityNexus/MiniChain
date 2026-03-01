from .pow import calculate_hash
import logging
import threading

logger = logging.getLogger(__name__)

class Mempool:
    def __init__(self, max_size=1000):
        self._pending_txs = []
        self._seen_tx_ids = set()
        self._lock = threading.Lock()
        self.max_size = max_size

    def _get_tx_id(self, tx):
        return calculate_hash(tx.to_dict())

    def add_transaction(self, tx):
        tx_id = self._get_tx_id(tx)

        if not tx.verify():
            logger.warning("Mempool: Invalid signature rejected")
            return False

        with self._lock:
            if tx_id in self._seen_tx_ids:
                logger.warning(f"Mempool: Duplicate transaction rejected {tx_id}")
                return False

            if len(self._pending_txs) >= self.max_size:
                logger.warning(f"Mempool: Pool full, transaction rejected")
                return False

            self._pending_txs.append(tx)
            self._seen_tx_ids.add(tx_id)
            logger.info(f"Mempool: Added transaction {tx_id}")
            return True

    def return_transactions(self, transactions):
        tx_ids = {self._get_tx_id(tx) for tx in transactions}
        with self._lock:
            self._pending_txs.extend(transactions)
            self._seen_tx_ids.update(tx_ids)

    def get_transactions_for_block(self):
        with self._lock:
            txs = self._pending_txs[:]
            self._pending_txs = []
            confirmed_ids = {self._get_tx_id(tx) for tx in txs}
            self._seen_tx_ids.difference_update(confirmed_ids)
            return txs
