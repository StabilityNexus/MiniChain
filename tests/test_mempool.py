"""
tests/test_mempool.py

Unit tests for minichain.mempool.Mempool.

Covers:
  1. Signature validation on entry.
  2. Duplicate / stale / underpriced replace-by-fee (RBF) rejection.
  3. Successful RBF replacement.
  4. Mempool size cap.
  5. Fee-priority ordering and block-slice retrieval.
  6. Removal after inclusion in a block.
"""

import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain.mempool import Mempool
from minichain.transaction import Transaction


def _keypair():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


def _signed_tx(sk, sender_pk, receiver_pk, nonce=0, fee_per_gas=0, timestamp=None):
    tx = Transaction(sender_pk, receiver_pk, amount=1, nonce=nonce,
                      fee_per_gas=fee_per_gas, timestamp=timestamp)
    tx.sign(sk)
    return tx


@pytest.fixture
def alice():
    return _keypair()


@pytest.fixture
def bob():
    return _keypair()


@pytest.fixture
def carol():
    return _keypair()


# ------------------------------------------------------------------
# 1. Signature validation
# ------------------------------------------------------------------

def test_unsigned_transaction_rejected(alice, bob):
    _, alice_pk = alice
    _, bob_pk = bob
    mempool = Mempool()

    tx = Transaction(alice_pk, bob_pk, amount=1, nonce=0)  # never signed

    assert mempool.add_transaction(tx) is False
    assert len(mempool) == 0


# ------------------------------------------------------------------
# 2. Duplicate / stale / underpriced replacement rejection
# ------------------------------------------------------------------

def test_duplicate_transaction_rejected(alice, bob):
    alice_sk, alice_pk = alice
    _, bob_pk = bob
    mempool = Mempool()

    tx = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=10)

    assert mempool.add_transaction(tx) is True
    assert mempool.add_transaction(tx) is False
    assert len(mempool) == 1


def test_replacement_with_older_timestamp_rejected(alice, bob):
    alice_sk, alice_pk = alice
    _, bob_pk = bob
    mempool = Mempool()

    tx1 = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=10, timestamp=2_000_000_000_000)
    tx2 = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=100, timestamp=1_000_000_000_000)

    assert mempool.add_transaction(tx1) is True
    assert mempool.add_transaction(tx2) is False, "An older-timestamped replacement must be rejected even with a higher fee."
    assert len(mempool) == 1


def test_replacement_fee_too_low_rejected(alice, bob):
    alice_sk, alice_pk = alice
    _, bob_pk = bob
    mempool = Mempool()

    tx1 = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=10, timestamp=1_000_000_000_000)
    # Only a 5% bump; RBF requires >10%.
    tx2 = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=10.5, timestamp=2_000_000_000_000)

    assert mempool.add_transaction(tx1) is True
    assert mempool.add_transaction(tx2) is False
    assert len(mempool) == 1


# ------------------------------------------------------------------
# 3. Successful replace-by-fee
# ------------------------------------------------------------------

def test_replacement_by_fee_succeeds(alice, bob):
    alice_sk, alice_pk = alice
    _, bob_pk = bob
    mempool = Mempool()

    tx1 = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=10, timestamp=1_000_000_000_000)
    tx2 = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=20, timestamp=2_000_000_000_000)

    assert mempool.add_transaction(tx1) is True
    assert mempool.add_transaction(tx2) is True
    assert len(mempool) == 1

    remaining = mempool.get_transactions_for_block()
    assert remaining[0].tx_id == tx2.tx_id


# ------------------------------------------------------------------
# 4. Mempool size cap
# ------------------------------------------------------------------

def test_full_mempool_rejects_new_sender(alice, bob, carol):
    alice_sk, alice_pk = alice
    _, bob_pk = bob
    carol_sk, carol_pk = carol
    mempool = Mempool(max_size=1)

    tx1 = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=10)
    tx2 = _signed_tx(carol_sk, carol_pk, bob_pk, nonce=0, fee_per_gas=999)

    assert mempool.add_transaction(tx1) is True
    assert mempool.add_transaction(tx2) is False, "A full mempool must reject a transaction from a new sender."
    assert len(mempool) == 1


# ------------------------------------------------------------------
# 5. Fee-priority ordering and block-slice retrieval
# ------------------------------------------------------------------

def test_transactions_ordered_by_fee_descending(alice, bob, carol):
    alice_sk, alice_pk = alice
    bob_sk, bob_pk = bob
    carol_sk, carol_pk = carol
    mempool = Mempool()

    tx_low = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=5)
    tx_high = _signed_tx(bob_sk, bob_pk, alice_pk, nonce=0, fee_per_gas=20)
    tx_mid = _signed_tx(carol_sk, carol_pk, alice_pk, nonce=0, fee_per_gas=10)

    for tx in (tx_low, tx_high, tx_mid):
        assert mempool.add_transaction(tx) is True

    ordered = mempool.get_transactions_for_block()
    assert [tx.fee_per_gas for tx in ordered] == [20, 10, 5]


def test_get_transactions_for_block_respects_limit(alice, bob, carol):
    alice_sk, alice_pk = alice
    bob_sk, bob_pk = bob
    carol_sk, carol_pk = carol
    mempool = Mempool(transactions_per_block=2)

    tx_low = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=5)
    tx_high = _signed_tx(bob_sk, bob_pk, alice_pk, nonce=0, fee_per_gas=20)
    tx_mid = _signed_tx(carol_sk, carol_pk, alice_pk, nonce=0, fee_per_gas=10)

    for tx in (tx_low, tx_high, tx_mid):
        mempool.add_transaction(tx)

    block_txs = mempool.get_transactions_for_block()
    assert len(block_txs) == 2
    assert [tx.fee_per_gas for tx in block_txs] == [20, 10]
    # The underlying pool itself is untouched by taking a block slice.
    assert len(mempool) == 3


# ------------------------------------------------------------------
# 6. Removal after inclusion in a block
# ------------------------------------------------------------------

def test_remove_transactions(alice, bob, carol):
    alice_sk, alice_pk = alice
    bob_sk, bob_pk = bob
    carol_sk, carol_pk = carol
    mempool = Mempool()

    tx1 = _signed_tx(alice_sk, alice_pk, bob_pk, nonce=0, fee_per_gas=5)
    tx2 = _signed_tx(bob_sk, bob_pk, alice_pk, nonce=0, fee_per_gas=20)
    mempool.add_transaction(tx1)
    mempool.add_transaction(tx2)

    mempool.remove_transactions([tx2])

    assert len(mempool) == 1
    remaining = mempool.get_transactions_for_block()
    assert remaining[0].tx_id == tx1.tx_id
