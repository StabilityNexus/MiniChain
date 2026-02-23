"""Unit tests for SQLite persistence and transactional storage behavior."""

from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from minichain.block import Block, BlockHeader
from minichain.crypto import derive_address, generate_key_pair
from minichain.state import Account, State
from minichain.storage import SQLiteStorage, StorageError
from minichain.transaction import Transaction, create_coinbase_transaction


def _signed_transaction(
    *,
    sender_key: object,
    sender_address: str,
    recipient: str,
    amount: int,
    nonce: int,
    fee: int,
    timestamp: int,
) -> Transaction:
    tx = Transaction(
        sender=sender_address,
        recipient=recipient,
        amount=amount,
        nonce=nonce,
        fee=fee,
        timestamp=timestamp,
    )
    tx.sign(sender_key)
    return tx


def _block_with_transactions(
    *,
    previous_hash: str,
    height: int,
    timestamp: int,
    transactions: list[Transaction],
) -> Block:
    header = BlockHeader(
        version=0,
        previous_hash=previous_hash,
        merkle_root="",
        timestamp=timestamp,
        difficulty_target=(1 << 255),
        nonce=0,
        block_height=height,
    )
    block = Block(header=header, transactions=transactions)
    block.update_header_merkle_root()
    return block


def test_store_and_load_block_round_trip(tmp_path: pytest.TempPathFactory) -> None:
    sender_key, sender_verify = generate_key_pair()
    recipient_key, recipient_verify = generate_key_pair()
    miner_key, miner_verify = generate_key_pair()
    _ = recipient_key
    _ = miner_key

    sender = derive_address(sender_verify)
    recipient = derive_address(recipient_verify)
    miner = derive_address(miner_verify)

    tx = _signed_transaction(
        sender_key=sender_key,
        sender_address=sender,
        recipient=recipient,
        amount=25,
        nonce=0,
        fee=2,
        timestamp=1_739_990_001,
    )
    coinbase = create_coinbase_transaction(
        miner_address=miner,
        amount=52,
        timestamp=1_739_990_001,
    )
    block = _block_with_transactions(
        previous_hash="00" * 32,
        height=1,
        timestamp=1_739_990_001,
        transactions=[coinbase, tx],
    )

    db_path = tmp_path / "chain.db"
    with SQLiteStorage(db_path) as storage:
        storage.store_block(block)
        loaded_by_hash = storage.get_block_by_hash(block.hash().hex())
        loaded_by_height = storage.get_block_by_height(block.header.block_height)

    assert loaded_by_hash is not None
    assert loaded_by_hash.hash() == block.hash()
    assert loaded_by_hash.header.previous_hash == block.header.previous_hash
    assert len(loaded_by_hash.transactions) == 2
    assert loaded_by_hash.transactions[0].is_coinbase()
    assert loaded_by_hash.transactions[1].signature == tx.signature
    assert loaded_by_height is not None
    assert loaded_by_height.hash() == block.hash()


def test_state_and_metadata_persist_across_restart(
    tmp_path: pytest.TempPathFactory,
) -> None:
    db_path = tmp_path / "chain.db"
    state = State()
    state.set_account("11" * 20, Account(balance=100, nonce=2))
    state.set_account("22" * 20, Account(balance=50, nonce=0))
    head_hash = "ab" * 32

    storage = SQLiteStorage(db_path)
    storage.save_state(state)
    storage.save_chain_metadata(height=7, head_hash=head_hash)
    storage.close()

    reopened = SQLiteStorage(db_path)
    loaded_state = reopened.load_state()
    metadata = reopened.load_chain_metadata()
    reopened.close()

    assert loaded_state.get_account("11" * 20).balance == 100
    assert loaded_state.get_account("11" * 20).nonce == 2
    assert loaded_state.get_account("22" * 20).balance == 50
    assert metadata == {"height": 7, "head_hash": head_hash}


def test_atomic_persist_rolls_back_on_metadata_failure(
    tmp_path: pytest.TempPathFactory,
) -> None:
    db_path = tmp_path / "chain.db"
    with SQLiteStorage(db_path) as storage:
        base_state = State()
        base_state.set_account("aa" * 20, Account(balance=10, nonce=0))
        block_1 = _block_with_transactions(
            previous_hash="00" * 32,
            height=1,
            timestamp=1_739_990_100,
            transactions=[
                create_coinbase_transaction(
                    miner_address="bb" * 20,
                    amount=50,
                    timestamp=1_739_990_100,
                )
            ],
        )
        storage.persist_block_state_and_metadata(block=block_1, state=base_state)

        failing_state = State()
        failing_state.set_account("cc" * 20, Account(balance=999, nonce=5))
        block_2 = _block_with_transactions(
            previous_hash=block_1.hash().hex(),
            height=2,
            timestamp=1_739_990_130,
            transactions=[
                create_coinbase_transaction(
                    miner_address="dd" * 20,
                    amount=50,
                    timestamp=1_739_990_130,
                )
            ],
        )

        with pytest.raises(StorageError, match="head_hash"):
            storage.persist_block_state_and_metadata(
                block=block_2,
                state=failing_state,
                head_hash="invalid-hash",
            )

        assert storage.get_block_by_hash(block_2.hash().hex()) is None
        loaded_state = storage.load_state()
        metadata = storage.load_chain_metadata()
        assert loaded_state.get_account("aa" * 20).balance == 10
        assert "cc" * 20 not in loaded_state.accounts
        assert metadata == {"height": 1, "head_hash": block_1.hash().hex()}
