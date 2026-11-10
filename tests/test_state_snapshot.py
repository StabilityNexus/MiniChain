import copy
from unittest.mock import patch

from minichain.state import State, StateSnapshot


def _account(balance, nonce=0):
    return {"balance": balance, "nonce": nonce, "code": None, "storage": {}}


def test_snapshots_only_copy_changed_accounts():
    state = State()
    for i in range(100):
        state.accounts[f"{i:040x}"] = _account(1)
    state.snapshot()

    state.get_account("0" * 40)["balance"] = 2
    with patch("minichain.state.copy.deepcopy", wraps=copy.deepcopy) as mocked:
        snapshot = state.snapshot()

    assert mocked.call_count == 1
    assert snapshot["0" * 40]["balance"] == 2


def test_delta_snapshots_restore_older_states_independently():
    state = State()
    state.accounts["alice"] = _account(10)
    state.accounts["bob"] = _account(20)
    first = state.snapshot()

    state.get_account("alice")["balance"] = 15
    state.accounts["carol"] = _account(5)
    second = state.snapshot()

    state.get_account("bob")["balance"] = 30
    state.get_account("alice")["balance"] = 25
    third = state.snapshot()

    assert first == {"alice": _account(10), "bob": _account(20)}
    assert second == {
        "alice": _account(15),
        "bob": _account(20),
        "carol": _account(5),
    }
    assert third == {
        "alice": _account(25),
        "bob": _account(30),
        "carol": _account(5),
    }

    second["alice"]["balance"] = 999
    assert first["alice"]["balance"] == 10
    assert third["alice"]["balance"] == 25

    state.restore(first)
    assert state.accounts == first.to_dict()
    assert state.accounts["alice"]["balance"] == 10
    assert "carol" not in state.accounts

    state.restore(third)
    assert state.accounts["alice"]["balance"] == 25
    assert state.accounts["bob"]["balance"] == 30


def test_snapshot_records_account_deletions():
    state = State()
    state.accounts["alice"] = _account(10)
    state.accounts["bob"] = _account(20)
    state.snapshot()

    del state.accounts["bob"]
    snapshot = state.snapshot()

    assert snapshot == {"alice": _account(10)}
    state.restore(snapshot)
    assert "bob" not in state.accounts


def test_unchanged_snapshot_reuses_parent():
    state = State()
    state.accounts["alice"] = _account(10)
    first = state.snapshot()
    second = state.snapshot()

    assert isinstance(first, StateSnapshot)
    assert second is first


def test_snapshot_of_journal_is_self_contained():
    state = State()
    state.accounts["alice"] = _account(10)
    state.snapshot()

    journal_state = state.copy()
    journal_state.get_account("bob")["balance"] = 7
    snapshot = journal_state.snapshot()

    assert snapshot["alice"]["balance"] == 10
    assert snapshot["bob"]["balance"] == 7
    assert "bob" not in state.accounts
