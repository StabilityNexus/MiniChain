"""Block primitives and block-level validation logic."""

from __future__ import annotations

from dataclasses import dataclass, field

from minichain.crypto import blake2b_digest
from minichain.merkle import compute_merkle_root
from minichain.serialization import serialize_block_header
from minichain.transaction import Transaction


class BlockValidationError(ValueError):
    """Raised when a block fails structural or semantic validation."""


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

    def validate_coinbase(self, *, block_reward: int) -> None:
        """Validate coinbase placement and reward accounting."""
        if block_reward < 0:
            raise BlockValidationError("block_reward must be non-negative")
        if not self.transactions:
            raise BlockValidationError("Block must contain a coinbase transaction")
        if not self.has_valid_merkle_root():
            raise BlockValidationError("Block merkle_root does not match body")

        coinbase = self.transactions[0]
        if not coinbase.is_coinbase():
            raise BlockValidationError("First transaction must be a valid coinbase")

        for transaction in self.transactions[1:]:
            if transaction.is_coinbase():
                raise BlockValidationError("Coinbase transaction must only appear once")

        total_fees = sum(transaction.fee for transaction in self.transactions[1:])
        expected_amount = block_reward + total_fees
        if coinbase.amount != expected_amount:
            raise BlockValidationError(
                f"Invalid coinbase amount: expected {expected_amount}, got {coinbase.amount}"
            )

    def hash(self) -> bytes:
        return self.header.hash()
