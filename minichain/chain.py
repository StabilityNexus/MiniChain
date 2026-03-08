from .block import Block
from .state import State
from .pow import calculate_hash
from minichain.consensus.difficulty import PIDDifficultyAdjuster
import logging
import threading

logger = logging.getLogger(__name__)


class Blockchain:
    """
    Manages the blockchain, validates blocks, and commits state transitions.
    """

    def __init__(self):
        self.difficulty = 3
        self.difficulty_adjuster = PIDDifficultyAdjuster(target_block_time=5)
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

    def add_block(self, block):
        """
        Validates and adds a block to the chain if all transactions succeed.
        Uses a copied State to ensure atomic validation.
        """

        with self._lock:
            # Check previous hash linkage
            if block.previous_hash != self.last_block.hash:
                logger.warning("Block %s rejected: Invalid previous hash %s != %s", block.index, block.previous_hash, self.last_block.hash)
                return False

            # Check index linkage
            if block.index != self.last_block.index + 1:
                logger.warning("Block %s rejected: Invalid index %s != %s", block.index, block.index, self.last_block.index + 1)
                return False

            # Verify block hash
            if block.hash != calculate_hash(block.to_header_dict()):
                logger.warning("Block %s rejected: Invalid hash %s", block.index, block.hash)
                return False

            # Enforce PoW difficulty
            if block.difficulty != self.difficulty:
                logger.warning(
                    "Block %s rejected: Invalid difficulty %s != %s",
                    block.index, block.difficulty, self.difficulty
                )
                return False
            if not block.hash.startswith("0" * self.difficulty):
                logger.warning("Block %s rejected: Hash does not meet difficulty target", block.index)
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
            previous_timestamp = self.last_block.timestamp
            self.state = temp_state
            self.chain.append(block)
            actual_block_time = max(0, (block.timestamp - previous_timestamp) / 1000)
            self.difficulty = self.difficulty_adjuster.adjust(
                 self.difficulty,
                 actual_block_time=actual_block_time,
             )            
             logger.info("New difficulty: %s", self.difficulty)
             return True
             return True
