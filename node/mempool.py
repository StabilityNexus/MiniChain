class Mempool:
    def __init__(self):
        self.pending_txs = []

    def add_transaction(self, tx):
        """
        Adds a transaction to the pool if signature is valid.
        """
        if tx.verify():
            self.pending_txs.append(tx)
            return True
        else:
            print("Mempool: Invalid signature rejected")
            return False

    def get_transactions_for_block(self):
        """
        Returns pending transactions and clears the pool.
        """
        # In a real chain, we would sort by fee and limit by size
        txs = self.pending_txs[:]
        self.pending_txs = [] 
        return txs