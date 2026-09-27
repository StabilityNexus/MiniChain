"""
tests/test_pow.py

Unit tests for minichain.pow.mine_block.

Covers:
  1. Successful mining against an easy target.
  2. Invalid target validation.
  3. max_nonce exceeded.
  4. timeout_seconds exceeded.
  5. Cancellation via progress_callback.
  6. Logger hooks are exercised without raising.
"""

import logging
import pytest

from minichain.block import Block
from minichain.pow import mine_block, MiningExceededError
from minichain.network_config import MAX_TARGET


def _block(target):
    return Block(index=1, previous_hash="0x0", target=target, timestamp=1000)


# ------------------------------------------------------------------
# 1. Successful mining
# ------------------------------------------------------------------

def test_mine_block_succeeds_against_easy_target():
    block = _block(MAX_TARGET)
    result = mine_block(block)
    assert result.hash is not None
    assert int(result.hash, 16) < MAX_TARGET
    assert result is block


def test_mine_block_uses_explicit_target_over_block_target():
    block = _block(target=1)  # near-impossible if used
    result = mine_block(block, target=MAX_TARGET)
    assert result.hash is not None
    assert block.target == MAX_TARGET


# ------------------------------------------------------------------
# 2. Invalid target validation
# ------------------------------------------------------------------

def test_mine_block_rejects_zero_target():
    block = _block(target=0)
    with pytest.raises(ValueError, match="Target must be a positive integer"):
        mine_block(block)


def test_mine_block_rejects_non_integer_target():
    block = _block(target=MAX_TARGET)
    with pytest.raises(ValueError, match="Target must be a positive integer"):
        mine_block(block, target="not-an-int")


# ------------------------------------------------------------------
# 3. max_nonce exceeded
# ------------------------------------------------------------------

def test_mine_block_raises_when_max_nonce_exceeded():
    block = _block(target=1)  # effectively unattainable within a handful of hashes
    with pytest.raises(MiningExceededError, match="max_nonce exceeded"):
        mine_block(block, max_nonce=5)


# ------------------------------------------------------------------
# 4. timeout_seconds exceeded
# ------------------------------------------------------------------

def test_mine_block_raises_when_timeout_exceeded(caplog):
    block = _block(target=1)
    logger = logging.getLogger("test-pow-timeout")
    with caplog.at_level(logging.WARNING, logger="test-pow-timeout"):
        with pytest.raises(MiningExceededError, match="timeout exceeded"):
            mine_block(block, timeout_seconds=0, logger=logger)
    assert any("Mining timeout exceeded" in record.message for record in caplog.records)


# ------------------------------------------------------------------
# 5. Cancellation via progress_callback
# ------------------------------------------------------------------

def test_mine_block_cancelled_via_progress_callback(caplog):
    block = _block(target=1)
    logger = logging.getLogger("test-pow-cancel")

    def cancel_immediately(nonce, block_hash):
        return False

    with caplog.at_level(logging.INFO, logger="test-pow-cancel"):
        with pytest.raises(MiningExceededError, match="Mining cancelled"):
            mine_block(block, progress_callback=cancel_immediately, max_nonce=1000, logger=logger)
    assert any("cancelled" in record.message for record in caplog.records)


def test_mine_block_progress_callback_can_allow_continuation():
    block = _block(target=MAX_TARGET)
    calls = []

    def track(nonce, block_hash):
        calls.append(nonce)
        return True

    result = mine_block(block, progress_callback=track)
    assert result.hash is not None
    # With an easy target the first attempt should succeed before the callback is consulted.
    assert calls == [] or all(isinstance(n, int) for n in calls)


# ------------------------------------------------------------------
# 6. Logger hooks
# ------------------------------------------------------------------

def test_mine_block_logs_success(caplog):
    block = _block(target=MAX_TARGET)
    logger = logging.getLogger("test-pow-success")
    with caplog.at_level(logging.INFO, logger="test-pow-success"):
        mine_block(block, logger=logger)
    assert any("Success" in record.message for record in caplog.records)


def test_mine_block_logs_max_nonce_exceeded(caplog):
    block = _block(target=1)
    logger = logging.getLogger("test-pow-maxnonce")
    with caplog.at_level(logging.WARNING, logger="test-pow-maxnonce"):
        with pytest.raises(MiningExceededError):
            mine_block(block, max_nonce=5, logger=logger)
    assert any("Max nonce exceeded" in record.message for record in caplog.records)
