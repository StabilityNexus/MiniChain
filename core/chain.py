from core.block import Block
from core.state import State
from core.transaction import Transaction
from consensus import calculate_hash
import logging
import threading
import json
import os
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_CHAIN_FILE = "chain_data.json"


class Blockchain:
    """
    Manages the blockchain, validates blocks, and commits state transitions.
    """

    def __init__(self, chain_file: Optional[str] = None):
        self.chain = []
        self.state = State()
        self._lock = threading.RLock()
        self._chain_file = chain_file or DEFAULT_CHAIN_FILE
        self._load_from_file()

    def _load_from_file(self):
        if not os.path.exists(self._chain_file):
            self._create_genesis_block()
            return

        temp_file = None
        try:
            with open(self._chain_file, 'r') as f:
                data = json.load(f)

            self.chain = []
            for block_data in data.get("chain", []):
                transactions = []
                for tx in block_data.get("transactions", []):
                    t = Transaction(
                        sender=tx["sender"],
                        receiver=tx.get("receiver"),
                        amount=tx["amount"],
                        nonce=tx["nonce"],
                        data=tx.get("data"),
                        signature=tx.get("signature")
                    )
                    t.timestamp = tx.get("timestamp", t.timestamp)
                    transactions.append(t)
                
                block = Block(
                    index=block_data["index"],
                    previous_hash=block_data["previous_hash"],
                    transactions=transactions,
                    timestamp=block_data.get("timestamp"),
                    difficulty=block_data.get("difficulty")
                )
                block.nonce = block_data.get("nonce", 0)
                block.hash = block_data.get("hash")
                self.chain.append(block)

            for i in range(1, len(self.chain)):
                prev_block = self.chain[i - 1]
                curr_block = self.chain[i]
                
                if curr_block.previous_hash != prev_block.hash:
                    logger.warning(f"Loaded chain has invalid previous_hash at block {i}. Rejecting loaded chain.")
                    self.chain = []
                    break
                
                if curr_block.hash != calculate_hash(curr_block.to_header_dict()):
                    logger.warning(f"Loaded chain has invalid hash at block {i}. Rejecting loaded chain.")
                    self.chain = []
                    break
                
                expected_merkle = curr_block.merkle_root
                computed_merkle = Block(
                    index=curr_block.index,
                    previous_hash=curr_block.previous_hash,
                    transactions=curr_block.transactions,
                    timestamp=curr_block.timestamp,
                    difficulty=curr_block.difficulty
                ).merkle_root
                
                if expected_merkle != computed_merkle:
                    logger.warning(f"Loaded chain has invalid merkle_root at block {i}. Rejecting loaded chain.")
                    self.chain = []
                    break
            else:
                if len(self.chain) == 0:
                    self._create_genesis_block()
                elif data.get("state"):
                    self.state = State.from_dict(data["state"])
                    logger.info(f"Loaded chain with {len(self.chain)} blocks from {self._chain_file}")
                    return

            if not self.chain:
                self._create_genesis_block()

        except Exception as e:
            logger.warning(f"Failed to load chain from {self._chain_file}: {e}. Creating new genesis block.")
            self._create_genesis_block()

    def save_to_file(self):
        temp_file = None
        try:
            data = {
                "chain": [
                    {
                        "index": block.index,
                        "previous_hash": block.previous_hash,
                        "merkle_root": block.merkle_root,
                        "timestamp": block.timestamp,
                        "difficulty": block.difficulty,
                        "nonce": block.nonce,
                        "hash": block.hash,
                        "transactions": [tx.to_dict() for tx in block.transactions]
                    }
                    for block in self.chain
                ],
                "state": self.state.to_dict() if hasattr(self.state, 'to_dict') else {}
            }

            temp_file = self._chain_file + ".tmp"
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)

            os.replace(temp_file, self._chain_file)
            logger.info(f"Saved chain with {len(self.chain)} blocks to {self._chain_file}")

        except Exception as e:
            logger.error(f"Failed to save chain to {self._chain_file}: {e}")
            if temp_file is not None and os.path.exists(temp_file):
                os.remove(temp_file)

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

            # All transactions valid → commit state and append block
            self.state = temp_state
            self.chain.append(block)
            self.save_to_file()
            return True
