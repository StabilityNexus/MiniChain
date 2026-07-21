from .block import Block, calculate_receipt_root
from .state import State
from .pow import calculate_hash
import logging
import threading
import json
import os
import sys
import time
from .network_config import MAX_FUTURE_BLOCK_TIME_MS

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

    if int(block.hash, 16) > block.target:
        raise ValueError(f"invalid Proof of Work: hash {block.hash} does not satisfy target {block.target}")

    if block.timestamp <= previous_block.timestamp:
        raise ValueError(f"invalid timestamp: {block.timestamp} is not strictly greater than previous block timestamp {previous_block.timestamp}")

    max_allowed_time = int(time.time() * 1000) + MAX_FUTURE_BLOCK_TIME_MS
    if block.timestamp > max_allowed_time:
        raise ValueError(f"invalid timestamp: {block.timestamp} is too far in the future (max allowed: {max_allowed_time})")


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
        total_alloc = 0
        for address, data in alloc.items():
            balance = data.get("balance", 0)
            if not isinstance(balance, int) or balance < 0:
                logger.error("Invalid genesis balance for %s: %s. Must be a non-negative integer.", address, balance)
                sys.exit(1)
            account = self.state.get_account(address)
            account['balance'] = balance
            total_alloc += balance

        initial_supply = config.get("initial_supply")
        if initial_supply is not None and initial_supply != total_alloc:
            logger.error("Genesis allocation mismatch: initial_supply is %s but alloc sum is %s", initial_supply, total_alloc)
            sys.exit(1)

        self.chain_id = config.get("chain_id", "minichain-default")
        self.state.chain_id = self.chain_id

        timestamp = config.get("timestamp")
        difficulty = config.get("difficulty")
        if isinstance(difficulty, int) and difficulty <= 256:
            difficulty = (1 << (256 - 4 * difficulty)) - 1
        
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
        Work is proportional to expected hashes (2^256 // target).
        """
        if chain_list is None:
            with self._lock:
                chain_list = self.chain
        return sum((1 << 256) // (block.target + 1) for block in chain_list)

    def _next_difficulty(self, difficulty, avg_block_time):
        """Advance the EMA difficulty control after a block, returning the new target."""
        ratio = avg_block_time / self.target_block_time
        # Clamp ratio to prevent extreme swings
        ratio = max(0.25, min(4.0, ratio))
        new_target = int(difficulty * ratio)
        max_target = (1 << 256) - 1
        return max(1, min(max_target, new_target))

    def _apply_block(self, prev_block, block, state, difficulty, avg_block_time):
        """
        Canonical block-application pipeline shared by add_block and resolve_conflicts.
        Validates `block` against `prev_block` and applies its transactions to `state`
        (mutated in place). On any non-VALID status the caller must discard `state`.
        Returns: (ValidationStatus, new_difficulty, new_avg_block_time)
        """
        from .validators import ValidationStatus

        try:
            validate_block_link_and_hash(prev_block, block)
        except ValueError as exc:
            logger.warning("Block %s rejected: %s", block.index, exc)
            status = ValidationStatus.INVALID if "hash" in str(exc) else ValidationStatus.FAILED
            return status, difficulty, avg_block_time

        if block.target != difficulty:
            logger.warning("Block %s rejected: Invalid target. Expected %s, got %s", block.index, difficulty, block.target)
            return ValidationStatus.INVALID, difficulty, avg_block_time

        receipts = []
        for tx in block.transactions:
            status, receipt = state.validate_and_apply_with_status(tx)
            if status != ValidationStatus.VALID:
                logger.warning("Block %s rejected: Transaction failed validation", block.index)
                return status, difficulty, avg_block_time
            receipts.append(receipt)

        total_fees = sum(getattr(r, 'gas_used', 0) * getattr(tx, 'max_fee_per_gas', 0) for r, tx in zip(receipts, block.transactions))
        if block.miner:
            state.credit_mining_reward(block.miner, reward=state.DEFAULT_MINING_REWARD + total_fees)

        computed_receipt_root = calculate_receipt_root(receipts)
        if block.receipt_root != computed_receipt_root:
            logger.warning("Block %s rejected: Invalid receipt root. Expected %s, got %s", block.index, computed_receipt_root, block.receipt_root)
            return ValidationStatus.INVALID, difficulty, avg_block_time

        if [r.to_dict() for r in block.receipts] != [r.to_dict() for r in receipts]:
            logger.warning("Block %s rejected: Receipts payload mismatch", block.index)
            return ValidationStatus.INVALID, difficulty, avg_block_time

        computed_state_root = state.state_root()
        if block.state_root != computed_state_root:
            logger.warning("Block %s rejected: Invalid state root. Expected %s, got %s", block.index, computed_state_root, block.state_root)
            return ValidationStatus.INVALID, difficulty, avg_block_time

        new_avg = self.alpha * (block.timestamp - prev_block.timestamp) + (1 - self.alpha) * avg_block_time
        return ValidationStatus.VALID, self._next_difficulty(difficulty, new_avg), new_avg

    def add_block(self, block):
        """
        Validates and adds a block to the chain if all transactions succeed.
        Uses a copied State to ensure atomic validation.
        """
        from .validators import ValidationStatus

        with self._lock:
            temp_state = self.state.copy()
            temp_state.chain_id = self.chain_id
            status, new_difficulty, new_avg = self._apply_block(
                self.last_block, block, temp_state, self.current_difficulty, self.avg_block_time
            )
            if status != ValidationStatus.VALID:
                return status

            # All transactions valid → commit state and append block
            self.state = temp_state
            self.current_difficulty = new_difficulty
            self.avg_block_time = new_avg
            self.chain.append(block)
            return ValidationStatus.VALID

    def resolve_conflicts(self, new_chain_list) -> tuple[bool, list]:
        """
        Evaluates a competing partial or full chain. If it has strictly greater cumulative work,
        attempts a reorg. Rebuilds state from genesis to guarantee validity.
        Returns: (success_bool, list_of_orphaned_transactions)
        """
        from .validators import ValidationStatus

        if not new_chain_list:
            return False, []

        with self._lock:
            first_block = new_chain_list[0]
            fork_idx = first_block.index
            
            if fork_idx == 0:
                if first_block.hash != self.chain[0].hash:
                    logger.warning("Reorg failed: Genesis hash mismatch.")
                    return False, []
            else:
                if fork_idx > len(self.chain):
                    logger.warning("Reorg failed: Partial chain does not connect to our history.")
                    return False, []
                if first_block.previous_hash != self.chain[fork_idx - 1].hash:
                    logger.warning("Reorg failed: Partial chain hash mismatch at fork point.")
                    return False, []

            proposed_chain = self.chain[:fork_idx] + new_chain_list

            current_work = self.get_total_work()
            new_work = self.get_total_work(proposed_chain)

            if new_work <= current_work:
                logger.debug("Incoming chain (work: %s) is not heavier than local chain (work: %s). Rejecting.", new_work, current_work)
                return False, []

            logger.info("Incoming chain is heavier (%s > %s). Attempting reorg...", new_work, current_work)

            original_chain = list(self.chain)

            temp_state = State()
            temp_state.chain_id = self.chain_id
            temp_state.restore(self._genesis_state_snapshot)

            temp_difficulty = proposed_chain[0].target
            temp_avg_block_time = self.target_block_time

            for i in range(1, len(proposed_chain)):
                status, temp_difficulty, temp_avg_block_time = self._apply_block(
                    proposed_chain[i - 1], proposed_chain[i], temp_state, temp_difficulty, temp_avg_block_time
                )
                if status != ValidationStatus.VALID:
                    logger.warning("Reorg failed at block %s", proposed_chain[i].index)
                    return False, []

            old_txs = {tx.tx_id: tx for b in original_chain[1:] for tx in b.transactions}
            new_tx_ids = {tx.tx_id for b in proposed_chain[1:] for tx in b.transactions}
            orphans = [tx for tx_id, tx in old_txs.items() if tx_id not in new_tx_ids]

            self.chain = proposed_chain
            self.state = temp_state
            self.current_difficulty = temp_difficulty
            self.avg_block_time = temp_avg_block_time
            logger.info("Reorg successful! Switched to new chain tip: Block %s", self.last_block.index)
            return True, orphans
