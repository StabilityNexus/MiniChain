from .block import Block, calculate_receipt_root
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
        self.chain_id = "minichain-default"
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
                logger.error("Failed to load genesis config: %s", e)
                sys.exit(1)
        else:
            logger.error("Failed to load genesis config: file %s does not exist.", genesis_path)
            sys.exit(1)
        
        # Apply genesis allocations
        alloc = config.get("alloc", {})
        for address, data in alloc.items():
            balance = data.get("balance", 0)
            if not isinstance(balance, int) or balance < 0:
                logger.error("Invalid genesis balance for %s: %s. Must be a non-negative integer.", address, balance)
                sys.exit(1)
            account = self.state.get_account(address)
            account['balance'] = balance

        self.chain_id = config.get("chain_id", "minichain-default")
        self.state.chain_id = self.chain_id

        timestamp = config.get("timestamp")
        difficulty = config.get("difficulty")
        
        self.target_block_time = config.get("target_block_time", 10000)
        self.alpha = config.get("alpha", 0.1)
        self.current_difficulty = difficulty
        self.avg_block_time = self.target_block_time
        
        genesis_block = Block(
            index=0,
            previous_hash="0",
            transactions=[],
            timestamp=timestamp,
            difficulty=difficulty,
            state_root=self.state.state_root(),
            receipt_root=None,
            receipts=[]
        )
        
        computed_hash = calculate_hash(genesis_block.to_header_dict())
        config_hash = config.get("hash")
        
        if config_hash:
            if config_hash != computed_hash:
                logger.error("Genesis hash mismatch. Config hash: %s, Computed hash: %s", config_hash, computed_hash)
                sys.exit(1)
            genesis_block.hash = config_hash
        else:
            genesis_block.hash = computed_hash
            
        self.chain.append(genesis_block)
        
        # Snapshot the state exactly after genesis allocation for clean reorg rebuilds
        self._genesis_state_snapshot = self.state.snapshot()

    @property
    def last_block(self):
        """
        Returns the most recent block in the chain.
        """
        with self._lock: # Acquire lock for thread-safe access
            return self.chain[-1]

    def get_total_work(self, chain_list=None):
        """
        Calculates the cumulative PoW of a chain.
        Work is proportional to 2^difficulty.
        """
        if chain_list is None:
            with self._lock:
                chain_list = self.chain
        return sum(2 ** (block.difficulty or 1) for block in chain_list)

    def add_block(self, block):
        """
        Validates and adds a block to the chain if all transactions succeed.
        Uses a copied State to ensure atomic validation.
        """
        from .validators import ValidationStatus

        with self._lock:
            try:
                validate_block_link_and_hash(self.last_block, block)
            except ValueError as exc:
                logger.warning("Block %s rejected: %s", block.index, exc)
                return ValidationStatus.INVALID if "hash" in str(exc) else ValidationStatus.FAILED

            if block.difficulty != self.current_difficulty:
                logger.warning("Block %s rejected: Invalid difficulty. Expected %s, got %s", block.index, self.current_difficulty, block.difficulty)
                return ValidationStatus.INVALID

            # Validate transactions on a temporary state copy
            temp_state = self.state.copy()
            temp_state.chain_id = self.chain_id
            receipts = []

            for tx in block.transactions:
                status, receipt = temp_state.validate_and_apply_with_status(tx)

                # Reject block if any transaction fails mathematical validation
                if status != ValidationStatus.VALID:
                    logger.warning("Block %s rejected: Transaction failed validation", block.index)
                    return status
                    
                receipts.append(receipt)

            total_fees = sum(getattr(r, 'gas_used', 0) for r in receipts)
            if block.miner:
                temp_state.credit_mining_reward(block.miner, reward=temp_state.DEFAULT_MINING_REWARD + total_fees)
                
            computed_receipt_root = calculate_receipt_root(receipts)
            if block.receipt_root != computed_receipt_root:
                logger.warning("Block %s rejected: Invalid receipt root. Expected %s, got %s", block.index, computed_receipt_root, block.receipt_root)
                return ValidationStatus.INVALID

            if [r.to_dict() for r in block.receipts] != [r.to_dict() for r in receipts]:
                logger.warning("Block %s rejected: Receipts payload mismatch", block.index)
                return ValidationStatus.INVALID

            # Verify state root
            if block.state_root != temp_state.state_root():
                logger.warning("Block %s rejected: Invalid state root. Expected %s, got %s", block.index, temp_state.state_root(), block.state_root)
                return ValidationStatus.INVALID

            # Update EMA difficulty state
            time_diff = block.timestamp - self.last_block.timestamp
            self.avg_block_time = self.alpha * time_diff + (1 - self.alpha) * self.avg_block_time
            
            if self.avg_block_time > self.target_block_time:
                self.current_difficulty = max(1, self.current_difficulty - 1)
            elif self.avg_block_time < self.target_block_time:
                self.current_difficulty += 1

            # All transactions valid → commit state and append block
            self.state = temp_state
            self.chain.append(block)
            return ValidationStatus.VALID

    def resolve_conflicts(self, new_chain_list) -> tuple[bool, list]:
        """
        Evaluates a competing chain. If it has strictly greater cumulative work,
        attempts a reorg. Rebuilds state from genesis to guarantee validity.
        Returns: (success_bool, list_of_orphaned_transactions)
        """
        if not new_chain_list:
            return False, []

        with self._lock:
            current_work = self.get_total_work()
            new_work = self.get_total_work(new_chain_list)

            if new_work <= current_work:
                logger.debug("Incoming chain (work: %s) is not heavier than local chain (work: %s). Rejecting.", new_work, current_work)
                return False, []

            # 1. Verify genesis block matches
            if new_chain_list[0].hash != self.chain[0].hash:
                logger.warning("Reorg failed: Genesis hash mismatch.")
                return False, []

            logger.info("Incoming chain is heavier (%s > %s). Attempting reorg...", new_work, current_work)

            # 2. Snapshot current chain in case reorg fails validation
            original_chain = list(self.chain)

            # 3. Rebuild state entirely from genesis using the new chain
            temp_state = State()
            temp_state.chain_id = self.chain_id
            temp_state.restore(self._genesis_state_snapshot)

            temp_difficulty = new_chain_list[0].difficulty
            temp_avg_block_time = self.target_block_time

            # Verify and apply blocks 1 to N
            for i in range(1, len(new_chain_list)):
                prev_block = new_chain_list[i-1]
                block = new_chain_list[i]

                if block.difficulty != temp_difficulty:
                    logger.warning("Reorg failed at block %s: Invalid difficulty. Expected %s, got %s", block.index, temp_difficulty, block.difficulty)
                    return False, []

                try:
                    validate_block_link_and_hash(prev_block, block)
                except ValueError as exc:
                    logger.warning("Reorg failed at block %s: %s", block.index, exc)
                    return False, []

                receipts = []
                for tx in block.transactions:
                    from .validators import ValidationStatus
                    status, receipt = temp_state.validate_and_apply_with_status(tx)
                    if status != ValidationStatus.VALID:
                        logger.warning("Reorg failed: Transaction validation failed in block %s", block.index)
                        return False, []
                    receipts.append(receipt)

                total_fees = sum(getattr(r, 'gas_used', 0) for r in receipts)
                if block.miner:
                    temp_state.credit_mining_reward(block.miner, reward=temp_state.DEFAULT_MINING_REWARD + total_fees)

                computed_receipt_root = calculate_receipt_root(receipts)
                if block.receipt_root != computed_receipt_root:
                    logger.warning("Reorg failed: Invalid receipt root at block %s. Expected %s, got %s", block.index, computed_receipt_root, block.receipt_root)
                    return False, []

                if block.state_root != temp_state.state_root():
                    logger.warning("Reorg failed: Invalid state root at block %s", block.index)
                    return False, []

                # Update EMA difficulty state for reorg validation
                time_diff = block.timestamp - prev_block.timestamp
                temp_avg_block_time = self.alpha * time_diff + (1 - self.alpha) * temp_avg_block_time
                if temp_avg_block_time > self.target_block_time:
                    temp_difficulty = max(1, temp_difficulty - 1)
                elif temp_avg_block_time < self.target_block_time:
                    temp_difficulty += 1

            # 4. Success! Compute orphaned transactions.
            old_txs = {tx.tx_id: tx for b in original_chain[1:] for tx in b.transactions}
            new_tx_ids = {tx.tx_id for b in new_chain_list[1:] for tx in b.transactions}
            orphans = [tx for tx_id, tx in old_txs.items() if tx_id not in new_tx_ids]

            self.chain = new_chain_list
            self.state = temp_state
            self.current_difficulty = temp_difficulty
            self.avg_block_time = temp_avg_block_time
            logger.info("Reorg successful! Switched to new chain tip: Block %s", self.last_block.index)
            return True, orphans
