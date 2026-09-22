"""
tests/test_chain_extra.py

Additional unit coverage for minichain.chain: validate_block_link_and_hash's
individual rejection branches (tested directly, without needing a full
mined chain) and Blockchain's genesis-config validation (which sys.exit(1)s
on malformed config -- untested by the happy-path fixture every other test
relies on).
"""

import json
import os
import tempfile

import pytest

from minichain.block import Block
from minichain.chain import Blockchain, validate_block_link_and_hash
from minichain.network_config import MAX_TARGET
from minichain.pow import calculate_hash


def _prev_block():
    prev = Block(index=0, previous_hash="0", target=MAX_TARGET, timestamp=1000)
    prev.hash = prev.compute_hash()
    return prev


def _self_consistent_block(index, previous_hash, target, timestamp):
    """Build a block whose stored hash matches its own header -- i.e. it will
    pass validate_block_link_and_hash's hash-consistency check (line 27-28)
    regardless of what index/previous_hash/target/timestamp it carries, so a
    test can isolate exactly one of the *other* checks."""
    block = Block(index=index, previous_hash=previous_hash, target=target, timestamp=timestamp)
    block.hash = calculate_hash(block.to_header_dict())
    return block


# ------------------------------------------------------------------
# validate_block_link_and_hash
# ------------------------------------------------------------------

def test_link_valid_pair_passes():
    prev = _prev_block()
    child = _self_consistent_block(1, prev.hash, MAX_TARGET, prev.timestamp + 1)
    validate_block_link_and_hash(prev, child)  # must not raise


def test_link_rejects_wrong_previous_hash():
    prev = _prev_block()
    child = _self_consistent_block(1, "0" * 64, MAX_TARGET, prev.timestamp + 1)
    with pytest.raises(ValueError, match="invalid previous hash"):
        validate_block_link_and_hash(prev, child)


def test_link_rejects_wrong_index():
    prev = _prev_block()
    child = _self_consistent_block(5, prev.hash, MAX_TARGET, prev.timestamp + 1)
    with pytest.raises(ValueError, match="invalid index"):
        validate_block_link_and_hash(prev, child)


def test_link_rejects_tampered_hash():
    prev = _prev_block()
    child = _self_consistent_block(1, prev.hash, MAX_TARGET, prev.timestamp + 1)
    child.hash = "f" * 64  # no longer matches its own header
    with pytest.raises(ValueError, match="invalid hash"):
        validate_block_link_and_hash(prev, child)


def test_link_rejects_non_positive_target():
    prev = _prev_block()
    child = _self_consistent_block(1, prev.hash, 0, prev.timestamp + 1)
    with pytest.raises(ValueError, match="invalid target"):
        validate_block_link_and_hash(prev, child)


def test_link_rejects_target_above_max():
    prev = _prev_block()
    child = _self_consistent_block(1, prev.hash, MAX_TARGET + 1, prev.timestamp + 1)
    with pytest.raises(ValueError, match="invalid target"):
        validate_block_link_and_hash(prev, child)


def test_link_rejects_hash_not_satisfying_pow():
    prev = _prev_block()
    # target=1 -- virtually any hash is >= 1, so PoW is not satisfied.
    child = _self_consistent_block(1, prev.hash, 1, prev.timestamp + 1)
    with pytest.raises(ValueError, match="does not satisfy target"):
        validate_block_link_and_hash(prev, child)


def test_link_rejects_non_increasing_timestamp():
    prev = _prev_block()
    child = _self_consistent_block(1, prev.hash, MAX_TARGET, prev.timestamp)  # not strictly greater
    with pytest.raises(ValueError, match="not strictly greater"):
        validate_block_link_and_hash(prev, child)


def test_link_rejects_timestamp_too_far_in_future():
    from minichain.network_config import MAX_FUTURE_BLOCK_TIME_MS
    import time
    prev = _prev_block()
    far_future = int(time.time() * 1000) + MAX_FUTURE_BLOCK_TIME_MS + 1_000_000
    child = _self_consistent_block(1, prev.hash, MAX_TARGET, far_future)
    with pytest.raises(ValueError, match="too far in the future"):
        validate_block_link_and_hash(prev, child)


# ------------------------------------------------------------------
# Genesis config validation
# ------------------------------------------------------------------

BASE_GENESIS = {
    "chain_id": "minichain-default",
    "timestamp": 1716880000000,
    "target": "0x0000FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF",
    "target_block_time": 10000,
    "alpha": 0.1,
    "alloc": {},
}


def _write_genesis(tmpdir, overrides=None, omit=()):
    config = dict(BASE_GENESIS)
    if overrides:
        config.update(overrides)
    for key in omit:
        config.pop(key, None)
    path = os.path.join(tmpdir, "genesis.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f)
    return path


@pytest.fixture
def tmpdir():
    with tempfile.TemporaryDirectory() as d:
        yield d


def test_genesis_missing_file_exits(tmpdir):
    missing_path = os.path.join(tmpdir, "does-not-exist.json")
    with pytest.raises(SystemExit):
        Blockchain(genesis_path=missing_path)


def test_genesis_malformed_json_exits(tmpdir):
    path = os.path.join(tmpdir, "genesis.json")
    with open(path, "w", encoding="utf-8") as f:
        f.write("{not valid json")
    with pytest.raises(SystemExit):
        Blockchain(genesis_path=path)


def test_genesis_negative_alloc_balance_exits(tmpdir):
    path = _write_genesis(tmpdir, overrides={"alloc": {"deadbeef" * 5: {"balance": -1}}})
    with pytest.raises(SystemExit):
        Blockchain(genesis_path=path)


def test_genesis_non_integer_alloc_balance_exits(tmpdir):
    path = _write_genesis(tmpdir, overrides={"alloc": {"deadbeef" * 5: {"balance": "lots"}}})
    with pytest.raises(SystemExit):
        Blockchain(genesis_path=path)


def test_genesis_initial_supply_mismatch_exits(tmpdir):
    path = _write_genesis(tmpdir, overrides={
        "alloc": {"deadbeef" * 5: {"balance": 100}},
        "initial_supply": 999,
    })
    with pytest.raises(SystemExit):
        Blockchain(genesis_path=path)


def test_genesis_initial_supply_matching_alloc_succeeds(tmpdir):
    path = _write_genesis(tmpdir, overrides={
        "alloc": {"deadbeef" * 5: {"balance": 100}},
        "initial_supply": 100,
    })
    bc = Blockchain(genesis_path=path)
    assert bc.state.get_account("deadbeef" * 5)["balance"] == 100


def test_genesis_missing_target_exits(tmpdir):
    path = _write_genesis(tmpdir, omit=("target",))
    with pytest.raises(SystemExit):
        Blockchain(genesis_path=path)


def test_genesis_target_out_of_bounds_exits(tmpdir):
    path = _write_genesis(tmpdir, overrides={"target": hex(MAX_TARGET + 1)})
    with pytest.raises(SystemExit):
        Blockchain(genesis_path=path)


def test_genesis_zero_target_exits(tmpdir):
    path = _write_genesis(tmpdir, overrides={"target": "0x0"})
    with pytest.raises(SystemExit):
        Blockchain(genesis_path=path)


def test_genesis_hash_mismatch_exits(tmpdir):
    path = _write_genesis(tmpdir, overrides={"hash": "f" * 64})
    with pytest.raises(SystemExit):
        Blockchain(genesis_path=path)


def test_genesis_hash_matching_config_succeeds(tmpdir):
    # First build a genesis normally to learn its computed hash...
    path = _write_genesis(tmpdir)
    bc = Blockchain(genesis_path=path)
    real_hash = bc.chain[0].hash

    # ...then re-load with that hash pinned in the config, exercising the
    # "config hash matches computed hash" branch explicitly.
    path2 = _write_genesis(tmpdir, overrides={"hash": real_hash})
    bc2 = Blockchain(genesis_path=path2)
    assert bc2.chain[0].hash == real_hash
