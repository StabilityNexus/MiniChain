"""Unit tests for the cryptographic identity module."""

from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from minichain.crypto import (
    derive_address,
    deserialize_signing_key,
    deserialize_verify_key,
    generate_key_pair,
    serialize_signing_key,
    serialize_verify_key,
    sign_message,
    verify_signature,
)


def test_generated_key_pair_can_sign_and_verify() -> None:
    signing_key, verify_key = generate_key_pair()
    message = b"minichain-crypto-test"

    signature = sign_message(message, signing_key)

    assert verify_signature(message, signature, verify_key)


def test_address_derivation_is_deterministic() -> None:
    signing_key, verify_key = generate_key_pair()
    first = derive_address(verify_key)
    second = derive_address(verify_key)

    assert first == second
    assert first == derive_address(signing_key.verify_key)
    assert len(first) == 40


def test_invalid_signature_is_rejected() -> None:
    signing_key, verify_key = generate_key_pair()
    other_signing_key, _ = generate_key_pair()
    message = b"minichain-message"

    wrong_signature = sign_message(message, other_signing_key)

    assert not verify_signature(message, wrong_signature, verify_key)


def test_key_hex_serialization_round_trip() -> None:
    signing_key, verify_key = generate_key_pair()

    signing_key_hex = serialize_signing_key(signing_key)
    verify_key_hex = serialize_verify_key(verify_key)

    decoded_signing_key = deserialize_signing_key(signing_key_hex)
    decoded_verify_key = deserialize_verify_key(verify_key_hex)

    message = b"serialization-round-trip"
    signature = sign_message(message, decoded_signing_key)

    assert verify_signature(message, signature, decoded_verify_key)
    assert derive_address(decoded_verify_key) == derive_address(verify_key)
