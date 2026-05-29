import logging
import threading

logger = logging.getLogger(__name__)

class Mempool:
    def __init__(self, max_size=1000, transactions_per_block=100):
        self._pool = {}
        self._size = 0
        self._lock = threading.Lock()
        self.max_size = max_size
        self.transactions_per_block = transactions_per_block

    def add_transaction(self, tx):
        if not tx.verify():
            logger.warning("Mempool: Invalid signature rejected")
            return False

        with self._lock:
            existing = self._pool.get(tx.sender, {}).get(tx.nonce)

            if existing:
                if existing.tx_id == tx.tx_id:
                    logger.warning("Mempool: Duplicate transaction rejected %s", tx.tx_id)
                    return False
                # Fix: Guard against older replacements (e.g. rejected block restore)
                # Only allow overwrite if it's a genuinely newer replacement
                if tx.timestamp <= existing.timestamp:
                    logger.warning("Mempool: Ignoring older replacement %s", tx.tx_id)
                    return False
                
            else:
                if self._size >= self.max_size:
                    logger.warning("Mempool: Full, rejecting transaction")
                    return False
                self._size += 1
            self._pool.setdefault(tx.sender, {})[tx.nonce] = tx
            return True

    def get_transactions_for_block(self):
        with self._lock:
            snapshot = {s: list(pool.values()) for s, pool in self._pool.items()}

        for txs in snapshot.values():
            txs.sort(key=lambda t: t.nonce)

        selected = []
        while len(selected) < self.transactions_per_block:
            best_tx = None
            best_sender = None
            
            for sender, txs in snapshot.items():
                if txs:
                    if best_tx is None or (txs[0].timestamp, sender, txs[0].nonce) < (best_tx.timestamp, best_sender, best_tx.nonce):
                        best_tx = txs[0]
                        best_sender = sender
                        
            if not best_tx:
                break
                
            selected.append(best_tx)
            snapshot[best_sender].pop(0)

        return selected

    def remove_transactions(self, transactions):
        with self._lock:
            for tx in transactions:
                pool = self._pool.get(tx.sender)
                if pool and tx.nonce in pool:
                    del pool[tx.nonce]
                    self._size -= 1
                    if not pool:
                        del self._pool[tx.sender]

    def __len__(self):
        with self._lock:
            return self._size
