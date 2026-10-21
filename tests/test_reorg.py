import pytest
import os
import json
import time

from minichain.chain import Blockchain
from minichain.transaction import Transaction
from minichain.mempool import Mempool

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from main import mine_and_process_block

from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

@pytest.fixture
def genesis_file(tmp_path):
    path = tmp_path / "genesis_reorg.json"
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    data = {
        "timestamp": int(time.time()),
        "target": int("F"*64, 16),
        "alloc": {
            pk: {"balance": 1000}
        }
    }
    with open(path, "w") as f:
        json.dump(data, f)
    return str(path), sk, pk

def test_resolve_conflicts_heavier_chain(genesis_file):
    g_path, sk, pk = genesis_file
    
    node_a = Blockchain(genesis_path=g_path)
    node_b = Blockchain(genesis_path=g_path)
    
    assert node_a.get_total_work() == node_b.get_total_work()
    
    pool_b = Mempool()
    tx = Transaction(sender=pk, receiver="b"*64, amount=10, nonce=0, gas_limit=1, fee_per_gas=1)
    tx.sign(sk)
    pool_b.add_transaction(tx)
    
    mined_b = mine_and_process_block(node_b, pool_b, pk)
    assert mined_b is not None
    assert node_b.get_total_work() > node_a.get_total_work()
    
    # Node A receives Node B's chain
    success, orphans = node_a.resolve_conflicts(node_b.chain)
    
    assert success is True
    assert node_a.last_block.hash == node_b.last_block.hash
    assert node_a.state.accounts == node_b.state.accounts
    assert len(orphans) == 0

def test_resolve_conflicts_reorg_with_orphans(genesis_file):
    g_path, sk, pk = genesis_file
    
    node_a = Blockchain(genesis_path=g_path)
    node_b = Blockchain(genesis_path=g_path)
    
    pool_a = Mempool()
    pool_b = Mempool()
    
    # Node A mines tx1 (nonce 0)
    tx1 = Transaction(sender=pk, receiver="a"*64, amount=10, nonce=0, gas_limit=1, fee_per_gas=1)
    tx1.sign(sk)
    pool_a.add_transaction(tx1)
    mine_and_process_block(node_a, pool_a, pk)
    
    # Node B mines tx2 (nonce 0, competing transaction)
    tx2 = Transaction(sender=pk, receiver="b"*64, amount=20, nonce=0, gas_limit=1, fee_per_gas=1)
    tx2.sign(sk)
    pool_b.add_transaction(tx2)
    mine_and_process_block(node_b, pool_b, pk)
    
    # Node B mines tx3 (nonce 1) to become the heavier chain
    tx3 = Transaction(sender=pk, receiver="c"*64, amount=30, nonce=1, gas_limit=1, fee_per_gas=1)
    tx3.sign(sk)
    pool_b.add_transaction(tx3)
    block_b2 = mine_and_process_block(node_b, pool_b, pk)
    
    assert node_b.get_total_work() > node_a.get_total_work()
    
    # Node A attempts reorg using B's heavier chain
    success, orphans = node_a.resolve_conflicts(node_b.chain)
    
    assert success is True
    assert node_a.last_block.hash == block_b2.hash
    
    # tx1 was in A's chain but NOT in B's chain. It should be orphaned.
    assert len(orphans) == 1
    assert orphans[0].tx_id == tx1.tx_id

def test_resolve_conflicts_rejects_lighter_chain(genesis_file):
    g_path, sk, pk = genesis_file
    
    node_a = Blockchain(genesis_path=g_path)
    node_b = Blockchain(genesis_path=g_path)
    
    pool_a = Mempool()
    
    # Node A mines a block
    tx1 = Transaction(sender=pk, receiver="a"*64, amount=10, nonce=0, gas_limit=1, fee_per_gas=1)
    tx1.sign(sk)
    pool_a.add_transaction(tx1)
    mine_and_process_block(node_a, pool_a, pk)
    
    # Node B is empty. It tries to reorg Node A with its shorter chain.
    success, orphans = node_a.resolve_conflicts(node_b.chain)
    
    assert success is False
    assert len(orphans) == 0
    assert node_a.get_total_work() > node_b.get_total_work()


def test_work_comparison_skips_common_prefix(genesis_file, monkeypatch):
    g_path, sk, pk = genesis_file
    node_a = Blockchain(genesis_path=g_path)
    node_b = Blockchain(genesis_path=g_path)

    for _ in range(5):
        assert mine_and_process_block(node_a, Mempool(), pk) is not None
    assert node_b.resolve_conflicts(node_a.chain)[0] is True

    # The chains now share 6 blocks and fork: A adds 1 block, B adds 2.
    assert mine_and_process_block(node_a, Mempool(), pk) is not None
    for _ in range(2):
        assert mine_and_process_block(node_b, Mempool(), pk) is not None

    summed = []
    real = node_a.get_total_work
    monkeypatch.setattr(node_a, "get_total_work", lambda chain_list=None: (summed.append(len(chain_list or node_a.chain)), real(chain_list))[1])

    # Peer sends its whole chain, common prefix included.
    success, _ = node_a.resolve_conflicts(node_b.chain)

    assert success is True
    assert node_a.last_block.hash == node_b.last_block.hash
    assert summed == [1, 2]  # only the blocks after the fork point were summed


def test_chain_that_adds_nothing_is_rejected(genesis_file):
    g_path, sk, pk = genesis_file
    node_a = Blockchain(genesis_path=g_path)
    for _ in range(3):
        mine_and_process_block(node_a, Mempool(), pk)
    assert node_a.resolve_conflicts(list(node_a.chain)) == (False, [])
    assert node_a.resolve_conflicts(node_a.chain[:2]) == (False, [])


OTHER_MINER = "ab" * 32


def _fork(g_path, pk, shared, a_extra, b_extra):
    """Two nodes that share `shared` mined blocks, then mine `a_extra` and `b_extra` blocks apart."""
    node_a = Blockchain(genesis_path=g_path)
    node_b = Blockchain(genesis_path=g_path)
    for _ in range(shared):
        assert mine_and_process_block(node_a, Mempool(), pk) is not None
    if shared:
        assert node_b.resolve_conflicts(node_a.chain)[0] is True
    for _ in range(a_extra):
        assert mine_and_process_block(node_a, Mempool(), pk) is not None
    # A different miner makes B's blocks differ from A's even if mined in the same millisecond.
    for _ in range(b_extra):
        assert mine_and_process_block(node_b, Mempool(), OTHER_MINER) is not None
    return node_a, node_b


class _ReadLog(list):
    """A list that records which single positions were read."""

    def __init__(self, *args):
        super().__init__(*args)
        self.read = []

    def __getitem__(self, key):
        if isinstance(key, int):
            self.read.append(key)
        return super().__getitem__(key)


def test_fork_point_found_without_reading_shared_prefix(genesis_file):
    g_path, sk, pk = genesis_file
    node_a, node_b = _fork(g_path, pk, shared=5, a_extra=1, b_extra=2)

    incoming = _ReadLog(node_b.chain)
    success, _ = node_a.resolve_conflicts(incoming)

    assert success is True
    assert node_a.last_block.hash == node_b.last_block.hash
    # Blocks 0..5 are shared; walking back from the tip stops at block 5.
    assert min(incoming.read) == 5


def test_overlapping_blocks_from_peer_are_not_trusted(genesis_file):
    import copy
    g_path, sk, pk = genesis_file
    node_a, node_b = _fork(g_path, pk, shared=5, a_extra=1, b_extra=2)
    our_block_2 = node_a.chain[2]

    incoming = list(node_b.chain)
    tampered = copy.copy(incoming[2])
    tampered.target = 1  # its hash no longer satisfies this target
    incoming[2] = tampered

    success, _ = node_a.resolve_conflicts(incoming)

    assert success is True
    assert node_a.chain[2] is our_block_2


def test_reorg_resumes_difficulty_from_snapshot(genesis_file, monkeypatch):
    g_path, sk, pk = genesis_file
    node_a, node_b = _fork(g_path, pk, shared=5, a_extra=1, b_extra=2)
    assert node_b.avg_block_time != node_b.target_block_time  # the difficulty EMA has actually moved

    calls = []
    real = node_a._next_target
    monkeypatch.setattr(node_a, "_next_target", lambda t, avg: (calls.append(1), real(t, avg))[1])

    assert node_a.resolve_conflicts(node_b.chain)[0] is True

    assert len(calls) == 3  # once for the fork base, once per new block; nothing from genesis
    assert node_a.current_target == node_b.current_target
    assert node_a.avg_block_time == node_b.avg_block_time


def test_deep_fork_without_snapshot_replays_from_genesis(genesis_file):
    from minichain.node_config import MAX_STATE_SNAPSHOTS
    g_path, sk, pk = genesis_file
    node_a, node_b = _fork(
        g_path, pk, shared=0, a_extra=MAX_STATE_SNAPSHOTS + 1, b_extra=MAX_STATE_SNAPSHOTS + 2
    )
    genesis_hash = node_a.chain[0].hash
    assert all(h != genesis_hash for h, *_ in node_a._state_snapshots)

    assert node_a.resolve_conflicts(node_b.chain)[0] is True

    assert node_a.last_block.hash == node_b.last_block.hash
    assert node_a.current_target == node_b.current_target
    assert node_a.avg_block_time == node_b.avg_block_time
    assert node_a.state.state_root() == node_b.state.state_root()


def test_orphans_come_only_from_dropped_blocks(genesis_file):
    g_path, sk, pk = genesis_file
    node_a = Blockchain(genesis_path=g_path)
    node_b = Blockchain(genesis_path=g_path)

    shared_tx = Transaction(sender=pk, receiver="a" * 64, amount=1, nonce=0, gas_limit=1, fee_per_gas=1)
    shared_tx.sign(sk)
    pool = Mempool()
    pool.add_transaction(shared_tx)
    assert mine_and_process_block(node_a, pool, pk) is not None
    assert node_b.resolve_conflicts(node_a.chain)[0] is True

    dropped_tx = Transaction(sender=pk, receiver="b" * 64, amount=1, nonce=1, gas_limit=1, fee_per_gas=1)
    dropped_tx.sign(sk)
    pool = Mempool()
    pool.add_transaction(dropped_tx)
    assert mine_and_process_block(node_a, pool, pk) is not None
    for _ in range(2):
        assert mine_and_process_block(node_b, Mempool(), OTHER_MINER) is not None

    success, orphans = node_a.resolve_conflicts(node_b.chain)

    assert success is True
    assert [tx.tx_id for tx in orphans] == [dropped_tx.tx_id]
