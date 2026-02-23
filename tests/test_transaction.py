"""Unit tests for transaction signing and verification."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("nacl")

from minichain.crypto import derive_address, generate_key_pair, serialize_verify_key
from minichain.transaction import COINBASE_SENDER, Transaction, create_coinbase_transaction


def _build_signed_transaction() -> tuple[Transaction, object]:
    signing_key, verify_key = generate_key_pair()
    tx = Transaction(
        sender=derive_address(verify_key),
        recipient="ab" * 20,
        amount=25,
        nonce=0,
        fee=2,
        timestamp=1_739_760_000,
    )
    tx.sign(signing_key)
    return tx, signing_key


def test_valid_transaction_signing_and_verification() -> None:
    tx, _ = _build_signed_transaction()

    assert tx.verify()


def test_tampered_transaction_amount_is_rejected() -> None:
    tx, _ = _build_signed_transaction()
    tampered = replace(tx, amount=tx.amount + 1)

    assert not tampered.verify()


def test_tampered_transaction_recipient_is_rejected() -> None:
    tx, _ = _build_signed_transaction()
    tampered = replace(tx, recipient="cd" * 20)

    assert not tampered.verify()


def test_mismatched_public_key_and_sender_is_rejected() -> None:
    tx, _ = _build_signed_transaction()
    other_signing_key, other_verify_key = generate_key_pair()
    _ = other_signing_key
    tampered = replace(tx, public_key=serialize_verify_key(other_verify_key))

    assert not tampered.verify()


def test_transaction_id_changes_when_signature_changes() -> None:
    tx, _ = _build_signed_transaction()
    original_id = tx.transaction_id()
    tampered = replace(tx, signature="00" * 64)

    assert tampered.transaction_id() != original_id


def test_coinbase_transaction_verifies_without_signature() -> None:
    tx = create_coinbase_transaction(
        miner_address="ef" * 20,
        amount=55,
        timestamp=1_739_760_111,
    )

    assert tx.sender == COINBASE_SENDER
    assert tx.verify()


def test_coinbase_with_auth_fields_is_rejected() -> None:
    tx = create_coinbase_transaction(
        miner_address="ef" * 20,
        amount=55,
        timestamp=1_739_760_111,
    )
    tampered = replace(tx, signature="00" * 64)

    assert not tampered.verify()
