import time
import hashlib
from typing import Optional  # <-- Removed 'List' as requested

from .transaction import Transaction
from .serialization import canonical_json_hash, canonical_json_bytes


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()

# <-- Updated 'List' to built-in 'list'
def _calculate_merkle_root(transactions: list[Transaction]) -> Optional[str]:
    if not transactions:
        return None

    # Hash each transaction deterministically
    tx_hashes = [
        tx.tx_id
        for tx in transactions
    ]

    # Build Merkle tree
    while len(tx_hashes) > 1:
        if len(tx_hashes) % 2 != 0:
            tx_hashes.append(tx_hashes[-1])  # duplicate last if odd

        new_level = []
        for i in range(0, len(tx_hashes), 2):
            combined = tx_hashes[i] + tx_hashes[i + 1]
            new_level.append(_sha256(combined))

        tx_hashes = new_level

    return tx_hashes[0]

class Block:
    def __init__(
        self,
        index: int,
        previous_hash: str,
        transactions: Optional[list[Transaction]] = None,  # <-- Updated to built-in 'list'
        timestamp: Optional[float] = None,
        difficulty: Optional[int] = None,
        miner: Optional[str] = None
    ):
        self.index = index
        self.previous_hash = previous_hash
        # Freeze transactions into an immutable tuple to prevent header/body mismatch
        self.transactions = tuple(transactions) if transactions else ()
        self.miner = miner
        # Deterministic timestamp (ms)
        self.timestamp: int = (
            round(time.time() * 1000)
            if timestamp is None
            else int(timestamp)
        )
        self.difficulty: Optional[int] = difficulty
        self.nonce: int = 0
        self.hash: Optional[str] = None

        # NEW: compute merkle root once
        self.merkle_root: Optional[str] = _calculate_merkle_root(self.transactions)

    # -------------------------
    # HEADER (used for mining)
    # -------------------------
    def to_header_dict(self):
        header = {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "merkle_root": self.merkle_root,
            "timestamp": self.timestamp,
            "difficulty": self.difficulty,
            "nonce": self.nonce,
        }
        # Include miner in header only when present (optional field)  <-- Reworded comment
        if self.miner is not None:
            header["miner"] = self.miner          
        return header
        
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
        data = self.to_header_dict()
        data.update(self.to_body_dict()) # Reuses existing serialization logic
        data["hash"] = self.hash
        return data

    # -------------------------
    # HASH CALCULATION
    # -------------------------
    def compute_hash(self) -> str:
        return canonical_json_hash(self.to_header_dict())

    @classmethod
    def from_dict(cls, payload: dict):
        transactions = [
            Transaction.from_dict(tx_payload)
            for tx_payload in payload.get("transactions", [])
        ]
        
        # Safely extract and cast difficulty if it exists
        raw_diff = payload.get("difficulty")
        parsed_diff = int(raw_diff) if raw_diff is not None else None
        
        # Safely extract and cast timestamp if it exists <-- Added explicit timestamp casting
        raw_ts = payload.get("timestamp")
        parsed_ts = int(raw_ts) if raw_ts is not None else None
        
        block = cls(
            index=int(payload["index"]),  
            previous_hash=payload["previous_hash"],
            transactions=transactions,
            timestamp=parsed_ts,          # <-- Passed the casted timestamp
            difficulty=parsed_diff,       
            miner=payload.get("miner"),
        )
        block.nonce = int(payload.get("nonce", 0))  
        block.hash = payload.get("hash")
      
        # Verify the block hash
        expected_hash = block.compute_hash()
        if block.hash is not None and block.hash != expected_hash:
            raise ValueError("block hash does not match header")

        # Recalculate and verify the Merkle root!
        if "merkle_root" in payload and payload["merkle_root"] != block.merkle_root:
            raise ValueError("merkle_root does not match transactions")
        return block

    @property
    def canonical_payload(self) -> bytes:
        """Returns the full block (header + body) as canonical bytes for networking."""
        # Sanity checks to prevent broadcasting invalid blocks
        if self.hash is None:
            raise ValueError("block hash is missing")
        if self.hash != self.compute_hash():
            raise ValueError("block hash does not match header")
        
        return canonical_json_bytes(self.to_dict())