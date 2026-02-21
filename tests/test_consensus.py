"""Unit tests for Proof-of-Work mining primitives."""

from __future__ import annotations

from threading import Event

from minichain.block import BlockHeader
from minichain.consensus import MiningInterrupted, is_valid_pow, mine_block_header


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
