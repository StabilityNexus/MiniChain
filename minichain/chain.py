import logging
import threading
from .block import Block
from .state import State
from .pow import calculate_hash

logger = logging.getLogger(__name__)

def validate_block_link_and_hash(previous_block, block):
    if block.previous_hash != previous_block.hash:
        raise ValueError("Invalid previous hash")
    if block.index != previous_block.index + 1:
        raise ValueError("Invalid index")
    if block.hash != calculate_hash(block.to_header_dict()):
        raise ValueError("Invalid hash")

class Blockchain:
    def __init__(self):
        self.chain = []
        self.state = State()
        self._lock = threading.RLock()
        self._create_genesis_block()

    def _create_genesis_block(self):
        genesis = Block(index=0, previous_hash="0", transactions=[])
        genesis.hash = "0" * 64
        self.chain.append(genesis)

    @property
    def last_block(self):
        with self._lock:
            return self.chain[-1]

    def add_block(self, block):
        with self._lock:
            try:
                validate_block_link_and_hash(self.last_block, block)
            except ValueError as exc:
                logger.warning(f"Block {block.index} rejected: {exc}")
                return False

            temp_state = self.state.copy()
            for tx in block.transactions:
                if not temp_state.validate_and_apply(tx):
                    return False

            self.state = temp_state
            self.chain.append(block)
            return True