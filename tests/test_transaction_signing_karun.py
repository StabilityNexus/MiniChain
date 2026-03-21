"""
tests/test_transaction_signing.py

Unit tests for MiniChain transaction signing and verification.

Covers:
  1. Valid transaction — properly signed tx verifies successfully.
  2. Modified transaction data — tampering after signing breaks verification.
  3. Invalid public key — Transaction.sign() raises ValueError at signing time
     when the signing key does not match the sender field; a forged sender
     field set after signing causes verify() to return False.
  4. Replay protection — duplicate nonce is rejected by state validation.
"""

import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Transaction, State


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def alice():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


@pytest.fixture
def bob():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


@pytest.fixture
def funded_state(alice):
    _, alice_pk = alice
    state = State()
    state.credit_mining_reward(alice_pk, 100)
    return state


# ------------------------------------------------------------------
# 1. Valid transaction
# ------------------------------------------------------------------

def test_valid_signature_verifies(alice, bob):
    """A properly signed transaction must pass signature verification."""
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, 10, nonce=0)
    tx.sign(alice_sk)

    assert tx.verify(), "A correctly signed transaction should verify successfully."


# ------------------------------------------------------------------
# 2. Modified transaction data
# ------------------------------------------------------------------

def test_tampered_amount_fails_verification(alice, bob):
    """Changing `amount` after signing must invalidate the signature."""
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, 10, nonce=0)
    tx.sign(alice_sk)
    tx.amount = 9999  # tamper

    assert not tx.verify(), "A transaction with a tampered amount must not verify."


def test_tampered_receiver_fails_verification(alice, bob):
    """Changing `receiver` after signing must invalidate the signature."""
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, 10, nonce=0)
    tx.sign(alice_sk)

    attacker_sk = SigningKey.generate()
    tx.receiver = attacker_sk.verify_key.encode(encoder=HexEncoder).decode()  # tamper

    assert not tx.verify(), "A transaction with a tampered receiver must not verify."


def test_tampered_nonce_fails_verification(alice, bob):
    """Changing `nonce` after signing must invalidate the signature."""
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, 10, nonce=0)
    tx.sign(alice_sk)
    tx.nonce = 99  # tamper

    assert not tx.verify(), "A transaction with a tampered nonce must not verify."


# ------------------------------------------------------------------
# 3. Invalid public key
# ------------------------------------------------------------------

def test_wrong_sender_key_raises(alice, bob):
    """Signing with a key that doesn't match sender must raise ValueError."""
    _, alice_pk = alice
    bob_sk, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, 10, nonce=0)

    with pytest.raises(ValueError, match="Signing key does not match sender"):
        tx.sign(bob_sk)


def test_forged_sender_field_fails_verification(alice, bob):
    """Manually swapping `sender` after signing must fail verification."""
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, 10, nonce=0)
    tx.sign(alice_sk)
    tx.sender = bob_pk  # forge sender

    assert not tx.verify(), "A transaction with a forged sender field must not verify."


def test_unsigned_transaction_fails_verification(alice, bob):
    """A transaction that was never signed must fail verification."""
    _, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, 10, nonce=0)
    # No call to tx.sign()

    assert not tx.verify(), "An unsigned transaction must not verify."



        "Bob's balance should be 20 after receiving two transfers."
