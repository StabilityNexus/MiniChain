from .block import Block
from .state import State
from .pow import calculate_hash
import logging
import threading
from minichain.consensus.difficulty import PIDDifficultyAdjuster
logger = logging.getLogger(__name__)


class Blockchain:
    """
    Manages the blockchain, validates blocks, and commits state transitions.
    """

   def __init__(self):
    self.chain = []
    self.state = State()
    self._lock = threading.RLock()
    self.difficulty = 2
    self.difficulty_adjuster = PIDDifficultyAdjuster(target_block_time=5)
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

            # Validate transactions on a temporary state copy
            temp_state = self.state.copy()

            for tx in block.transactions:
                result = temp_state.validate_and_apply(tx)

                # Reject block if any transaction fails
                if not result:
                    logger.warning("Block %s rejected: Transaction failed validation", block.index)
                    return False

            self.state = temp_state
self.chain.append(block)

self.difficulty = self.difficulty_adjuster.adjust(self.difficulty)
print(f"⚙ Difficulty adjusted to: {self.difficulty}")

return True
