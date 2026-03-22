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
            
             # Verify block meets difficulty target BEFORE mutating PID state
            # Use same formula as pow.py: target = "0" * difficulty
            expected_difficulty = self.current_difficulty
             if getattr(block, "difficulty", None) != expected_difficulty:
                 logger.warning(
                     "Block %s rejected: unexpected difficulty %r != %d",
                     block.index,
                     getattr(block, "difficulty", None),
                     expected_difficulty,
                 )
                 return False
            target_prefix = '0' * expected_difficulty
            
            if not block.hash or not block.hash.startswith(target_prefix):
                logger.warning(
                    "Block %s rejected: PoW check failed (difficulty: %d)",
                    block.index,
                    expected_difficulty
                )
                return False

           # Block difficulty validation passed; block.difficulty remains as-is
           # (reflects the difficulty at which it was actually mined)

            # Validate transactions on a temporary state copy
            temp_state = self.state.copy()

            for tx in block.transactions:
                result = temp_state.validate_and_apply(tx)

                # Reject block if any transaction fails
                if not result:
                    logger.warning("Block %s rejected: Transaction failed validation", block.index)
                    return False
            for tx in block.transactions:
                result = temp_state.validate_and_apply(tx)

                # Reject block if any transaction fails
                if not result:
                    logger.warning("Block %s rejected: Transaction failed validation", block.index)
                    return False

            # All transactions valid → commit state and append block
            self.state = temp_state
            self.chain.append(block)
            
            # Adjust difficulty for next block (single adjustment per block)
            old_difficulty = self.current_difficulty
            self.current_difficulty = self.difficulty_adjuster.adjust(
                self.current_difficulty,
                block.mining_time if hasattr(block, 'mining_time') else None
            )
            
            logger.info(
                "Block %s accepted. Difficulty: %d → %d",
                block.index,
                old_difficulty,
                self.current_difficulty
            )
            return True

            # All transactions valid → commit state and append block
            self.state = temp_state
            self.chain.append(block)
            return True
