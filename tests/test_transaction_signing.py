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


# ------------------------------------------------------------------
# 4. Replay protection
# ------------------------------------------------------------------

def test_replay_attack_same_nonce_rejected(alice, bob, funded_state):
    """Replaying the same transaction must be rejected the second time."""
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, 10, nonce=0)
    tx.sign(alice_sk)

    assert funded_state.apply_transaction(tx), "First submission must succeed."
    assert not funded_state.apply_transaction(tx), "Replayed transaction must be rejected."
    # Ensure the rejected replay did not mutate the ledger
    assert funded_state.get_account(alice_pk)["balance"] == 90, \
        "Alice's balance must not change after a rejected replay."
    assert funded_state.get_account(alice_pk)["nonce"] == 1, \
        "Alice's nonce must not advance after a rejected replay."


def test_out_of_order_nonce_rejected(alice, bob, funded_state):
    """A transaction with a skipped nonce must be rejected."""
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, 10, nonce=5)
    tx.sign(alice_sk)

    assert not funded_state.apply_transaction(tx), "A transaction with a skipped nonce must be rejected."
    # Ensure the rejected transaction did not mutate the ledger
    assert funded_state.get_account(alice_pk)["balance"] == 100, \
        "Alice's balance must remain unchanged after a rejected transaction."
    assert funded_state.get_account(alice_pk)["nonce"] == 0, \
        "Alice's nonce must remain unchanged after a rejected transaction."


def test_sequential_nonces_accepted(alice, bob, funded_state):
    """Two transactions with consecutive nonces must both succeed."""
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx0 = Transaction(alice_pk, bob_pk, 10, nonce=0)
    tx0.sign(alice_sk)
    assert funded_state.apply_transaction(tx0)

    tx1 = Transaction(alice_pk, bob_pk, 10, nonce=1)
    tx1.sign(alice_sk)
    assert funded_state.apply_transaction(tx1)

    assert funded_state.get_account(alice_pk)["nonce"] == 2, \
        "Alice's nonce should advance to 2 after two accepted transactions."
    assert funded_state.get_account(alice_pk)["balance"] == 80, \
        "Alice's balance should be 80 after two 10-coin transfers."
    assert funded_state.get_account(bob_pk)["balance"] == 20, \
        "Bob's balance should be 20 after receiving two transfers."