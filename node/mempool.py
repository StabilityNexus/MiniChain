from consensus.pow import calculate_hash


class Mempool:
    def __init__(self):
        self.pending_txs = []
        self.seen_tx_ids = set()  # Dedup tracking

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

        if not tx.verify():
            print("Mempool: Invalid signature rejected")
            return False

        tx_id = self._get_tx_id(tx)

        if tx_id in self.seen_tx_ids:
            print("Mempool: Duplicate transaction rejected")
            return False

        self.pending_txs.append(tx)
        self.seen_tx_ids.add(tx_id)

        return True

    def get_transactions_for_block(self):
        """
        Returns pending transactions and clears the pool.
        """

        txs = self.pending_txs[:]

        # Clear both list and dedup set to stay in sync
        self.pending_txs = []
        self.seen_tx_ids.clear()

        return txs
