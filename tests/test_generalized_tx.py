import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import (
    Transaction,
    Withdrawal,
    Deposit,
    ContractCall,
    Transfer,
    ALLOW_MINT,
    ALLOW_BURN,
)


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
def charlie():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


# ---------------------------------------------------------------------------
# 1. Primitives: Withdrawal, Deposit, ContractCall, Transfer
# ---------------------------------------------------------------------------

def test_withdrawal_primitive(alice):
    _, alice_pk = alice
    w1 = Withdrawal(alice_pk, 100)
    assert w1.amount == {"": 100}
    assert w1.sender == alice_pk

    w2 = Withdrawal(alice_pk, {"": 100, "tokenA": 50})
    assert w2.amount == {"": 100, "tokenA": 50}

    d = w2.to_dict()
    assert d == {"sender": alice_pk, "amount": {"": 100, "tokenA": 50}}
    w_restored = Withdrawal.from_dict(d)
    assert w_restored == w2


def test_deposit_primitive(bob):
    _, bob_pk = bob
    d1 = Deposit(bob_pk, 50)
    assert d1.amount == {"": 50}
    assert d1.receiver == bob_pk

    d2 = Deposit(bob_pk, {"tokenB": 25})
    d_dict = d2.to_dict()
    assert d_dict == {"receiver": bob_pk, "amount": {"tokenB": 25}}
    d_restored = Deposit.from_dict(d_dict)
    assert d_restored == d2


def test_contract_call_primitive(alice, bob):
    _, alice_pk = alice
    _, bob_pk = bob
    # Contract call
    call = ContractCall(caller=alice_pk, address=bob_pk, amount=10, data="increment()")
    assert call.caller == alice_pk
    assert call.address == bob_pk
    assert call.amount == {"": 10}
    assert call.data == "increment()"

    c_dict = call.to_dict()
    restored = ContractCall.from_dict(c_dict)
    assert restored == call

    deploy = ContractCall(caller=alice_pk, address=None, amount=0, data="print('hello')")
    assert deploy.address is None
    assert deploy.amount == {"": 0}
    deploy_dict = deploy.to_dict()
    restored_deploy = ContractCall.from_dict(deploy_dict)
    assert restored_deploy == deploy


def test_transfer_shorthand_expansion(alice, bob):
    _, alice_pk = alice
    _, bob_pk = bob

    transfer = Transfer(alice_pk, bob_pk, {"": 50, "asset": 20})
    assert transfer.sender == alice_pk
    assert transfer.receiver == bob_pk
    assert transfer.amount == {"": 50, "asset": 20}

    w, d = transfer.to_withdrawal_and_deposit()
    assert isinstance(w, Withdrawal)
    assert w.sender == alice_pk
    assert w.amount == {"": 50, "asset": 20}

    assert isinstance(d, Deposit)
    assert d.receiver == bob_pk
    assert d.amount == {"": 50, "asset": 20}

    # Serialization roundtrip
    t_dict = transfer.to_dict()
    assert t_dict == {"sender": alice_pk, "receiver": bob_pk, "amount": {"": 50, "asset": 20}}
    restored = Transfer.from_dict(t_dict)
    assert restored == transfer


# ---------------------------------------------------------------------------
# 2. Generalized Transaction with transfers Shorthand (Option 2)
# ---------------------------------------------------------------------------

def test_tx_with_transfers_avoids_duplicate_amount_on_wire(alice, bob):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    # Transaction constructed using transfers shorthand
    tx = Transaction(
        transfers=[Transfer(alice_pk, bob_pk, {"": 100, "custom.COIN": 50})],
        nonces={alice_pk: 1},
    )

    # Core engine sees withdrawals and deposits expanded
    assert len(tx.withdrawals) == 1
    assert tx.withdrawals[0] == Withdrawal(alice_pk, {"": 100, "custom.COIN": 50})
    assert len(tx.deposits) == 1
    assert tx.deposits[0] == Deposit(bob_pk, {"": 100, "custom.COIN": 50})

    # Ergonomic legacy properties still match
    assert tx.sender == alice_pk
    assert tx.receiver == bob_pk
    assert tx.amount == {"": 100, "custom.COIN": 50}
    assert tx.nonce == 1

    # On the wire (to_dict), transfers shorthand is preserved: amount written ONCE
    wire_dict = tx.to_dict()
    assert "transfers" in wire_dict
    assert "withdrawals" not in wire_dict
    assert "deposits" not in wire_dict
    assert len(wire_dict["transfers"]) == 1
    assert wire_dict["transfers"][0]["amount"] == {"": 100, "custom.COIN": 50}

    # Sign and verify
    tx.sign(alice_sk)
    assert tx.verify() is True
    assert alice_pk in tx.signatures

    # Deserializing from wire dict expands back into withdrawals and deposits
    restored = Transaction.from_dict(wire_dict)
    assert len(restored.withdrawals) == 1
    assert len(restored.deposits) == 1
    assert restored.withdrawals[0].amount == {"": 100, "custom.COIN": 50}
    assert restored.deposits[0].amount == {"": 100, "custom.COIN": 50}
    assert restored.verify() is True


def test_tx_multiple_transfers(alice, bob, charlie):
    alice_sk, alice_pk = alice
    _, bob_pk = bob
    _, charlie_pk = charlie

    tx = Transaction(
        transfers=[
            Transfer(alice_pk, bob_pk, 100),
            Transfer(alice_pk, charlie_pk, 200),
        ],
        nonces={alice_pk: 5},
    )

    assert len(tx.withdrawals) == 2
    assert len(tx.deposits) == 2
    assert tx.deposits[0].receiver == bob_pk
    assert tx.deposits[1].receiver == charlie_pk

    tx.sign(alice_sk)
    assert tx.verify() is True


# ---------------------------------------------------------------------------
# 3. Generalized Transaction with Explicit Withdrawals & Deposits (Multi-party)
# ---------------------------------------------------------------------------

def test_multi_party_atomic_swap_signatures(alice, bob):
    alice_sk, alice_pk = alice
    bob_sk, bob_pk = bob

    # Alice gives 100 native to Bob; Bob gives 50 TOKEN to Alice
    tx = Transaction(
        withdrawals=[
            Withdrawal(alice_pk, {"": 100}),
            Withdrawal(bob_pk, {f"{bob_pk}.TOKEN": 50}),
        ],
        deposits=[
            Deposit(bob_pk, {"": 100}),
            Deposit(alice_pk, {f"{bob_pk}.TOKEN": 50}),
        ],
        nonces={alice_pk: 3, bob_pk: 7},
    )

    assert tx.required_signers == {alice_pk, bob_pk}

    # Before signing, verification fails
    assert tx.verify() is False

    # Alice signs
    tx.sign(alice_sk)
    # Bob has not signed yet, so verification still fails
    assert tx.verify() is False

    # Bob signs
    tx.sign(bob_sk)
    # Both signed, verification succeeds!
    assert tx.verify() is True
    assert len(tx.signatures) == 2

    # Wire dict preserves explicit withdrawals & deposits
    d = tx.to_dict()
    assert "withdrawals" in d
    assert "deposits" in d
    assert "transfers" not in d
    assert len(d["withdrawals"]) == 2
    assert len(d["deposits"]) == 2
    assert len(d["signatures"]) == 2

    # Roundtrip from dict
    restored = Transaction.from_dict(d)
    assert restored.verify() is True
    assert restored.required_signers == {alice_pk, bob_pk}


def test_tampering_invalidates_generalized_signature(alice, bob):
    alice_sk, alice_pk = alice
    bob_sk, bob_pk = bob

    tx = Transaction(
        withdrawals=[Withdrawal(alice_pk, 100)],
        deposits=[Deposit(bob_pk, 100)],
        nonces={alice_pk: 0},
    )
    tx.sign(alice_sk)
    assert tx.verify() is True

    # Tamper with withdrawal amount
    object.__setattr__(tx, "withdrawals", [Withdrawal(alice_pk, 9999)])
    assert tx.verify() is False


def test_signing_with_unrelated_key_raises_error(alice, bob, charlie):
    _, alice_pk = alice
    _, bob_pk = bob
    charlie_sk, _ = charlie

    tx = Transaction(
        withdrawals=[Withdrawal(alice_pk, 100)],
        deposits=[Deposit(bob_pk, 100)],
        nonces={alice_pk: 0},
    )
    # Charlie is not a sender or caller in this transaction
    with pytest.raises(ValueError, match="Signing key does not match"):
        tx.sign(charlie_sk)


# ---------------------------------------------------------------------------
# 4. Backward Compatibility with Existing Legacy Transactions
# ---------------------------------------------------------------------------

def test_legacy_tx_automatically_derives_generalized_structures(alice, bob):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, 50, nonce=2)
    assert tx._is_legacy is True
    assert len(tx.withdrawals) == 1
    assert tx.withdrawals[0] == Withdrawal(alice_pk, {"": 50})
    assert len(tx.deposits) == 1
    assert tx.deposits[0] == Deposit(bob_pk, {"": 50})
    assert len(tx.calls) == 0

    tx.sign(alice_sk)
    assert tx.verify() is True

    # Legacy to_dict contains 'sender', 'receiver', 'amount', 'signature'
    d = tx.to_dict()
    assert "sender" in d
    assert "receiver" in d
    assert "amount" in d
    assert "signature" in d

    # from_dict recreates legacy tx faithfully
    restored = Transaction.from_dict(d)
    assert restored.sender == alice_pk
    assert restored.receiver == bob_pk
    assert restored.amount == {"": 50}
    assert restored.verify() is True
