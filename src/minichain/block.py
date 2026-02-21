"""Block primitives and block-level validation logic."""

from __future__ import annotations

from dataclasses import dataclass, field

from minichain.crypto import blake2b_digest
from minichain.merkle import compute_merkle_root
from minichain.serialization import serialize_block_header
from minichain.transaction import Transaction


@dataclass
class BlockHeader:
    """Consensus-critical block header."""

    version: int
    previous_hash: str
    merkle_root: str
    timestamp: int
    difficulty_target: int
    nonce: int
    block_height: int

    def hash(self) -> bytes:
        """Compute the canonical block-header hash."""
        return blake2b_digest(serialize_block_header(self))

    def hash_hex(self) -> str:
        return self.hash().hex()


@dataclass
class Block:
    """A block containing a header and ordered transactions."""

    header: BlockHeader
    transactions: list[Transaction] = field(default_factory=list)

    def transaction_hashes(self) -> list[bytes]:
        return [tx.transaction_id() for tx in self.transactions]

    def computed_merkle_root(self) -> bytes:
        return compute_merkle_root(self.transaction_hashes())

    def computed_merkle_root_hex(self) -> str:
        return self.computed_merkle_root().hex()

    def update_header_merkle_root(self) -> None:
        self.header.merkle_root = self.computed_merkle_root_hex()

    def has_valid_merkle_root(self) -> bool:
        return self.header.merkle_root == self.computed_merkle_root_hex()

    def hash(self) -> bytes:
        return self.header.hash()
