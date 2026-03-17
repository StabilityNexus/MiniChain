import logging
import threading

logger = logging.getLogger(__name__)


class Mempool:
    TRANSACTIONS_PER_BLOCK = 100

    def __init__(self, max_size=1000, transactions_per_block=TRANSACTIONS_PER_BLOCK):
        self._pending_txs = []
        self._seen_tx_ids = set()
        self._lock = threading.Lock()
        self.max_size = max_size
        self.transactions_per_block = transactions_per_block

    def _get_tx_id(self, tx):
        return tx.tx_id

    def add_transaction(self, tx):
        """
        Adds a transaction to the pool if:
        - Signature is valid
        - Transaction is not a duplicate
        - Mempool is not full
        """
        tx_id = self._get_tx_id(tx)

        if not tx.verify():
            logger.warning("Mempool: Invalid signature rejected")
            return False

        with self._lock:
            if tx_id in self._seen_tx_ids:
                logger.warning("Mempool: Duplicate transaction rejected %s", tx_id)
                return False

            replacement_index = None
            for index, pending_tx in enumerate(self._pending_txs):
                if pending_tx.sender == tx.sender and pending_tx.nonce == tx.nonce:
                    replacement_index = index
                    break

            if replacement_index is None and len(self._pending_txs) >= self.max_size:
                logger.warning("Mempool: Full, rejecting transaction")
                return False

            if replacement_index is not None:
                old_tx = self._pending_txs[replacement_index]
                self._seen_tx_ids.discard(self._get_tx_id(old_tx))
                self._pending_txs[replacement_index] = tx
            else:
                self._pending_txs.append(tx)

            self._seen_tx_ids.add(tx_id)
            return True

    def get_transactions_for_block(self):
        """
        Returns transactions in deterministic sorted queue order.
        This is read-only; transactions are removed only after block acceptance.
        """
        with self._lock:
            selected = list(self._pending_txs)
            selected.sort(key=lambda tx: (tx.timestamp, tx.sender, tx.nonce))
            return selected[: self.transactions_per_block]

    def remove_transactions(self, transactions):
        with self._lock:
            remove_ids = {self._get_tx_id(tx) for tx in transactions}
            remove_sender_nonces = {(tx.sender, tx.nonce) for tx in transactions}
            if not remove_ids:
                return
            self._pending_txs = [
                tx
                for tx in self._pending_txs
                if self._get_tx_id(tx) not in remove_ids
                and (tx.sender, tx.nonce) not in remove_sender_nonces
            ]
            self._seen_tx_ids = {self._get_tx_id(tx) for tx in self._pending_txs}

    def __len__(self):
        with self._lock:
            return len(self._pending_txs)
