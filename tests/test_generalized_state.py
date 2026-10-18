import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import (
    Transaction,
    Withdrawal,
    Deposit,
    ContractCall,
    Transfer,
    State,
    ALLOW_MINT,
    ALLOW_BURN,
    BURN_ADDRESS,
)
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
def charlie():
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    return sk, pk


@pytest.fixture
def state():
    return State()


def get_bal(state, address, asset=""):
    return state.get_account(address)["balances"].get(asset, 0)


# ---------------------------------------------------------------------------
# 1. Option 2 Transfers Shorthand Execution
# ---------------------------------------------------------------------------

def test_state_apply_transfers_shorthand(alice, bob, state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    state.credit_mining_reward(alice_pk, 1000)

    tx = Transaction(
        transfers=[Transfer(alice_pk, bob_pk, 250)],
        gas_limit=100,
        fee_per_gas=1,
        nonces={alice_pk: 0},
    )
    tx.sign(alice_sk)

    status, receipt = state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.VALID
    assert receipt is not None
    assert receipt.status == 1

    # Alice paid 250 + 100 gas = 350
    assert get_bal(state, alice_pk) == 650
    # Bob received 250
    assert get_bal(state, bob_pk) == 250
    # Alice nonce incremented
    assert state.get_account(alice_pk)["nonce"] == 1


# ---------------------------------------------------------------------------
# 2. Multi-Party Atomic Swaps (Multi-sig + Multi-asset)
# ---------------------------------------------------------------------------

def test_state_multi_party_atomic_swap(alice, bob, state):
    alice_sk, alice_pk = alice
    bob_sk, bob_pk = bob

    token = f"{bob_pk}.SWAP"

    # Alice has 500 native
    state.credit_mining_reward(alice_pk, 500)

    # Bob has 100 token
    state.get_account(bob_pk)["balances"][token] = 100

    assert get_bal(state, alice_pk) == 500
    assert get_bal(state, bob_pk, token) == 100

    # Atomic swap: Alice gives 200 native for Bob's 50 token. Alice pays 10 gas.
    tx = Transaction(
        withdrawals=[
            Withdrawal(alice_pk, {"": 200}),
            Withdrawal(bob_pk, {token: 50}),
        ],
        deposits=[
            Deposit(bob_pk, {"": 200}),
            Deposit(alice_pk, {token: 50}),
        ],
        gas_limit=10,
        fee_per_gas=1,
        nonces={alice_pk: 0, bob_pk: 0},
    )
    tx.sign(alice_sk)
    tx.sign(bob_sk)

    status, receipt = state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.VALID
    assert receipt is not None
    assert receipt.status == 1

    # Alice: 500 - 200 - 10 (gas) = 290 native; 50 token
    assert get_bal(state, alice_pk) == 290
    assert get_bal(state, alice_pk, token) == 50
    assert state.get_account(alice_pk)["nonce"] == 1

    # Bob: 200 native; 100 - 50 = 50 token
    assert get_bal(state, bob_pk) == 200
    assert get_bal(state, bob_pk, token) == 50
    assert state.get_account(bob_pk)["nonce"] == 1


# ---------------------------------------------------------------------------
# 3. Security & Validation Rules
# ---------------------------------------------------------------------------

def test_pool_deficit_conservation_law_rejected(alice, bob, state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    state.credit_mining_reward(alice_pk, 1000)

    # Withdrawing 100 native, but trying to deposit 200 native!
    tx = Transaction(
        withdrawals=[Withdrawal(alice_pk, 100)],
        deposits=[Deposit(bob_pk, 200)],
        gas_limit=10,
        fee_per_gas=1,
        nonces={alice_pk: 0},
    )
    tx.sign(alice_sk)

    status, receipt = state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None
    # Balances untouched
    assert get_bal(state, alice_pk) == 1000
    assert get_bal(state, bob_pk) == 0


def test_insufficient_balance_on_participant_rejected(alice, bob, state):
    alice_sk, alice_pk = alice
    bob_sk, bob_pk = bob

    state.credit_mining_reward(alice_pk, 500)
    # Bob only has 10 native, but tries to withdraw 50
    state.credit_mining_reward(bob_pk, 10)

    tx = Transaction(
        withdrawals=[
            Withdrawal(alice_pk, 100),
            Withdrawal(bob_pk, 50),
        ],
        deposits=[
            Deposit(bob_pk, 100),
            Deposit(alice_pk, 50),
        ],
        nonces={alice_pk: 0, bob_pk: 0},
    )
    tx.sign(alice_sk)
    tx.sign(bob_sk)

    status, receipt = state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None
    # No balance moved
    assert get_bal(state, alice_pk) == 500
    assert get_bal(state, bob_pk) == 10


def test_nonce_mismatch_on_participant_rejected(alice, bob, state):
    alice_sk, alice_pk = alice
    bob_sk, bob_pk = bob

    state.credit_mining_reward(alice_pk, 500)
    state.credit_mining_reward(bob_pk, 500)

    # Bob's account nonce is 0, but tx specifies nonce 99
    tx = Transaction(
        withdrawals=[Withdrawal(alice_pk, 50), Withdrawal(bob_pk, 50)],
        deposits=[Deposit(bob_pk, 50), Deposit(alice_pk, 50)],
        nonces={alice_pk: 0, bob_pk: 99},
    )
    tx.sign(alice_sk)
    tx.sign(bob_sk)

    status, receipt = state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None


def test_generalized_burn_without_flag_fails(alice, state):
    alice_sk, alice_pk = alice
    state.credit_mining_reward(alice_pk, 500)

    # Deposit to BURN_ADDRESS without ALLOW_BURN flag
    tx = Transaction(
        withdrawals=[Withdrawal(alice_pk, 100)],
        deposits=[Deposit(BURN_ADDRESS, 100)],
        flags=0,
        nonces={alice_pk: 0},
    )
    tx.sign(alice_sk)

    status, receipt = state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None


def test_generalized_burn_with_flag_succeeds(alice, state):
    alice_sk, alice_pk = alice
    state.credit_mining_reward(alice_pk, 500)

    # Deposit to BURN_ADDRESS with ALLOW_BURN flag
    tx = Transaction(
        withdrawals=[Withdrawal(alice_pk, 100)],
        deposits=[Deposit(BURN_ADDRESS, 100)],
        flags=ALLOW_BURN,
        nonces={alice_pk: 0},
    )
    tx.sign(alice_sk)

    status, receipt = state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.VALID
    assert receipt.status == 1
    assert get_bal(state, alice_pk) == 400
    assert get_bal(state, BURN_ADDRESS) == 100


def test_generalized_mint_creates_new_supply(alice, bob, state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    state.credit_mining_reward(alice_pk, 500)
    tok = f"{alice_pk}.NEWCOIN"

    # Alice mints 1000 of her token to Bob
    tx = Transaction(
        withdrawals=[Withdrawal(alice_pk, {tok: 1000})],
        deposits=[Deposit(bob_pk, {tok: 1000})],
        flags=ALLOW_MINT,
        nonces={alice_pk: 0},
    )
    tx.sign(alice_sk)

    status, receipt = state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.VALID
    assert receipt.status == 1
    # Bob has 1000 NEWCOIN
    assert state.get_account(bob_pk)["balances"][tok] == 1000
    # Alice native balance unchanged (only gas if charged)
    assert get_bal(state, alice_pk) == 500


# ---------------------------------------------------------------------------
# 4. Generalized Contract Deployment & Calls
# ---------------------------------------------------------------------------

def test_generalized_contract_deploy_and_call(alice, bob, state):
    alice_sk, alice_pk = alice
    bob_sk, bob_pk = bob

    state.credit_mining_reward(alice_pk, 100000)
    state.credit_mining_reward(bob_pk, 100000)

    code = """
if msg['data'] == 'ping':
    storage['pings'] = storage.get('pings', 0) + 1
"""
    # Deploy
    deploy_tx = Transaction(
        calls=[ContractCall(caller=alice_pk, address=None, amount=0, data=code)],
        gas_limit=50000,
        fee_per_gas=1,
        nonces={alice_pk: 0},
    )
    deploy_tx.sign(alice_sk)

    status, receipt = state.validate_and_apply_with_status(deploy_tx)
    assert status == ValidationStatus.VALID
    assert receipt.status == 1
    contract_addr = receipt.contract_address
    assert contract_addr is not None

    # Bob calls contract
    call_tx = Transaction(
        calls=[ContractCall(caller=bob_pk, address=contract_addr, amount=0, data="ping")],
        gas_limit=50000,
        fee_per_gas=1,
        nonces={bob_pk: 0},
    )
    call_tx.sign(bob_sk)

    status, call_receipt = state.validate_and_apply_with_status(call_tx)
    assert status == ValidationStatus.VALID
    assert call_receipt.status == 1

    # Check contract storage
    contract_acc = state.get_account(contract_addr)
    assert contract_acc["storage"]["pings"] == 1
