"""Unit tests for mempool transaction queuing and selection behavior."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("nacl")

from minichain.crypto import derive_address, generate_key_pair
from minichain.mempool import Mempool, MempoolValidationError
from minichain.state import Account, State
from minichain.transaction import Transaction


def _signed_transaction(
    *,
    sender_key: object,
    sender_address: str,
    recipient: str,
    amount: int,
    nonce: int,
    fee: int,
    timestamp: int = 1_739_950_000,
) -> Transaction:
    transaction = Transaction(
        sender=sender_address,
        recipient=recipient,
        amount=amount,
        nonce=nonce,
        fee=fee,
        timestamp=timestamp + nonce,
    )
    transaction.sign(sender_key)
    return transaction


def test_deduplicates_transactions_by_id() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)
    state = State()
    state.set_account(sender, Account(balance=100, nonce=0))
    mempool = Mempool()

    tx = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=1,
    )

    mempool.add_transaction(tx, state)
    with pytest.raises(MempoolValidationError, match="Duplicate transaction"):
        mempool.add_transaction(tx, state)


def test_fee_priority_respects_sender_nonce_ordering() -> None:
    a_key, a_verify = generate_key_pair()
    b_key, b_verify = generate_key_pair()
    c_key, c_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender_a = derive_address(a_verify)
    sender_b = derive_address(b_verify)
    sender_c = derive_address(c_verify)
    recipient = derive_address(recipient_verify)

    state = State()
    state.set_account(sender_a, Account(balance=100, nonce=0))
    state.set_account(sender_b, Account(balance=100, nonce=0))
    state.set_account(sender_c, Account(balance=100, nonce=0))
    mempool = Mempool()

    tx_a0 = _signed_transaction(
        sender_key=a_key,
        sender_address=sender_a,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=1,
    )
    tx_a1 = _signed_transaction(
        sender_key=a_key,
        sender_address=sender_a,
        recipient=recipient,
        amount=5,
        nonce=1,
        fee=10,
    )
    tx_b0 = _signed_transaction(
        sender_key=b_key,
        sender_address=sender_b,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=8,
    )
    tx_c0 = _signed_transaction(
        sender_key=c_key,
        sender_address=sender_c,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=4,
    )

    mempool.add_transaction(tx_a1, state)
    mempool.add_transaction(tx_b0, state)
    mempool.add_transaction(tx_a0, state)
    mempool.add_transaction(tx_c0, state)

    selected = mempool.get_transactions_for_mining(state, limit=4)

    assert [tx.fee for tx in selected] == [8, 4, 1, 10]
    assert selected[2].sender == sender_a and selected[2].nonce == 0
    assert selected[3].sender == sender_a and selected[3].nonce == 1


def test_evicts_low_fee_when_pool_exceeds_max_size() -> None:
    s1_key, s1_verify = generate_key_pair()
    s2_key, s2_verify = generate_key_pair()
    s3_key, s3_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender1 = derive_address(s1_verify)
    sender2 = derive_address(s2_verify)
    sender3 = derive_address(s3_verify)
    recipient = derive_address(recipient_verify)

    state = State()
    state.set_account(sender1, Account(balance=100, nonce=0))
    state.set_account(sender2, Account(balance=100, nonce=0))
    state.set_account(sender3, Account(balance=100, nonce=0))
    mempool = Mempool(max_size=2, max_age_seconds=10_000)

    tx1 = _signed_transaction(
        sender_key=s1_key,
        sender_address=sender1,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=1,
    )
    tx2 = _signed_transaction(
        sender_key=s2_key,
        sender_address=sender2,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=6,
    )
    tx3 = _signed_transaction(
        sender_key=s3_key,
        sender_address=sender3,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=3,
    )

    id1 = mempool.add_transaction(tx1, state, received_at=1)
    id2 = mempool.add_transaction(tx2, state, received_at=2)
    id3 = mempool.add_transaction(tx3, state, received_at=3)

    assert mempool.size() == 2
    assert not mempool.contains(id1)
    assert mempool.contains(id2)
    assert mempool.contains(id3)


def test_nonce_gap_is_held_then_promoted_when_gap_filled() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)

    state = State()
    state.set_account(sender, Account(balance=100, nonce=0))
    mempool = Mempool()

    tx_nonce_1 = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=5,
        nonce=1,
        fee=5,
    )
    tx_nonce_0 = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=1,
    )

    mempool.add_transaction(tx_nonce_1, state)
    assert mempool.ready_count() == 0
    assert mempool.waiting_count() == 1

    mempool.add_transaction(tx_nonce_0, state)
    assert mempool.ready_count() == 2
    assert mempool.waiting_count() == 0

    selected = mempool.get_transactions_for_mining(state, limit=2)
    assert [tx.nonce for tx in selected] == [0, 1]


def test_confirmed_transaction_removal_revalidates_pending_set() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)
    state = State()
    state.set_account(sender, Account(balance=100, nonce=0))
    mempool = Mempool()

    tx0 = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=10,
        nonce=0,
        fee=2,
    )
    tx1 = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=10,
        nonce=1,
        fee=1,
    )

    mempool.add_transaction(tx0, state)
    mempool.add_transaction(tx1, state)
    assert mempool.size() == 2
    assert mempool.ready_count() == 2

    state.apply_transaction(tx0)
    mempool.remove_confirmed_transactions([tx0], state)

    assert mempool.size() == 1
    assert mempool.ready_count() == 1
    selected = mempool.get_transactions_for_mining(state, limit=1)
    assert selected[0].nonce == 1


def test_rejects_duplicate_sender_nonce_even_if_tx_id_differs() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)
    state = State()
    state.set_account(sender, Account(balance=100, nonce=0))
    mempool = Mempool()

    tx = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=1,
    )
    tx_modified = replace(tx, amount=6)
    tx_modified.sign(sender_key)

    mempool.add_transaction(tx, state)
    with pytest.raises(MempoolValidationError, match="Duplicate sender nonce"):
        mempool.add_transaction(tx_modified, state)


def test_evicts_stale_transactions_by_age() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)
    state = State()
    state.set_account(sender, Account(balance=100, nonce=0))
    mempool = Mempool(max_size=10, max_age_seconds=10)

    tx = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=5,
        nonce=0,
        fee=1,
    )
    tx_id = mempool.add_transaction(tx, state, received_at=100)

    evicted = mempool.evict(state, current_time=111)
    assert tx_id in evicted
    assert mempool.size() == 0
