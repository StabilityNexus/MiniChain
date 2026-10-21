import random

import pytest

import minichain.state as state_module
from minichain.state import AccountStore, State


def addr(i):
    return f"{i:040x}"


def funded_state(n):
    state = State()
    for i in range(n):
        state.get_account(addr(i))["balance"] = i + 1
    return state


def test_incremental_root_matches_full_rebuild_under_random_changes():
    rng = random.Random(7)
    state = funded_state(40)
    for _ in range(60):
        journal_state = state.copy()
        for _ in range(rng.randint(1, 5)):
            acc = journal_state.get_account(addr(rng.randint(0, 60)))
            kind = rng.choice(["balance", "nonce", "storage", "empty"])
            if kind == "balance":
                acc["balance"] += rng.randint(1, 9)
            elif kind == "nonce":
                acc["nonce"] += 1
            elif kind == "storage":
                acc["storage"][str(rng.randint(0, 3))] = rng.randint(0, 9)
            else:
                acc.update(balance=0, nonce=0, code=None, storage={})
        assert journal_state.state_root() == journal_state._full_state_root()
        if rng.random() < 0.7:
            journal_state.accounts.commit()
            state = State()
            state.accounts = journal_state.accounts.backing
        assert state.state_root() == state._full_state_root()


def test_only_touched_accounts_are_reserialized(monkeypatch):
    state = funded_state(500)
    state.state_root()

    calls = []
    real = state_module._sync_leaf
    monkeypatch.setattr(state_module, "_sync_leaf", lambda trie, address, acc: (calls.append(address), real(trie, address, acc))[1])

    state.get_account(addr(3))["balance"] += 1
    state.state_root()
    assert calls == [addr(3)]

    calls.clear()
    child = state.copy()
    child.get_account(addr(4))["balance"] += 1
    child.get_account(addr(5))["nonce"] += 1
    child.state_root()
    assert sorted(calls) == sorted([addr(4), addr(5)])


def test_journal_root_does_not_change_the_live_state():
    state = funded_state(20)
    live_root = state.state_root()

    child = state.copy()
    child.get_account(addr(1))["balance"] += 100
    assert child.state_root() != live_root

    child.accounts.rollback()
    assert state.state_root() == live_root
    assert state.state_root() == state._full_state_root()


def test_snapshot_and_restore_keep_roots_consistent():
    state = funded_state(10)
    root = state.state_root()
    snapshot = state.snapshot()

    state.get_account(addr(2))["balance"] = 999
    assert state.state_root() != root

    state.restore(snapshot)
    assert isinstance(state.accounts, AccountStore)
    assert state.state_root() == root == state._full_state_root()


def test_trie_is_rebuilt_when_stale_nodes_pile_up(monkeypatch):
    monkeypatch.setattr(AccountStore, "_MAX_NODES_PER_ACCOUNT", 1)
    monkeypatch.setattr(AccountStore, "_NODE_SLACK", 0)
    state = funded_state(8)
    for step in range(30):
        state.get_account(addr(step % 8))["balance"] += 1
        assert state.state_root() == state._full_state_root()


def test_plain_dict_assignment_is_wrapped():
    state = State()
    state.accounts = {addr(1): {"balance": 5, "nonce": 0, "code": None, "storage": {}}}
    assert isinstance(state.accounts, AccountStore)
    assert state.state_root() == state._full_state_root()
