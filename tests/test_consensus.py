"""Unit tests for Proof-of-Work mining primitives."""

from __future__ import annotations

from threading import Event

from minichain.block import BlockHeader
from minichain.consensus import (
    MiningInterrupted,
    compute_next_difficulty_target,
    is_valid_pow,
    mine_block_header,
)


def _header_template(difficulty_target: int) -> BlockHeader:
    return BlockHeader(
        version=0,
        previous_hash="00" * 32,
        merkle_root="11" * 32,
        timestamp=1_740_000_000,
        difficulty_target=difficulty_target,
        nonce=0,
        block_height=10,
    )


def _make_chain(
    *,
    heights: list[int],
    timestamps: list[int],
    difficulty_target: int,
) -> list[BlockHeader]:
    if len(heights) != len(timestamps):
        raise ValueError("heights and timestamps must have the same length")
    return [
        BlockHeader(
            version=0,
            previous_hash=f"{height:064x}",
            merkle_root="22" * 32,
            timestamp=timestamp,
            difficulty_target=difficulty_target,
            nonce=0,
            block_height=height,
        )
        for height, timestamp in zip(heights, timestamps, strict=True)
    ]


def test_valid_pow_is_accepted() -> None:
    header = _header_template(difficulty_target=(1 << 256) - 1)
    assert is_valid_pow(header)


def test_invalid_pow_is_rejected() -> None:
    header = _header_template(difficulty_target=1)
    assert not is_valid_pow(header)


def test_mining_finds_valid_nonce_for_reasonable_target() -> None:
    header = _header_template(difficulty_target=1 << 252)
    nonce, _digest = mine_block_header(header, max_nonce=500_000)

    mined_header = BlockHeader(
        version=header.version,
        previous_hash=header.previous_hash,
        merkle_root=header.merkle_root,
        timestamp=header.timestamp,
        difficulty_target=header.difficulty_target,
        nonce=nonce,
        block_height=header.block_height,
    )
    assert is_valid_pow(mined_header)


def test_mining_honors_stop_event() -> None:
    header = _header_template(difficulty_target=1 << 240)
    stop = Event()
    stop.set()

    try:
        mine_block_header(header, max_nonce=1_000_000, stop_event=stop)
    except MiningInterrupted as exc:
        assert "interrupted" in str(exc).lower()
    else:
        raise AssertionError("Expected mining interruption")


def test_mining_raises_when_nonce_range_exhausted() -> None:
    header = _header_template(difficulty_target=1)
    try:
        mine_block_header(header, start_nonce=0, max_nonce=10)
    except RuntimeError as exc:
        assert "No valid nonce found" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when nonce space exhausted")


def test_difficulty_unchanged_when_not_on_adjustment_height() -> None:
    chain = _make_chain(
        heights=[0, 1, 2, 3, 5],
        timestamps=[0, 10, 20, 30, 40],
        difficulty_target=1_000_000,
    )
    assert (
        compute_next_difficulty_target(
            chain,
            adjustment_interval=4,
            target_block_time_seconds=10,
        )
        == 1_000_000
    )


def test_difficulty_target_decreases_when_blocks_are_fast() -> None:
    chain = _make_chain(
        heights=[0, 1, 2, 3, 4],
        timestamps=[0, 5, 10, 15, 20],
        difficulty_target=1_000_000,
    )
    new_target = compute_next_difficulty_target(
        chain,
        adjustment_interval=4,
        target_block_time_seconds=10,
    )
    assert new_target == 500_000


def test_difficulty_target_increases_when_blocks_are_slow() -> None:
    chain = _make_chain(
        heights=[0, 1, 2, 3, 4],
        timestamps=[0, 20, 40, 60, 80],
        difficulty_target=1_000_000,
    )
    new_target = compute_next_difficulty_target(
        chain,
        adjustment_interval=4,
        target_block_time_seconds=10,
    )
    assert new_target == 2_000_000


def test_difficulty_adjustment_is_capped_to_half_on_extreme_speed() -> None:
    chain = _make_chain(
        heights=[0, 1, 2, 3, 4],
        timestamps=[0, 1, 2, 3, 4],
        difficulty_target=1_000_000,
    )
    new_target = compute_next_difficulty_target(
        chain,
        adjustment_interval=4,
        target_block_time_seconds=10,
    )
    assert new_target == 500_000


def test_difficulty_adjustment_is_capped_to_double_on_extreme_delay() -> None:
    chain = _make_chain(
        heights=[0, 1, 2, 3, 4],
        timestamps=[0, 100, 200, 300, 400],
        difficulty_target=1_000_000,
    )
    new_target = compute_next_difficulty_target(
        chain,
        adjustment_interval=4,
        target_block_time_seconds=10,
    )
    assert new_target == 2_000_000
