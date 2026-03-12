from collections import defaultdict
import logging
import threading

logger = logging.getLogger(__name__)


class Mempool:
    def __init__(self, max_size=1000):
        self._pending_by_sender = defaultdict(dict)
        self._seen_tx_ids = set()
        self._lock = threading.Lock()
        self.max_size = max_size

    def _get_tx_id(self, tx):
        return tx.tx_id

    def _count_transactions_unlocked(self):
        return sum(len(sender_queue) for sender_queue in self._pending_by_sender.values())

    def _expected_nonce_for_sender(self, sender: str, state) -> int:
        """Return the sender nonce from required chain state."""
        return state.get_account(sender)["nonce"]

    def add_transaction(self, tx):
        """
        Adds a transaction to the pool if:
        - Signature is valid
        - Transaction is not a duplicate
        - Sender nonce is not already present in the pool
        """
        tx_id = self._get_tx_id(tx)

        if not tx.verify():
            logger.warning("Mempool: Invalid signature rejected")
            return False

        with self._lock:
            if tx_id in self._seen_tx_ids:
                logger.warning("Mempool: Duplicate transaction rejected %s", tx_id)
                return False

            sender_queue = self._pending_by_sender[tx.sender]
            if tx.nonce in sender_queue:
                logger.warning(
                    "Mempool: Duplicate sender nonce rejected sender=%s nonce=%s",
                    tx.sender[:8],
                    tx.nonce,
                )
                return False

            if self._count_transactions_unlocked() >= self.max_size:
                logger.warning("Mempool: Full, rejecting transaction")
                return False

            sender_queue[tx.nonce] = tx
            self._seen_tx_ids.add(tx_id)
            return True

    def get_transactions_for_block(self, state, max_count=None):
        """
        Returns ready transactions only.

        Transactions for the same sender are included in nonce order starting
        from the sender's current account nonce. Later nonces stay queued until
        earlier ones are confirmed.
        """
        with self._lock:
            selected = []

            for sender, sender_queue in self._pending_by_sender.items():
                expected_nonce = self._expected_nonce_for_sender(sender, state)
                while expected_nonce in sender_queue:
                    selected.append(sender_queue[expected_nonce])
                    expected_nonce += 1

            selected.sort(key=lambda tx: (tx.timestamp, tx.sender, tx.nonce))
            if max_count is not None:
                if not isinstance(max_count, int) or max_count <= 0:
                    return []
                selected = selected[:max_count]
            return selected

    def remove_transactions(self, transactions):
        with self._lock:
            self._remove_transactions_unlocked(transactions)

    def _remove_transactions_unlocked(self, transactions):
        for tx in transactions:
            tx_id = self._get_tx_id(tx)
            sender_queue = self._pending_by_sender.get(tx.sender)
            if sender_queue and tx.nonce in sender_queue:
                del sender_queue[tx.nonce]
                if not sender_queue:
                    del self._pending_by_sender[tx.sender]
            self._seen_tx_ids.discard(tx_id)

    def __len__(self):
        with self._lock:
            return self._count_transactions_unlocked()
