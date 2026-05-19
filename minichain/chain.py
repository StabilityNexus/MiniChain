from .block import Block
from .state import State
from .pow import calculate_hash
import logging
import threading

MAX_BLOCKS_PER_REQUEST = 500

logger = logging.getLogger(__name__)


def validate_block_link_and_hash(previous_block, block):
    if block.previous_hash != previous_block.hash:
        raise ValueError(
            f"invalid previous hash {block.previous_hash} != {previous_block.hash}"
        )

    if block.index != previous_block.index + 1:
        raise ValueError(
            f"invalid index {block.index} != {previous_block.index + 1}"
        )

    expected_hash = calculate_hash(block.to_header_dict())
    if block.hash != expected_hash:
        raise ValueError(f"invalid hash {block.hash}")


class Blockchain:
    """
    Manages the blockchain, validates blocks, and commits state transitions.
    """

    def __init__(self):
        self.chain = []
        self.state = State()
        self._lock = threading.RLock()
        self._create_genesis_block()

    def _create_genesis_block(self):
        """
        Creates the genesis block with a fixed hash.
        """
        genesis_block = Block(
            index=0,
            previous_hash="0",
            transactions=[]
        )
        genesis_block.hash = "0" * 64
        self.chain.append(genesis_block)

    @property
    def last_block(self):
        """
        Returns the most recent block in the chain.
        """
        with self._lock: # Acquire lock for thread-safe access
            return self.chain[-1]

    @property
    def height(self) -> int:
        """Returns the current chain height (genesis = 0)"""
        with self._lock:
            return len(self.chain) - 1

    def add_block(self, block):
        """
        Validates and adds a block to the chain if all transactions succeed.
        Uses a copied State to ensure atomic validation.
        """

        with self._lock:
            try:
                validate_block_link_and_hash(self.last_block, block)
            except ValueError as exc:
                logger.warning("Block %s rejected: %s", block.index, exc)
                return False

            # Validate transactions on a temporary state copy
            temp_state = self.state.copy()

            for tx in block.transactions:
                result = temp_state.validate_and_apply(tx)

                # Reject block if any transaction fails
                if not result:
                    logger.warning("Block %s rejected: Transaction failed validation", block.index)
                    return False

            # All transactions valid → commit state and append block
            self.state = temp_state
            self.chain.append(block)
            return True

    def get_blocks_range(self, from_height: int, to_height: int) -> list:
        """Return serialized blocks in [from_height, to_height], capped at MAX_BLOCKS_PER_REQUEST."""
        with self._lock:
            to_height = min(
                to_height,
                len(self.chain) - 1,
                from_height + MAX_BLOCKS_PER_REQUEST - 1,
            )
            if from_height > to_height or from_height < 0:
                return []
            return [b.to_dict() for b in self.chain[from_height:to_height + 1]]

    def add_blocks_bulk(self, block_dicts: list) -> tuple:
        """
        Atomically add a batch of blocks: validate each block's transactions
        against a temporary state, and commit chain + state only if every
        block passes. Any failure leaves the local chain and state untouched.

        Returns (True, count) on full success, (False, 0) on any failure.
        """
        with self._lock:
            temp_state = self.state.copy()
            prev_block = self.chain[-1]
            new_blocks = []

            for block_dict in block_dicts:
                try:
                    block = Block.from_dict(block_dict)
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("Bulk add rejected: malformed block dict: %s", exc)
                    return False, 0

                try:
                    validate_block_link_and_hash(prev_block, block)
                except ValueError as exc:
                    logger.warning("Bulk add rejected at block %s: %s", block.index, exc)
                    return False, 0

                for tx in block.transactions:
                    if not temp_state.validate_and_apply(tx):
                        logger.warning(
                            "Bulk add rejected at block %s: transaction failed validation",
                            block.index,
                        )
                        return False, 0

                new_blocks.append(block)
                prev_block = block

            self.state = temp_state
            self.chain.extend(new_blocks)
            return True, len(new_blocks)

    def snapshot_state_and_height(self) -> tuple:
        """Capture accounts and chain height under a single lock acquisition."""
        with self._lock:
            accounts_copy = {
                addr: dict(acc) for addr, acc in self.state.accounts.items()
            }
            return accounts_copy, len(self.chain) - 1