import pytest
import os
import json
import time

from minichain.chain import Blockchain
from minichain.transaction import Transaction
from minichain.mempool import Mempool

import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
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
        "difficulty": 1,
        "alloc": {pk: {"balance": 1000}},
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
    tx = Transaction(sender=pk, receiver="b" * 64, amount=10, nonce=0, fee=1)
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
    tx1 = Transaction(sender=pk, receiver="a" * 64, amount=10, nonce=0, fee=1)
    tx1.sign(sk)
    pool_a.add_transaction(tx1)
    mine_and_process_block(node_a, pool_a, pk)

    # Node B mines tx2 (nonce 0, competing transaction)
    tx2 = Transaction(sender=pk, receiver="b" * 64, amount=20, nonce=0, fee=1)
    tx2.sign(sk)
    pool_b.add_transaction(tx2)
    mine_and_process_block(node_b, pool_b, pk)

    # Node B mines tx3 (nonce 1) to become the heavier chain
    tx3 = Transaction(sender=pk, receiver="c" * 64, amount=30, nonce=1, fee=1)
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
    tx1 = Transaction(sender=pk, receiver="a" * 64, amount=10, nonce=0, fee=1)
    tx1.sign(sk)
    pool_a.add_transaction(tx1)
    mine_and_process_block(node_a, pool_a, pk)

    # Node B is empty. It tries to reorg Node A with its shorter chain.
    success, orphans = node_a.resolve_conflicts(node_b.chain)

    assert success is False
    assert len(orphans) == 0
    assert node_a.get_total_work() > node_b.get_total_work()
