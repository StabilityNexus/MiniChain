from .block import Block
from .state import State
from .pow import calculate_hash
import logging
import threading
from minichain.pid import PIDDifficultyAdjuster
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
        self.difficulty_adjuster = PIDDifficultyAdjuster(target_block_time=10)
        self.current_difficulty = 1000  # Initial difficulty
        
        self._create_genesis_block()
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
        genesis_block.difficulty = self.current_difficulty
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
            try:
                validate_block_link_and_hash(self.last_block, block)
            except ValueError as exc:
                logger.warning("Block %s rejected: %s", block.index, exc)
                return False
            
             # Calculate block difficulty based on mining time
            if hasattr(block, 'mining_time') and block.mining_time:
                block.difficulty = self.difficulty_adjuster.adjust(
                    self.current_difficulty,
                    block.mining_time
                )
            else:
                block.difficulty = self.current_difficulty
 
            # Verify block meets its difficulty target
            if hasattr(block, 'hash') and hasattr(block, 'difficulty'):
                difficulty = block.difficulty
                target_prefix = '0' * (difficulty // 256 + 1)  # Rough difficulty check
                
                if not block.hash.startswith(target_prefix):
                    logger.warning(
                        "Block %s rejected: PoW check failed (difficulty: %d)",
                        block.index,
                        difficulty
                    )
                    return False

            # Validate transactions on a temporary state copy
            temp_state = self.state.copy()

            for tx in block.transactions:
                result = temp_state.validate_and_apply(tx)

                # Reject block if any transaction fails
                if not result:
                    logger.warning("Block %s rejected: Transaction failed validation", block.index)
                    return False
                
                self.current_difficulty = self.difficulty_adjuster.adjust(
                self.current_difficulty,
                block.mining_time if hasattr(block, 'mining_time') else None
            )
            
            logger.info(
                "Block %s accepted. Difficulty: %d → %d",
                block.index,
                block.difficulty,
                self.current_difficulty
            )

            # All transactions valid → commit state and append block
            self.state = temp_state
            self.chain.append(block)
            return True
