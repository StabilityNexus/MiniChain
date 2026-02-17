"""Unit tests for transaction signing and verification."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("nacl")

from minichain.crypto import derive_address, generate_key_pair, serialize_verify_key
from minichain.transaction import Transaction


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
