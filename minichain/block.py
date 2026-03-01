import time
import json
import hashlib
from typing import List, Optional
from .transaction import Transaction
from .merkle import MerkleTree
from .utils import _sha256


class Block:
    def __init__(
        self,
        index: int,
        previous_hash: str,
        transactions: Optional[List[Transaction]] = None,
        timestamp: Optional[float] = None,
        difficulty: Optional[int] = None,
    ):
        self.index = index
        self.previous_hash = previous_hash
        self.transactions: List[Transaction] = transactions or []

        # Deterministic timestamp (ms)
        self.timestamp: int = (
            round(time.time() * 1000)
            if timestamp is None
            else int(timestamp)
        )

        self.difficulty: Optional[int] = difficulty
        self.nonce: int = 0
        self.hash: Optional[str] = None

        self._merkle_tree = MerkleTree([tx.to_dict() for tx in self.transactions])
        self.merkle_root: Optional[str] = self._merkle_tree.get_merkle_root()

    # -------------------------
    # HEADER (used for mining)
    # -------------------------
    def to_header_dict(self):
        return {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "merkle_root": self.merkle_root,
            "timestamp": self.timestamp,
            "difficulty": self.difficulty,
            "nonce": self.nonce,
        }

    # -------------------------
    # BODY (transactions only)
    # -------------------------
    def to_body_dict(self):
        return {
            "transactions": [
                tx.to_dict() for tx in self.transactions
            ]
        }

    # -------------------------
    # FULL BLOCK
    # -------------------------
    def to_dict(self):
        return {
            **self.to_header_dict(),
            **self.to_body_dict(),
            "hash": self.hash,
        }

    # -------------------------
    # HASH CALCULATION
    # -------------------------
    def compute_hash(self) -> str:
        header_string = json.dumps(
            self.to_header_dict(),
            sort_keys=True
        )
        return _sha256(header_string)

    # -------------------------
    # MERKLE PROOF
    # -------------------------
    def get_merkle_proof(self, tx_index: int) -> Optional[List[dict]]:
        return self._merkle_tree.get_proof(tx_index)

    def get_tx_hash(self, tx_index: int) -> Optional[str]:
        if tx_index < 0 or tx_index >= len(self._merkle_tree.tx_hashes):
            return None
        return self._merkle_tree.tx_hashes[tx_index]
