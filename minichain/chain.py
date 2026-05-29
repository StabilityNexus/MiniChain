from .block import Block
from .state import State
from .pow import calculate_hash
import logging
import threading
import json
import os
import sys

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

    def __init__(self, genesis_path="genesis.json"):
        self.chain = []
        self.state = State()
        self._lock = threading.RLock()
        self._create_genesis_block(genesis_path)

    def _create_genesis_block(self, genesis_path):
        """
        Creates the genesis block and initializes state from config.
        """
        config = {}
        if os.path.exists(genesis_path):
            try:
                with open(genesis_path, "r") as f:
                    config = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load genesis config: {e}")
                sys.exit(1)
        else:
            logger.error(f"Failed to load genesis config: file {genesis_path} does not exist.")
            sys.exit(1)
        
        # Apply genesis allocations
        alloc = config.get("alloc", {})
        for address, data in alloc.items():
            balance = data.get("balance", 0)
            if not isinstance(balance, int) or balance < 0:
                logger.error(f"Invalid genesis balance for {address}: {balance}. Must be a non-negative integer.")
                sys.exit(1)
            account = self.state.get_account(address)
            account['balance'] = balance

        timestamp = config.get("timestamp")
        difficulty = config.get("difficulty")
        
        genesis_block = Block(
            index=0,
            previous_hash="0",
            transactions=[],
            timestamp=timestamp,
            difficulty=difficulty
        )
        
        computed_hash = calculate_hash(genesis_block.to_header_dict())
        config_hash = config.get("hash")
        
        if config_hash:
            if config_hash != computed_hash:
                logger.error(f"Genesis hash mismatch. Config hash: {config_hash}, Computed hash: {computed_hash}")
                sys.exit(1)
            genesis_block.hash = config_hash
        else:
            genesis_block.hash = computed_hash
            
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
