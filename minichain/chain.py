import logging
import threading
import time

from .block import Block
from .pow import calculate_hash
from .state import State

logger = logging.getLogger(__name__)


def validate_block_link_and_hash(previous_block, block):
    if block.previous_hash != previous_block.hash:
        raise ValueError(
            f"invalid previous hash {block.previous_hash} != {previous_block.hash}"
        )

    if block.index != previous_block.index + 1:
        raise ValueError(f"invalid index {block.index} != {previous_block.index + 1}")

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
        genesis_block = Block(index=0, previous_hash="0", transactions=[])
        genesis_block.hash = "0" * 64
        self.chain.append(genesis_block)

    @property
    def last_block(self):
        """
        Returns the most recent block in the chain.
        """
        with self._lock:
            return self.chain[-1]

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

            previous_block = self.last_block

            # Timestamp Validation

            if block.timestamp <= previous_block.timestamp:
                logger.warning(
                    "Block %s rejected: timestamp older than previous block",
                    block.index,
                )
                return False

            current_time = int(time.time() * 1000)

            if block.timestamp > current_time + 60000:
                logger.warning(
                    "Block %s rejected: timestamp too far in future",
                    block.index,
                )
                return False

            # Transaction Validation

            temp_state = self.state.copy()

            for tx in block.transactions:
                result = temp_state.validate_and_apply(tx)

                if not result:
                    logger.warning(
                        "Block %s rejected: Transaction failed validation",
                        block.index,
                    )
                    return False

            # Commit state
            self.state = temp_state
            self.chain.append(block)

            return True
