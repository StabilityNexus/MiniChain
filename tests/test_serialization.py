"""Tests for deterministic serialization."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from minichain.serialization import serialize_block_header, serialize_transaction


def test_transaction_serialization_is_deterministic() -> None:
    tx_a = {
        "sender": "a1" * 20,
        "recipient": "b2" * 20,
        "amount": 25,
        "nonce": 1,
        "fee": 2,
        "timestamp": 1_739_749_000,
    }
    tx_b = {
        "timestamp": 1_739_749_000,
        "fee": 2,
        "nonce": 1,
        "amount": 25,
        "recipient": "b2" * 20,
        "sender": "a1" * 20,
    }

    serialized_a = serialize_transaction(tx_a)
    serialized_b = serialize_transaction(tx_b)

    assert serialized_a == serialized_b
    assert b" " not in serialized_a


def test_changing_transaction_field_changes_serialization() -> None:
    base = {
        "sender": "aa" * 20,
        "recipient": "bb" * 20,
        "amount": 10,
        "nonce": 0,
        "fee": 1,
        "timestamp": 123456,
    }
    mutated = dict(base)
    mutated["amount"] = 11

    assert serialize_transaction(base) != serialize_transaction(mutated)


def test_changing_block_header_field_changes_serialization() -> None:
    base = {
        "version": 0,
        "previous_hash": "00" * 32,
        "merkle_root": "11" * 32,
        "timestamp": 123_456_789,
        "difficulty_target": 1_000_000,
        "nonce": 7,
        "block_height": 3,
    }
    mutated = dict(base)
    mutated["nonce"] = 8

    assert serialize_block_header(base) != serialize_block_header(mutated)


@pytest.mark.parametrize(
    "payload,serializer,expected",
    [
        (
            {
                "sender": "aa" * 20,
                "recipient": "bb" * 20,
                "amount": 1,
                "nonce": 1,
                "timestamp": 1,
            },
            serialize_transaction,
            "Missing required fields: fee",
        ),
        (
            {
                "version": 0,
                "previous_hash": "00" * 32,
                "merkle_root": "11" * 32,
                "timestamp": 1,
                "difficulty_target": 1,
                "nonce": 1,
                "block_height": 1,
                "extra": "x",
            },
            serialize_block_header,
            "Unexpected fields: extra",
        ),
    ],
)
def test_required_and_unexpected_fields_are_rejected(
    payload: dict[str, object], serializer: Callable[[dict[str, object]], bytes], expected: str
) -> None:
    with pytest.raises(ValueError, match=expected):
        serializer(payload)
