"""
tests/test_state_extra.py

Additional unit coverage for minichain.state: StateJournal edge cases,
validation branches, and execute_internal_call's cross-contract-call
guards that the deploy/call end-to-end tests elsewhere don't reach.
"""

from unittest.mock import patch

import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain.state import State, StateJournal
from minichain.transaction import Transaction
from minichain.validators import ValidationStatus


def _keypair():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


# ------------------------------------------------------------------
# StateJournal
# ------------------------------------------------------------------

def test_journal_delitem_not_supported():
    journal = StateJournal({"a": {"balance": 1}})
    with pytest.raises(NotImplementedError):
        del journal["a"]


def test_journal_update_from_plain_iterable_of_pairs():
    journal = StateJournal({})
    journal.update([("addr1", {"balance": 5}), ("addr2", {"balance": 10})])
    assert journal["addr1"]["balance"] == 5
    assert journal["addr2"]["balance"] == 10


# ------------------------------------------------------------------
# state_root: empty-account pruning
# ------------------------------------------------------------------

def test_state_root_skips_untouched_default_accounts():
    state = State()
    # Materializes a blank account (balance 0, nonce 0, no code, no storage).
    state.get_account("untouched")
    root_with_blank = state.state_root()

    fresh = State()
    root_without_any_accounts = fresh.state_root()

    assert root_with_blank == root_without_any_accounts


# ------------------------------------------------------------------
# verify_transaction_logic / validate_and_apply_with_status
# ------------------------------------------------------------------

def test_verify_transaction_logic_rejects_invalid_signature():
    _, alice_pk = _keypair()
    _, bob_pk = _keypair()
    state = State()
    state.credit_mining_reward(alice_pk, 100)

    tx = Transaction(alice_pk, bob_pk, amount=10, nonce=0)  # never signed
    assert state.verify_transaction_logic(tx) == ValidationStatus.INVALID


def test_malformed_negative_amount_rejected():
    alice_sk, alice_pk = _keypair()
    _, bob_pk = _keypair()
    state = State()
    state.credit_mining_reward(alice_pk, 100)

    tx = Transaction(alice_pk, bob_pk, amount=-5, nonce=0)
    tx.sign(alice_sk)

    status, receipt = state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.MALFORMED
    assert receipt is None


# ------------------------------------------------------------------
# Contract deployment: code-size gas guard
# ------------------------------------------------------------------

def test_deploy_out_of_gas_for_code_size():
    alice_sk, alice_pk = _keypair()
    state = State()
    state.credit_mining_reward(alice_pk, 100)

    tx = Transaction(alice_pk, None, amount=0, nonce=0, gas_limit=1, data="storage['x'] = 1")
    tx.sign(alice_sk)

    receipt = state.apply_transaction(tx)
    assert receipt.status == 0
    assert "Out of gas" in receipt.error_message


# ------------------------------------------------------------------
# execute_internal_call guards
# ------------------------------------------------------------------

def test_internal_call_insufficient_balance_for_non_top_level_call():
    state = State()
    sender = "caller-contract"
    receiver = "callee-contract"
    state.accounts[sender] = {"balance": 5, "nonce": 0, "code": "x", "storage": {}}
    state.accounts[receiver] = {"balance": 0, "nonce": 0, "code": "storage['x'] = 1", "storage": {}}

    result = state.execute_internal_call(
        sender=sender, receiver_address=receiver, amount=100, payload="go",
        gas_limit=1000, depth=1, is_top_level=False,
    )
    assert result["success"] is False
    assert result["error"] == "Insufficient balance"
    # The balances must be untouched -- the guard fires before any transfer.
    assert state.accounts[sender]["balance"] == 5
    assert state.accounts[receiver]["balance"] == 0


def test_internal_call_rolls_back_when_transfers_exceed_receiver_balance():
    """A contract whose declared transfers add up to more than it actually
    holds must be rejected and any provisional balance changes undone."""
    state = State()
    sender = "caller-contract"
    receiver = "callee-contract"
    state.accounts[sender] = {"balance": 100, "nonce": 0, "code": "x", "storage": {}}
    state.accounts[receiver] = {"balance": 0, "nonce": 0, "code": "irrelevant", "storage": {}}

    fake_result = {
        "success": True,
        "gas_used": 10,
        "storage": {},
        "transfers": [{"to": "someone-else", "amount": 9999}],
    }
    with patch.object(state.contract_machine, "execute", return_value=fake_result):
        result = state.execute_internal_call(
            sender=sender, receiver_address=receiver, amount=20, payload="go",
            gas_limit=1000, depth=1, is_top_level=False,
        )

    assert result["success"] is False
    assert "Insufficient contract balance for transfers" in result["error"]
    # Provisional amount transfer (sender -> receiver) must be fully undone.
    assert state.accounts[sender]["balance"] == 100
    assert state.accounts[receiver]["balance"] == 0


# ------------------------------------------------------------------
# Storage helpers
# ------------------------------------------------------------------

def test_update_contract_storage_missing_address_raises():
    state = State()
    with pytest.raises(KeyError):
        state.update_contract_storage("nowhere", {"x": 1})


def test_update_contract_storage_partial_missing_address_raises():
    state = State()
    with pytest.raises(KeyError):
        state.update_contract_storage_partial("nowhere", {"x": 1})


def test_update_contract_storage_partial_merges_dict():
    state = State()
    state.create_contract("addr", code="x")
    state.accounts["addr"]["storage"] = {"a": 1}
    state.update_contract_storage_partial("addr", {"b": 2})
    assert state.accounts["addr"]["storage"] == {"a": 1, "b": 2}


def test_update_contract_storage_partial_rejects_non_dict():
    state = State()
    state.create_contract("addr", code="x")
    with pytest.raises(ValueError):
        state.update_contract_storage_partial("addr", ["not", "a", "dict"])
