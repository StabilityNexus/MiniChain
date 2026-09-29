"""
tests/test_multi_asset.py

Covers native multi-asset accounts: CreateAsset transactions and
multi-asset Transfer transactions.
"""

import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import Transaction, State
from minichain.validators import ValidationStatus


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
# CreateAsset
# ------------------------------------------------------------------

def test_create_asset_mints_to_recipient_and_updates_registry(alice, bob, funded_state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, amount=1000, nonce=0, ticker="USDT")
    tx.sign(alice_sk)

    receipt = funded_state.apply_transaction(tx)
    assert receipt is not None
    assert receipt.status == 1

    full_name = f"{alice_pk}.USDT"
    assert funded_state.get_account(bob_pk)["balances"][full_name] == 1000
    assert "USDT" in funded_state.get_account(alice_pk)["registry"]
    # Amount minted is not debited from the creator's native balance, only gas is.
    assert funded_state.get_account(alice_pk)["balances"][""] == 100


def test_create_asset_gas_only_no_gas_debits_full_amount(alice, bob, funded_state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, amount=1000, nonce=0, ticker="FOO", gas_limit=5, fee_per_gas=2)
    tx.sign(alice_sk)

    receipt = funded_state.apply_transaction(tx)
    assert receipt.status == 1
    assert funded_state.get_account(alice_pk)["balances"][""] == 100 - 10


def test_create_asset_duplicate_ticker_rejected(alice, bob, funded_state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx1 = Transaction(alice_pk, bob_pk, amount=100, nonce=0, ticker="DUP", gas_limit=1, fee_per_gas=1)
    tx1.sign(alice_sk)
    receipt1 = funded_state.apply_transaction(tx1)
    assert receipt1.status == 1

    tx2 = Transaction(alice_pk, bob_pk, amount=50, nonce=1, ticker="DUP", gas_limit=1, fee_per_gas=1)
    tx2.sign(alice_sk)
    receipt2 = funded_state.apply_transaction(tx2)
    assert receipt2.status == 0
    assert "already exists" in receipt2.error_message
    # Gas for the failed attempt is still charged; nonce still advances.
    assert funded_state.get_account(alice_pk)["nonce"] == 2
    full_name = f"{alice_pk}.DUP"
    assert funded_state.get_account(bob_pk)["balances"][full_name] == 100


def test_create_asset_ticker_with_dot_is_malformed(alice, bob, funded_state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, amount=100, nonce=0, ticker="A.B")
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.MALFORMED
    assert receipt is None


def test_create_asset_requires_receiver_and_no_data(alice, funded_state):
    alice_sk, alice_pk = alice

    tx = Transaction(alice_pk, None, amount=100, nonce=0, ticker="BAD")
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.MALFORMED
    assert receipt is None


# ------------------------------------------------------------------
# Multi-asset Transfer
# ------------------------------------------------------------------

def test_transfer_with_extra_assets_moves_all_atomically(alice, bob, funded_state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    create_tx = Transaction(alice_pk, alice_pk, amount=500, nonce=0, ticker="TOK")
    create_tx.sign(alice_sk)
    assert funded_state.apply_transaction(create_tx).status == 1

    full_name = f"{alice_pk}.TOK"
    transfer_tx = Transaction(alice_pk, bob_pk, amount=10, nonce=1, assets={full_name: 200})
    transfer_tx.sign(alice_sk)
    receipt = funded_state.apply_transaction(transfer_tx)
    assert receipt.status == 1

    assert funded_state.get_account(bob_pk)["balances"][""] == 10
    assert funded_state.get_account(bob_pk)["balances"][full_name] == 200
    assert funded_state.get_account(alice_pk)["balances"][full_name] == 300


def test_transfer_insufficient_extra_asset_balance_rejected(alice, bob, funded_state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, amount=10, nonce=0, assets={f"{alice_pk}.NOPE": 5})
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None
    # Nothing was mutated.
    assert funded_state.get_account(alice_pk)["balances"][""] == 100
    assert funded_state.get_account(alice_pk)["nonce"] == 0


# ------------------------------------------------------------------
# state_root determinism
# ------------------------------------------------------------------

def test_state_root_deterministic_regardless_of_balance_insertion_order(alice, bob):
    _, alice_pk = alice
    _, bob_pk = bob

    s1 = State()
    acc = s1.get_account(alice_pk)
    acc["balances"] = {"": 10, f"{bob_pk}.X": 5}
    acc["registry"] = ["A", "B"]

    s2 = State()
    acc2 = s2.get_account(alice_pk)
    acc2["balances"] = {f"{bob_pk}.X": 5, "": 10}
    acc2["registry"] = ["A", "B"]

    assert s1.state_root() == s2.state_root()
