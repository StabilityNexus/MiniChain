import hashlib
import json
from typing import List, Optional, Tuple
from dataclasses import dataclass


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()


@dataclass
class MerkleProof:
    tx_hash: str
    merkle_root: str
    proof: List[dict]
    verification_status: bool


class MerkleTree:
    def __init__(self, transactions: List[dict]):
        self.transactions = transactions
        self.tx_hashes = self._hash_transactions()
        self.tree = self._build_tree()
        self.root = self._get_root()

    def _hash_transactions(self) -> List[str]:
        return [
            _sha256(json.dumps(tx, sort_keys=True))
            for tx in self.transactions
        ]

    def _build_tree(self) -> List[List[str]]:
        if not self.tx_hashes:
            return []

        tree = [self.tx_hashes[:]]
        
        while len(tree[-1]) > 1:
            current_level = tree[-1]
            if len(current_level) % 2 != 0:
                current_level.append(current_level[-1])
            
            new_level = []
            for i in range(0, len(current_level), 2):
                combined = current_level[i] + current_level[i + 1]
                new_level.append(_sha256(combined))
            
            tree.append(new_level)
        
        return tree

    def _get_root(self) -> Optional[str]:
        if not self.tree:
            return None
        return self.tree[-1][0] if self.tree[-1] else None

    def get_merkle_root(self) -> Optional[str]:
        return self.root

    def get_proof(self, index: int) -> Optional[List[dict]]:
        if index < 0 or index >= len(self.tx_hashes):
            return None

        proof = []
        for level_idx in range(len(self.tree) - 1):
            level = self.tree[level_idx]
            is_right = index % 2 == 1
            sibling_idx = index - 1 if is_right else index + 1

            if sibling_idx < len(level):
                proof.append({
                    "hash": level[sibling_idx],
                    "position": "left" if is_right else "right"
                })

            index //= 2

        return proof

    @staticmethod
    def verify_proof(tx_hash: str, proof: List[dict], merkle_root: str) -> bool:
        current_hash = tx_hash
        
        for item in proof:
            sibling_hash = item["hash"]
            position = item["position"]
            
            if position == "left":
                combined = sibling_hash + current_hash
            else:
                combined = current_hash + sibling_hash
            
            current_hash = _sha256(combined)
        
        return current_hash == merkle_root


def calculate_merkle_root(transactions: List[dict]) -> Optional[str]:
    if not transactions:
        return None
    tree = MerkleTree(transactions)
    return tree.get_merkle_root()
