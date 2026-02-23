"""Unit tests for account state transitions."""

from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from minichain.block import Block, BlockHeader
from minichain.crypto import derive_address, generate_key_pair
from minichain.state import Account, State, StateTransitionError
from minichain.transaction import Transaction, create_coinbase_transaction


def _signed_transaction(
    sender_key: object,
    sender_address: str,
    recipient: str,
    amount: int,
    nonce: int,
    fee: int = 1,
    timestamp: int = 1_739_900_000,
) -> Transaction:
    tx = Transaction(
        sender=sender_address,
        recipient=recipient,
        amount=amount,
        nonce=nonce,
        fee=fee,
        timestamp=timestamp + nonce,
    )
    tx.sign(sender_key)
    return tx


def _block_with_transactions(
    *,
    miner_address: str,
    transactions: list[Transaction],
    block_reward: int,
) -> Block:
    coinbase = create_coinbase_transaction(
        miner_address=miner_address,
        amount=block_reward + sum(tx.fee for tx in transactions),
        timestamp=1_739_900_100,
    )
    header = BlockHeader(
        version=0,
        previous_hash="00" * 32,
        merkle_root="",
        timestamp=1_739_900_100,
        difficulty_target=1_000_000,
        nonce=0,
        block_height=1,
    )
    block = Block(header=header, transactions=[coinbase, *transactions])
    block.update_header_merkle_root()
    return block


def test_successful_transfer_updates_balances_and_nonce() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender_address = derive_address(sender_verify)
    recipient_address = derive_address(recipient_verify)

    state = State()
    state.set_account(sender_address, Account(balance=100, nonce=0))

    tx = _signed_transaction(
        sender_key, sender_address, recipient_address, amount=25, nonce=0, fee=2
    )
    state.apply_transaction(tx)

    assert state.get_account(sender_address).balance == 73
    assert state.get_account(sender_address).nonce == 1
    assert state.get_account(recipient_address).balance == 25
    assert state.get_account(recipient_address).nonce == 0


def test_insufficient_balance_is_rejected() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender_address = derive_address(sender_verify)
    recipient_address = derive_address(recipient_verify)

    state = State()
    state.set_account(sender_address, Account(balance=5, nonce=0))

    tx = _signed_transaction(
        sender_key, sender_address, recipient_address, amount=10, nonce=0, fee=1
    )

    with pytest.raises(StateTransitionError, match="Insufficient balance"):
        state.apply_transaction(tx)


def test_nonce_mismatch_is_rejected() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender_address = derive_address(sender_verify)
    recipient_address = derive_address(recipient_verify)

    state = State()
    state.set_account(sender_address, Account(balance=100, nonce=1))

    tx = _signed_transaction(
        sender_key, sender_address, recipient_address, amount=10, nonce=0, fee=1
    )

    with pytest.raises(StateTransitionError, match="Nonce mismatch"):
        state.apply_transaction(tx)


def test_transfer_to_new_address_creates_recipient_account() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender_address = derive_address(sender_verify)
    recipient_address = derive_address(recipient_verify)

    state = State()
    state.set_account(sender_address, Account(balance=50, nonce=0))
    assert recipient_address not in state.accounts

    tx = _signed_transaction(
        sender_key, sender_address, recipient_address, amount=10, nonce=0, fee=1
    )
    state.apply_transaction(tx)

    assert recipient_address in state.accounts
    assert state.get_account(recipient_address).balance == 10


def test_apply_block_is_atomic_and_rolls_back_on_failure() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    miner_key, miner_verify = generate_key_pair()
    _ = recipient_key
    _ = miner_key

    sender_address = derive_address(sender_verify)
    recipient_address = derive_address(recipient_verify)
    miner_address = derive_address(miner_verify)

    state = State()
    state.set_account(sender_address, Account(balance=100, nonce=0))

    tx_ok = _signed_transaction(
        sender_key, sender_address, recipient_address, amount=10, nonce=0, fee=1
    )
    tx_fail = _signed_transaction(
        sender_key, sender_address, recipient_address, amount=95, nonce=1, fee=10
    )
    block_reward = 50
    block = _block_with_transactions(
        miner_address=miner_address,
        transactions=[tx_ok, tx_fail],
        block_reward=block_reward,
    )

    with pytest.raises(StateTransitionError, match="Block application failed"):
        state.apply_block(block, block_reward=block_reward)

    assert state.get_account(sender_address).balance == 100
    assert state.get_account(sender_address).nonce == 0
    assert state.get_account(recipient_address).balance == 0
    assert state.get_account(recipient_address).nonce == 0
    assert miner_address not in state.accounts


def test_apply_block_with_valid_coinbase_pays_reward_and_fees() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    miner_key, miner_verify = generate_key_pair()
    _ = recipient_key
    _ = miner_key

    sender_address = derive_address(sender_verify)
    recipient_address = derive_address(recipient_verify)
    miner_address = derive_address(miner_verify)

    state = State()
    state.set_account(sender_address, Account(balance=100, nonce=0))

    tx = _signed_transaction(
        sender_key, sender_address, recipient_address, amount=25, nonce=0, fee=3
    )
    block_reward = 50
    block = _block_with_transactions(
        miner_address=miner_address,
        transactions=[tx],
        block_reward=block_reward,
    )

    state.apply_block(block, block_reward=block_reward)

    assert state.get_account(sender_address).balance == 72
    assert state.get_account(sender_address).nonce == 1
    assert state.get_account(recipient_address).balance == 25
    assert state.get_account(miner_address).balance == 53


def test_block_with_incorrect_coinbase_amount_is_rejected() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    miner_key, miner_verify = generate_key_pair()
    _ = recipient_key
    _ = miner_key

    sender_address = derive_address(sender_verify)
    recipient_address = derive_address(recipient_verify)
    miner_address = derive_address(miner_verify)

    state = State()
    state.set_account(sender_address, Account(balance=100, nonce=0))

    tx = _signed_transaction(
        sender_key, sender_address, recipient_address, amount=10, nonce=0, fee=2
    )
    block_reward = 50
    block = _block_with_transactions(
        miner_address=miner_address,
        transactions=[tx],
        block_reward=block_reward,
    )
    block.transactions[0].amount += 1
    block.update_header_merkle_root()

    with pytest.raises(StateTransitionError, match="Invalid coinbase amount"):
        state.apply_block(block, block_reward=block_reward)


def test_block_without_coinbase_is_rejected() -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    _ = recipient_key

    sender_address = derive_address(sender_verify)
    recipient_address = derive_address(recipient_verify)

    state = State()
    state.set_account(sender_address, Account(balance=100, nonce=0))

    tx = _signed_transaction(
        sender_key, sender_address, recipient_address, amount=10, nonce=0, fee=1
    )
    header = BlockHeader(
        version=0,
        previous_hash="00" * 32,
        merkle_root="",
        timestamp=1_739_900_100,
        difficulty_target=1_000_000,
        nonce=0,
        block_height=1,
    )
    block = Block(header=header, transactions=[tx])
    block.update_header_merkle_root()

    with pytest.raises(StateTransitionError, match="coinbase"):
        state.apply_block(block, block_reward=50)
