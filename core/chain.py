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

    def get_chain_copy(self):
        with self._lock:
            return list(self.chain)

    def _load_from_file(self):
        if not os.path.exists(self._chain_file):
            self._create_genesis_block()
            return

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

if len(self.chain) == 0:
                self._create_genesis_block()
                logger.info("Chain file %s contained no blocks; created genesis block", self._chain_file)
                return

            genesis = self.chain[0]
            if genesis.hash != "0" * 64 or genesis.previous_hash != "0":
logger.warning("Loaded chain has invalid genesis block. Rejecting loaded chain.")
                self._create_genesis_block()
                logger.info("Created new genesis block after rejecting invalid chain")
                return

            for i in range(1, len(self.chain)):
                prev_block = self.chain[i - 1]
                curr_block = self.chain[i]
                curr_data = data["chain"][i]
                
                if curr_block.previous_hash != prev_block.hash:
                    logger.warning(f"Loaded chain has invalid previous_hash at block {i}. Rejecting loaded chain.")
                    self.chain = []
                    break
                
                if curr_block.hash != calculate_hash(curr_block.to_header_dict()):
                    logger.warning(f"Loaded chain has invalid hash at block {i}. Rejecting loaded chain.")
                    self.chain = []
                    break
                
                stored_merkle = curr_data.get("merkle_root")
                computed_merkle = curr_block.merkle_root
                
                if stored_merkle != computed_merkle:
                    logger.warning(f"Loaded chain has invalid merkle_root at block {i}. Rejecting loaded chain.")
                    self.chain = []
                    break
            else:
                if data.get("state"):
                    self.state = State.from_dict(data["state"])
                logger.info(f"Loaded chain with {len(self.chain)} blocks from {self._chain_file}")
                return

if not self.chain:
                self._create_genesis_block()
                logger.info("Created new genesis block after rejecting invalid chain")

        except Exception as e:
            logger.warning(f"Failed to load chain from {self._chain_file}: {e}. Creating new genesis block.")
            self._create_genesis_block()

    def _serialize_chain_data(self):
        return {
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

def _save_to_file_unlocked(self, data, block_count):
        temp_file = None
        try:
            temp_file = self._chain_file + ".tmp"
            with open(temp_file, 'w') as f:
                json.dump(data, f, indent=2)

            os.replace(temp_file, self._chain_file)
            logger.info("Saved chain with %s blocks to %s", block_count, self._chain_file)

        except Exception as e:
            logger.error(f"Failed to save chain to {self._chain_file}: {e}")
            if temp_file is not None and os.path.exists(temp_file):
                os.remove(temp_file)

def save_to_file(self):
        with self._lock:
            data = self._serialize_chain_data()
            block_count = len(self.chain)
        self._save_to_file_unlocked(data, block_count)

    def _create_genesis_block(self):
        genesis_block = Block(
            index=0,
            previous_hash="0",
            transactions=[]
        )
        genesis_block.hash = "0" * 64
        self.chain.append(genesis_block)

    @property
    def last_block(self):
        with self._lock:
            return self.chain[-1]

    def add_block(self, block):
        with self._lock:
            if block.previous_hash != self.last_block.hash:
                logger.warning("Block %s rejected: Invalid previous hash %s != %s", block.index, block.previous_hash, self.last_block.hash)
                return False

            if block.index != self.last_block.index + 1:
                logger.warning("Block %s rejected: Invalid index %s != %s", block.index, block.index, self.last_block.index + 1)
                return False

            if block.hash != calculate_hash(block.to_header_dict()):
                logger.warning("Block %s rejected: Invalid hash %s", block.index, block.hash)
                return False

            temp_state = self.state.copy()

            for tx in block.transactions:
                result = temp_state.validate_and_apply(tx)

                if not result:
                    logger.warning("Block %s rejected: Transaction failed validation", block.index)
                    return False

self.state = temp_state
            self.chain.append(block)
            
            data = self._serialize_chain_data()
            block_count = len(self.chain)

        self._save_to_file_unlocked(data, block_count)
        return True
