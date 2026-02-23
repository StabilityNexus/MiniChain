"""Chain manager and fork-resolution logic."""

from __future__ import annotations

from dataclasses import dataclass

from minichain.block import Block, BlockHeader
from minichain.consensus import compute_next_difficulty_target, is_valid_pow
from minichain.genesis import GENESIS_PREVIOUS_HASH
from minichain.state import State, StateTransitionError


class ChainValidationError(ValueError):
    """Raised when a block or branch is invalid."""


@dataclass(frozen=True)
class ChainConfig:
    """Configuration for chain validation and state transitions."""

    block_reward: int = 50
    difficulty_adjustment_interval: int = 10
    target_block_time_seconds: int = 30

    def validate(self) -> None:
        if self.block_reward < 0:
            raise ValueError("block_reward must be non-negative")
        if self.difficulty_adjustment_interval <= 0:
            raise ValueError("difficulty_adjustment_interval must be positive")
        if self.target_block_time_seconds <= 0:
            raise ValueError("target_block_time_seconds must be positive")


class ChainManager:
    """Maintains canonical chain, block index, and current canonical state."""

    def __init__(
        self,
        *,
        genesis_block: Block,
        genesis_state: State,
        config: ChainConfig | None = None,
    ) -> None:
        self.config = config or ChainConfig()
        self.config.validate()
        self._validate_genesis(genesis_block)

        genesis_hash = genesis_block.hash().hex()
        self._genesis_hash = genesis_hash
        self._blocks_by_hash: dict[str, Block] = {genesis_hash: genesis_block}
        self._heights: dict[str, int] = {genesis_hash: 0}
        self._canonical_hashes: list[str] = [genesis_hash]
        self._tip_hash = genesis_hash

        self._genesis_state = genesis_state.copy()
        self.state = genesis_state.copy()

    @property
    def tip_hash(self) -> str:
        return self._tip_hash

    @property
    def height(self) -> int:
        return self._heights[self._tip_hash]

    @property
    def tip_block(self) -> Block:
        return self._blocks_by_hash[self._tip_hash]

    def contains_block(self, block_hash: str) -> bool:
        return block_hash in self._blocks_by_hash

    def canonical_chain(self) -> list[Block]:
        return [self._blocks_by_hash[block_hash] for block_hash in self._canonical_hashes]

    def get_block_by_hash(self, block_hash: str) -> Block | None:
        return self._blocks_by_hash.get(block_hash)

    def get_canonical_block_by_height(self, height: int) -> Block | None:
        if height < 0 or height >= len(self._canonical_hashes):
            return None
        return self._blocks_by_hash[self._canonical_hashes[height]]

    def expected_next_difficulty(self, *, parent_hash: str | None = None) -> int:
        """Compute the expected next block target after the given parent."""
        path_hashes = (
            self._canonical_hashes
            if parent_hash is None
            else self._path_from_genesis(parent_hash)
        )
        headers = [self._blocks_by_hash[block_hash].header for block_hash in path_hashes]
        return compute_next_difficulty_target(
            headers,
            adjustment_interval=self.config.difficulty_adjustment_interval,
            target_block_time_seconds=self.config.target_block_time_seconds,
        )

    def add_block(self, block: Block) -> str:
        """Add a block to chain storage and update canonical tip when appropriate."""
        block_hash = block.hash().hex()
        if block_hash in self._blocks_by_hash:
            return "duplicate"

        parent_hash = block.header.previous_hash
        if parent_hash not in self._blocks_by_hash:
            raise ChainValidationError(f"Unknown parent block: {parent_hash}")

        self._blocks_by_hash[block_hash] = block
        self._heights[block_hash] = block.header.block_height

        try:
            candidate_path, candidate_state = self._replay_state_for_tip(block_hash)
        except ChainValidationError:
            self._blocks_by_hash.pop(block_hash, None)
            self._heights.pop(block_hash, None)
            raise

        parent_is_tip = parent_hash == self._tip_hash
        candidate_height = len(candidate_path) - 1
        canonical_height = self.height

        if parent_is_tip and candidate_height == canonical_height + 1:
            self._canonical_hashes.append(block_hash)
            self._tip_hash = block_hash
            self.state = candidate_state
            return "extended"

        if candidate_height > canonical_height:
            self._canonical_hashes = candidate_path
            self._tip_hash = block_hash
            self.state = candidate_state
            return "reorg"

        return "stored_fork"

    def _replay_state_for_tip(self, tip_hash: str) -> tuple[list[str], State]:
        path_hashes = self._path_from_genesis(tip_hash)
        replay_state = self._genesis_state.copy()
        replayed_headers = [self._blocks_by_hash[path_hashes[0]].header]

        for index, block_hash in enumerate(path_hashes[1:], start=1):
            block = self._blocks_by_hash[block_hash]
            parent_hash = path_hashes[index - 1]
            parent_header = replayed_headers[-1]

            self._validate_link(
                parent_hash=parent_hash,
                parent_height=parent_header.block_height,
                block=block,
            )
            self._validate_consensus(block=block, parent_headers=replayed_headers)

            try:
                replay_state.apply_block(block, block_reward=self.config.block_reward)
            except StateTransitionError as exc:
                raise ChainValidationError(f"State transition failed: {exc}") from exc

            replayed_headers.append(block.header)

        return path_hashes, replay_state

    def _path_from_genesis(self, tip_hash: str) -> list[str]:
        if tip_hash not in self._blocks_by_hash:
            raise ChainValidationError(f"Unknown block hash: {tip_hash}")

        path: list[str] = []
        seen: set[str] = set()
        cursor = tip_hash
        while True:
            if cursor in seen:
                raise ChainValidationError("Cycle detected in block ancestry")
            seen.add(cursor)
            path.append(cursor)

            if cursor == self._genesis_hash:
                break

            parent_hash = self._blocks_by_hash[cursor].header.previous_hash
            if parent_hash not in self._blocks_by_hash:
                raise ChainValidationError(
                    f"Missing ancestor for block {cursor}: {parent_hash}"
                )
            cursor = parent_hash

        path.reverse()
        if path[0] != self._genesis_hash:
            raise ChainValidationError("Candidate chain does not start at genesis")
        return path

    def _validate_consensus(self, *, block: Block, parent_headers: list[BlockHeader]) -> None:
        if not block.has_valid_merkle_root():
            raise ChainValidationError("Block merkle_root does not match transaction body")

        expected_target = compute_next_difficulty_target(
            parent_headers,
            adjustment_interval=self.config.difficulty_adjustment_interval,
            target_block_time_seconds=self.config.target_block_time_seconds,
        )
        if block.header.difficulty_target != expected_target:
            raise ChainValidationError(
                "Invalid difficulty target: "
                f"expected {expected_target}, got {block.header.difficulty_target}"
            )
        if not is_valid_pow(block.header):
            raise ChainValidationError("Block does not satisfy Proof-of-Work target")

    @staticmethod
    def _validate_link(*, parent_hash: str, parent_height: int, block: Block) -> None:
        if block.header.previous_hash != parent_hash:
            raise ChainValidationError("Block previous_hash does not match parent hash")
        expected_height = parent_height + 1
        if block.header.block_height != expected_height:
            raise ChainValidationError(
                f"Invalid block height: expected {expected_height}, got {block.header.block_height}"
            )

    @staticmethod
    def _validate_genesis(genesis_block: Block) -> None:
        if genesis_block.header.block_height != 0:
            raise ValueError("Genesis block height must be 0")
        if genesis_block.header.previous_hash != GENESIS_PREVIOUS_HASH:
            raise ValueError("Genesis previous_hash must be all zeros")
        if genesis_block.transactions:
            raise ValueError("Genesis block must not include transactions")
