"""Consensus and Proof-of-Work mining primitives."""

from __future__ import annotations

from dataclasses import replace
from threading import Event
from typing import Sequence

from minichain.block import BlockHeader

MAX_TARGET = (1 << 256) - 1


class MiningInterrupted(Exception):
    """Raised when mining is cancelled via a stop signal."""


def hash_to_int(block_hash: bytes) -> int:
    """Convert a hash digest into a big-endian integer."""
    return int.from_bytes(block_hash, byteorder="big", signed=False)


def validate_difficulty_target(target: int) -> None:
    """Validate difficulty target bounds."""
    if target <= 0:
        raise ValueError("difficulty_target must be positive")
    if target > MAX_TARGET:
        raise ValueError("difficulty_target exceeds hash space")


def is_valid_pow(header: BlockHeader) -> bool:
    """Return whether a header satisfies its own difficulty target."""
    if header.difficulty_target <= 0 or header.difficulty_target > MAX_TARGET:
        return False
    return hash_to_int(header.hash()) <= header.difficulty_target


def compute_next_difficulty_target(
    chain: Sequence[BlockHeader],
    *,
    adjustment_interval: int = 10,
    target_block_time_seconds: int = 30,
) -> int:
    """Compute the next difficulty target using bounded proportional retargeting."""
    if adjustment_interval <= 0:
        raise ValueError("adjustment_interval must be positive")
    if target_block_time_seconds <= 0:
        raise ValueError("target_block_time_seconds must be positive")
    if not chain:
        raise ValueError("chain must contain at least one header")

    tip = chain[-1]
    validate_difficulty_target(tip.difficulty_target)

    if tip.block_height == 0:
        return tip.difficulty_target
    if tip.block_height % adjustment_interval != 0:
        return tip.difficulty_target
    if len(chain) <= adjustment_interval:
        return tip.difficulty_target

    start_header = chain[-(adjustment_interval + 1)]
    elapsed_seconds = tip.timestamp - start_header.timestamp
    if elapsed_seconds <= 0:
        elapsed_seconds = 1

    expected_seconds = adjustment_interval * target_block_time_seconds
    unbounded_target = (tip.difficulty_target * elapsed_seconds) // expected_seconds

    min_target = max(1, tip.difficulty_target // 2)
    max_target = min(MAX_TARGET, tip.difficulty_target * 2)
    bounded_target = min(max(unbounded_target, min_target), max_target)

    validate_difficulty_target(bounded_target)
    return bounded_target


def mine_block_header(
    header_template: BlockHeader,
    *,
    start_nonce: int = 0,
    max_nonce: int = (1 << 64) - 1,
    stop_event: Event | None = None,
) -> tuple[int, bytes]:
    """Search nonces until a header hash satisfies the difficulty target."""
    validate_difficulty_target(header_template.difficulty_target)
    if start_nonce < 0:
        raise ValueError("start_nonce must be non-negative")
    if max_nonce < start_nonce:
        raise ValueError("max_nonce must be greater than or equal to start_nonce")

    for nonce in range(start_nonce, max_nonce + 1):
        if stop_event is not None and stop_event.is_set():
            raise MiningInterrupted("Mining interrupted by stop event")

        candidate = replace(header_template, nonce=nonce)
        digest = candidate.hash()
        if hash_to_int(digest) <= candidate.difficulty_target:
            return nonce, digest

    raise RuntimeError("No valid nonce found within nonce range")
