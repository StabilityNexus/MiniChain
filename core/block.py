import time

class Block:
    def __init__(self, index, previous_hash, transactions, timestamp=None, difficulty=None):
        self.index = index
        self.previous_hash = previous_hash
        self.transactions = transactions
        self.timestamp = time.time() if timestamp is None else timestamp
        self.nonce = 0
        self.hash = None
        self.difficulty = difficulty

    def to_dict(self):
        """Full block data for serialization/transport."""
        return {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "transactions": [tx.to_dict() for tx in self.transactions],
            "timestamp": self.timestamp,
            "difficulty": self.difficulty,
            "nonce": self.nonce,
            "hash": self.hash
        }

    def to_header_dict(self):
        """Data used for mining (consensus)."""
        return {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "transactions": [tx.to_dict() for tx in self.transactions],
            "timestamp": self.timestamp,
            "difficulty": self.difficulty,
            "nonce": self.nonce
        }
