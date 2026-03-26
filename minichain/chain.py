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
        # Use reasonable initial difficulty (4 leading zeros)
        self.current_difficulty = 4
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
        with self._lock:  # Acquire lock for thread-safe access
            return self.chain[-1]
 
    def add_block(self, block):
        """
        Validates and adds a block to the chain if all transactions succeed.
        Uses a copied State to ensure atomic validation.
        
    
        - Validates PoW against current network difficulty
        - Calculates block time from immutable timestamps (not mining_time)
        - Uses stateless PID (no local memory variables)
        - Prevents all hard-fork scenarios
        """
 
        with self._lock:
            try:
                validate_block_link_and_hash(self.last_block, block)
            except ValueError as exc:
                logger.warning("Block %s rejected: %s", block.index, exc)
                return False
 
            #   Validate PoW against current network difficulty
            # Cap difficulty to 64 (SHA-256 hash is 64 hex chars)
            expected_difficulty = min(self.current_difficulty, 64)
            target_prefix = '0' * expected_difficulty
 
            if not block.hash or not block.hash.startswith(target_prefix):
                logger.warning(
                    "Block %s rejected: PoW check failed (required %d leading zeros)",
                    block.index,
                    expected_difficulty
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
 
            # Calculate block time from TIMESTAMPS (immutable, secure)
            previous_block = self.last_block
            actual_block_time_ms = block.timestamp - previous_block.timestamp
            actual_block_time = actual_block_time_ms / 1000.0  # Convert ms to seconds
 
            # Adjust difficulty using STATELESS PID
            # Same calculation across all nodes = deterministic consensus
            old_difficulty = self.current_difficulty
            self.current_difficulty = self.difficulty_adjuster.adjust(
                self.current_difficulty,
                actual_block_time
            )
            
            # Cap difficulty to prevent impossible mining
            self.current_difficulty = min(self.current_difficulty, 64)
 
            # All transactions valid → commit state and append block
            self.state = temp_state
            self.chain.append(block)
 
            logger.info(
                "Block %s accepted. Time: %.2fs, Difficulty: %d → %d",
                block.index,
                actual_block_time,
                old_difficulty,
                self.current_difficulty
            )
            return True
 
