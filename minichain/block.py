import time
import hashlib
from typing import Sequence, Optional

from .transaction import Transaction
from .receipt import Receipt
from .serialization import canonical_json_hash, canonical_json_bytes

def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode()).hexdigest()

def _calculate_merkle_tree(hashes: Sequence[str]) -> Optional[str]:
    if not hashes:
        return None
    hashes_list = list(hashes)
    while len(hashes_list) > 1:
        if len(hashes_list) % 2 != 0:
            hashes_list.append(hashes_list[-1])
        new_level = []
        for i in range(0, len(hashes_list), 2):
            combined = hashes_list[i] + hashes_list[i + 1]
            new_level.append(_sha256(combined))
        hashes_list = new_level
    return hashes_list[0]

def _calculate_merkle_root(transactions: Sequence[Transaction]) -> Optional[str]:
    if not transactions:
        return None
    return _calculate_merkle_tree([tx.tx_id for tx in transactions])

def calculate_receipt_root(receipts: Sequence[Receipt]) -> Optional[str]:
    if not receipts:
        return None
    return _calculate_merkle_tree([canonical_json_hash(r.to_dict()) for r in receipts])

class Block:
    def __init__(
        self,
        index: int,
        previous_hash: str,
        target: int,
        transactions: Sequence[Transaction] = (),
        timestamp: int = 0,
        state_root: Optional[str] = None,
        receipt_root: Optional[str] = None,
        receipts: Sequence[Receipt] = (),
        miner: Optional[str] = None,
    ):
        self.index = index
        self.previous_hash = previous_hash
        # Freeze transactions into an immutable tuple to prevent header/body mismatch
        self.transactions = tuple(transactions)
        self.receipts = tuple(receipts)
        self.miner = miner
        # Deterministic timestamp (ms)
        self.timestamp: int = (
            round(time.time() * 1000)
            if timestamp == 0
            else int(timestamp)
        )
        self.target: int = target
        self.nonce: int = 0
        self.hash: Optional[str] = None
        self.state_root: Optional[str] = state_root
        self.receipt_root: Optional[str] = receipt_root
        # Difficulty EMA after this block. Local bookkeeping set when the block is
        # accepted: not part of the header, the hash, or the wire format.
        self.avg_block_time: Optional[float] = None

        # Compute merkle roots once
        self.merkle_root: Optional[str] = _calculate_merkle_root(self.transactions)
        
        # If receipt_root is missing but we have receipts, calculate it.
        if self.receipt_root is None and self.receipts:
            self.receipt_root = calculate_receipt_root(self.receipts)

    # -------------------------
    # HEADER (used for mining)
    # -------------------------
    def to_header_dict(self):
        header = {
            "index": self.index,
            "previous_hash": self.previous_hash,
            "merkle_root": self.merkle_root,
            "state_root": self.state_root,
            "receipt_root": self.receipt_root,
            "timestamp": self.timestamp,
            "target": hex(self.target),
            "nonce": self.nonce,
        }
        # Include miner in header only when present (optional field)
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
            ],
            "receipts": [
                r.to_dict() for r in self.receipts
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
        receipts = [
            Receipt.from_dict(r_payload)
            for r_payload in payload.get("receipts", [])
        ]
        
        # Safely extract and cast target and timestamp if they exist
        raw_target = payload.get("target")
        if raw_target is not None:
            from .network_config import MAX_TARGET
            parsed_target = int(raw_target, 16) if isinstance(raw_target, str) else int(raw_target)
            if not isinstance(parsed_target, int) or parsed_target <= 0 or parsed_target > MAX_TARGET:
                raise ValueError(f"invalid target in payload: {parsed_target}")
        else:
            raise ValueError("missing target in payload")

        raw_ts = payload.get("timestamp")
        parsed_ts = int(raw_ts) if raw_ts is not None else 0
        block = cls(
            index=int(payload["index"]),
            previous_hash=payload["previous_hash"],
            transactions=transactions,
            timestamp=parsed_ts,
            target=parsed_target,
            state_root=payload.get("state_root"),
            receipt_root=payload.get("receipt_root"),
            receipts=receipts,
            miner=payload.get("miner"),
        )
        block.nonce = int(payload.get("nonce") or 0)
        block.hash = payload.get("hash")

        # Verify the block hash
        expected_hash = block.compute_hash()
        if block.hash and block.hash != expected_hash:
            raise ValueError("block hash does not match header")

        # Recalculate and verify the Merkle root!
        if "merkle_root" in payload and payload["merkle_root"] != block.merkle_root:
            raise ValueError("merkle_root does not match transactions")
    
        if "receipt_root" in payload:
            expected_receipt_root = calculate_receipt_root(block.receipts)
            if payload["receipt_root"] != expected_receipt_root:
                raise ValueError("receipt_root does not match receipts")
                
        return block

    @property
    def canonical_payload(self) -> bytes:
        """Returns the full block (header + body) as canonical bytes for networking."""
        # Sanity checks to prevent broadcasting invalid blocks
        if not self.hash:
            raise ValueError("block hash is missing")
        if self.hash != self.compute_hash():
            raise ValueError("block hash does not match header")
        
        return canonical_json_bytes(self.to_dict())