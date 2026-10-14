"""
tests/test_multi_asset.py

Covers uniform multi-asset architecture:
- Minting custom tokens using flags=ALLOW_MINT (Flag 1)
- Burning tokens using flags=ALLOW_BURN (Flag 2)
- Multi-asset atomic transfers with dictionary amounts
- Namespace isolation preventing unauthorized minting
- Contract reception and transfer_out of multi-asset dictionaries
- Composite flags (Flag 3: ALLOW_MINT | ALLOW_BURN)
- State root determinism across insertion order
"""

import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import (
    Transaction,
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
def funded_state(alice):
    _, alice_pk = alice
    state = State()
    state.credit_mining_reward(alice_pk, 100)
    return state


# ------------------------------------------------------------------
# Migrated Legacy Tests (ticker / assets -> amount dict & flags)
# ------------------------------------------------------------------

def test_create_asset_mints_to_recipient_and_updates_registry(alice, bob, funded_state):
    """
    Migrated from legacy create_asset test:
    Alice mints custom tokens with flags=ALLOW_MINT and amount={f"{alice_pk}.USDT": 1000}.
    Recipient is credited, sender is not debited for custom asset (only gas).
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(
        alice_pk,
        bob_pk,
        amount={f"{alice_pk}.USDT": 1000},
        nonce=0,
        flags=ALLOW_MINT,
    )
    tx.sign(alice_sk)

    receipt = funded_state.apply_transaction(tx)
    assert receipt is not None
    assert receipt.status == 1

    full_name = f"{alice_pk}.USDT"
    assert funded_state.get_account(bob_pk)["balances"][full_name] == 1000
    # Amount minted is not debited from the creator's native balance (gas was 0).
    assert funded_state.get_account(alice_pk)["balances"][""] == 100
    assert funded_state.get_account(alice_pk)["balances"].get(full_name, 0) == 0


def test_create_asset_gas_only_no_gas_debits_full_amount(alice, bob, funded_state):
    """
    Migrated from legacy test:
    Minting with gas debit from native currency "" while custom asset is created
    without sender debit.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(
        alice_pk,
        bob_pk,
        amount={f"{alice_pk}.FOO": 1000},
        nonce=0,
        flags=ALLOW_MINT,
        gas_limit=5,
        fee_per_gas=2,
    )
    tx.sign(alice_sk)

    receipt = funded_state.apply_transaction(tx)
    assert receipt is not None
    assert receipt.status == 1

    # Native gas is charged: 5 * 2 = 10 from initial 100
    assert funded_state.get_account(alice_pk)["balances"][""] == 100 - 10
    full_name = f"{alice_pk}.FOO"
    assert funded_state.get_account(bob_pk)["balances"][full_name] == 1000
    assert funded_state.get_account(alice_pk)["balances"].get(full_name, 0) == 0


def test_transfer_with_extra_assets_moves_all_atomically(alice, bob, funded_state):
    """
    Migrated from legacy test:
    Sender mints custom token, then sends multi-asset dictionary amount={"": 10, f"{alice_pk}.TOK": 200}
    with flags=0 to recipient. Verifies balances of both native and custom tokens for both accounts.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    full_name = f"{alice_pk}.TOK"
    create_tx = Transaction(alice_pk, alice_pk, amount={full_name: 500}, nonce=0, flags=ALLOW_MINT)
    create_tx.sign(alice_sk)
    receipt1 = funded_state.apply_transaction(create_tx)
    assert receipt1 is not None
    assert receipt1.status == 1

    transfer_tx = Transaction(alice_pk, bob_pk, amount={"": 10, full_name: 200}, nonce=1, flags=0)
    transfer_tx.sign(alice_sk)
    receipt2 = funded_state.apply_transaction(transfer_tx)
    assert receipt2 is not None
    assert receipt2.status == 1

    assert funded_state.get_account(bob_pk)["balances"][""] == 10
    assert funded_state.get_account(bob_pk)["balances"][full_name] == 200
    assert funded_state.get_account(alice_pk)["balances"][full_name] == 300
    assert funded_state.get_account(alice_pk)["balances"][""] == 90


def test_transfer_insufficient_extra_asset_balance_rejected(alice, bob, funded_state):
    """
    Migrated from legacy test:
    Transfer fails when sender has insufficient balance of custom token.
    State is preserved.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(alice_pk, bob_pk, amount={"": 10, f"{alice_pk}.NOPE": 5}, nonce=0, flags=0)
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None
    # Nothing was mutated
    assert funded_state.get_account(alice_pk)["balances"][""] == 100
    assert funded_state.get_account(alice_pk)["nonce"] == 0


def test_state_root_deterministic_regardless_of_balance_insertion_order(alice, bob):
    """
    Deterministic state root calculation regardless of balance dictionary insertion order.
    """
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


# ------------------------------------------------------------------
# AC2: Minting Tests
# ------------------------------------------------------------------

def test_minting_with_allow_mint_succeeds(alice, bob, funded_state):
    """
    AC2: Sender mints <sender>.TOKEN with flags=ALLOW_MINT.
    Verify sender is not debited, recipient is credited, and native gas is debited.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    token_asset = f"{alice_pk}.TOKEN"
    tx = Transaction(
        alice_pk,
        bob_pk,
        amount={token_asset: 2500},
        nonce=0,
        flags=ALLOW_MINT,
        gas_limit=10,
        fee_per_gas=1,
    )
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.VALID
    assert receipt is not None
    assert receipt.status == 1

    # Verify sender was not debited custom token
    assert funded_state.get_account(alice_pk)["balances"].get(token_asset, 0) == 0
    # Verify recipient is credited
    assert funded_state.get_account(bob_pk)["balances"][token_asset] == 2500
    # Verify native gas is debited
    assert funded_state.get_account(alice_pk)["balances"][""] == 100 - 10
    assert funded_state.get_account(alice_pk)["nonce"] == 1


def test_minting_with_flags_zero_fails_without_balance(alice, bob, funded_state):
    """
    AC2: Sender attempts to transfer <sender>.TOKEN with flags=0 without having
    an existing balance. Verify transaction fails with ValidationStatus.FAILED.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    token_asset = f"{alice_pk}.TOKEN"
    tx = Transaction(
        alice_pk,
        bob_pk,
        amount={token_asset: 500},
        nonce=0,
        flags=0,
    )
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None

    # State is untouched
    assert funded_state.get_account(alice_pk)["balances"][""] == 100
    assert funded_state.get_account(alice_pk)["nonce"] == 0
    assert token_asset not in funded_state.get_account(bob_pk)["balances"]


# ------------------------------------------------------------------
# AC3: Burning Tests
# ------------------------------------------------------------------

def test_burning_with_allow_burn_succeeds(alice, funded_state):
    """
    AC3: Sender transfers tokens to BURN_ADDRESS with flags=ALLOW_BURN.
    Verify sender balance is debited and transaction succeeds.
    """
    alice_sk, alice_pk = alice

    # First mint some tokens to Alice
    token_asset = f"{alice_pk}.BURNME"
    mint_tx = Transaction(
        alice_pk,
        alice_pk,
        amount={token_asset: 500},
        nonce=0,
        flags=ALLOW_MINT,
    )
    mint_tx.sign(alice_sk)
    receipt_mint = funded_state.apply_transaction(mint_tx)
    assert receipt_mint is not None
    assert receipt_mint.status == 1

    # Burn native and custom tokens to BURN_ADDRESS with ALLOW_BURN
    burn_tx = Transaction(
        alice_pk,
        BURN_ADDRESS,
        amount={"": 20, token_asset: 300},
        nonce=1,
        flags=ALLOW_BURN,
        gas_limit=5,
        fee_per_gas=1,
    )
    burn_tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(burn_tx)
    assert status == ValidationStatus.VALID
    assert receipt is not None
    assert receipt.status == 1

    # Sender balance is debited
    # Native: 100 - 20 (sent to burn) - 5 (gas) = 75
    # Custom: 500 - 300 = 200
    assert funded_state.get_account(alice_pk)["balances"][""] == 75
    assert funded_state.get_account(alice_pk)["balances"][token_asset] == 200
    assert funded_state.get_account(alice_pk)["nonce"] == 2

    # Verify BURN_ADDRESS received the tokens
    burn_acc = funded_state.get_account(BURN_ADDRESS)
    assert burn_acc["balances"][""] == 20
    assert burn_acc["balances"][token_asset] == 300


def test_burning_with_flags_zero_fails(alice, funded_state):
    """
    AC3: Sender transfers tokens to BURN_ADDRESS with flags=0.
    Verify transaction fails with ValidationStatus.FAILED and state is untouched.
    """
    alice_sk, alice_pk = alice

    tx = Transaction(
        alice_pk,
        BURN_ADDRESS,
        amount={"": 10},
        nonce=0,
        flags=0,
    )
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None

    # State is untouched
    assert funded_state.get_account(alice_pk)["balances"][""] == 100
    assert funded_state.get_account(alice_pk)["nonce"] == 0
    assert funded_state.get_account(BURN_ADDRESS)["balances"].get("", 0) == 0


def test_burn_without_sufficient_balance_fails_even_with_allow_burn(alice, funded_state):
    """
    AC3 Edge Case: Sender attempts to burn more tokens than they possess.
    Even with ALLOW_BURN flag set, transaction must fail.
    """
    alice_sk, alice_pk = alice

    tx = Transaction(
        alice_pk,
        BURN_ADDRESS,
        amount={"": 500},  # Alice only has 100
        nonce=0,
        flags=ALLOW_BURN,
    )
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None
    assert funded_state.get_account(alice_pk)["balances"][""] == 100


# ------------------------------------------------------------------
# AC4: Unauthorized Minting Tests
# ------------------------------------------------------------------

def test_unauthorized_minting_prevented(alice, bob, funded_state):
    """
    AC4: Alice attempts to mint <bob_pk>.TOKEN with flags=ALLOW_MINT.
    Verify transaction fails with ValidationStatus.FAILED because Alice does not
    own Bob's namespace, and state is preserved.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    unauthorized_asset = f"{bob_pk}.TOKEN"
    tx = Transaction(
        alice_pk,
        alice_pk,
        amount={unauthorized_asset: 1000},
        nonce=0,
        flags=ALLOW_MINT,
    )
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None

    # State is preserved
    assert funded_state.get_account(alice_pk)["balances"][""] == 100
    assert funded_state.get_account(alice_pk)["nonce"] == 0
    assert unauthorized_asset not in funded_state.get_account(alice_pk)["balances"]


def test_partial_unauthorized_mint_fails_atomically(alice, bob, funded_state):
    """
    AC4 Edge Case: Alice attempts to mint one legitimate token under her own prefix
    and one unauthorized token under Bob's prefix in the same transaction.
    The entire transaction must fail atomically, without minting anything.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    legit_asset = f"{alice_pk}.MYTOKEN"
    unauth_asset = f"{bob_pk}.STOLEN"

    tx = Transaction(
        alice_pk,
        alice_pk,
        amount={legit_asset: 500, unauth_asset: 500},
        nonce=0,
        flags=ALLOW_MINT,
    )
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None

    # State preserved atomically
    assert legit_asset not in funded_state.get_account(alice_pk)["balances"]
    assert unauth_asset not in funded_state.get_account(alice_pk)["balances"]
    assert funded_state.get_account(alice_pk)["nonce"] == 0


# ------------------------------------------------------------------
# AC5: Contract Multi-Asset Support Tests
# ------------------------------------------------------------------

def test_contract_receives_and_sends_multi_asset_dict(alice, bob, funded_state):
    """
    AC5: Deploy a contract with initial multi-asset balance and call contract
    sending multi-asset dictionary {"": 10, f"{alice_pk}.TOKEN": 50}.
    Contract executes and issues transfer_out with dictionary amount.
    Verify both contract and recipient balances are accurately updated.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    custom_token = f"{alice_pk}.TOKEN"

    # Mint initial custom tokens to Alice
    mint_tx = Transaction(
        alice_pk,
        alice_pk,
        amount={custom_token: 500},
        nonce=0,
        flags=ALLOW_MINT,
    )
    mint_tx.sign(alice_sk)
    assert funded_state.apply_transaction(mint_tx).status == 1
    assert funded_state.get_account(alice_pk)["balances"][custom_token] == 500

    # Contract code that forwards out_amt to target
    code = """
target = msg['data']['target']
out_amt = msg['data']['out_amt']
transfer_out(target, out_amt)
"""

    # Deploy contract with initial multi-asset balance: {"": 20, custom_token: 100}
    deploy_tx = Transaction(
        alice_pk,
        None,
        amount={"": 20, custom_token: 100},
        nonce=1,
        data=code,
        gas_limit=50000,
        fee_per_gas=0,
    )
    deploy_tx.sign(alice_sk)
    deploy_receipt = funded_state.apply_transaction(deploy_tx)
    assert deploy_receipt is not None
    assert deploy_receipt.status == 1
    contract_addr = deploy_receipt.contract_address

    contract_acc = funded_state.get_account(contract_addr)
    assert contract_acc["balances"][""] == 20
    assert contract_acc["balances"][custom_token] == 100

    # Call contract sending multi-asset dict {"": 10, custom_token: 50}
    # Contract transfers {"": 15, custom_token: 40} to Bob
    call_tx = Transaction(
        alice_pk,
        contract_addr,
        amount={"": 10, custom_token: 50},
        nonce=2,
        data={
            "target": bob_pk,
            "out_amt": {"": 15, custom_token: 40},
        },
        gas_limit=50000,
        fee_per_gas=0,
    )
    call_tx.sign(alice_sk)
    call_receipt = funded_state.apply_transaction(call_tx)
    assert call_receipt is not None
    assert call_receipt.status == 1

    contract_acc = funded_state.get_account(contract_addr)
    # Contract balances:
    # Native: 20 (init) + 10 (sent) - 15 (transferred out) = 15
    # Custom: 100 (init) + 50 (sent) - 40 (transferred out) = 110
    assert contract_acc["balances"][""] == 15
    assert contract_acc["balances"][custom_token] == 110

    # Recipient Bob balances:
    bob_acc = funded_state.get_account(bob_pk)
    assert bob_acc["balances"][""] == 15
    assert bob_acc["balances"][custom_token] == 40

    # Alice balances:
    # Native: 100 - 20 (deploy) - 10 (call) = 70
    # Custom: 500 - 100 (deploy) - 50 (call) = 350
    alice_acc = funded_state.get_account(alice_pk)
    assert alice_acc["balances"][""] == 70
    assert alice_acc["balances"][custom_token] == 350


# ------------------------------------------------------------------
# Composite Flags Tests (Flag 3)
# ------------------------------------------------------------------

def test_composite_flag_mint_and_burn(alice, funded_state):
    """
    Composite Flag (Flag 3): Sender uses flags=ALLOW_MINT | ALLOW_BURN to mint
    a custom token and send directly to BURN_ADDRESS. Verify transaction succeeds.
    """
    alice_sk, alice_pk = alice

    token_asset = f"{alice_pk}.INSTABURN"
    tx = Transaction(
        alice_pk,
        BURN_ADDRESS,
        amount={token_asset: 1000},
        nonce=0,
        flags=ALLOW_MINT | ALLOW_BURN,
        gas_limit=5,
        fee_per_gas=1,
    )
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.VALID
    assert receipt is not None
    assert receipt.status == 1

    # Sender balance not debited custom token
    assert funded_state.get_account(alice_pk)["balances"].get(token_asset, 0) == 0
    # Native gas debited
    assert funded_state.get_account(alice_pk)["balances"][""] == 100 - 5
    # BURN_ADDRESS received the tokens
    assert funded_state.get_account(BURN_ADDRESS)["balances"][token_asset] == 1000


def test_mint_multiple_custom_tokens_in_single_tx(alice, bob, funded_state):
    """
    Verify that multiple custom assets can be minted in a single transaction
    under the sender's namespace when ALLOW_MINT is set.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    asset1 = f"{alice_pk}.USD"
    asset2 = f"{alice_pk}.EUR"

    tx = Transaction(
        alice_pk,
        bob_pk,
        amount={asset1: 1000, asset2: 2000},
        nonce=0,
        flags=ALLOW_MINT,
    )
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.VALID
    assert receipt.status == 1

    assert funded_state.get_account(bob_pk)["balances"][asset1] == 1000
    assert funded_state.get_account(bob_pk)["balances"][asset2] == 2000
    assert funded_state.get_account(alice_pk)["balances"][""] == 100


def test_allow_mint_does_not_permit_minting_native_currency(alice, bob, funded_state):
    """
    ALLOW_MINT does not bypass balance check for native currency ("").
    Alice cannot mint native currency beyond her balance.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    # Alice has 100 native currency, tries to send 500 native currency with ALLOW_MINT
    tx = Transaction(
        alice_pk,
        bob_pk,
        amount={"": 500},
        nonce=0,
        flags=ALLOW_MINT,
    )
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None
    assert funded_state.get_account(alice_pk)["balances"][""] == 100
    assert funded_state.get_account(alice_pk)["nonce"] == 0


def test_unprefixed_asset_cannot_be_minted_with_allow_mint(alice, bob, funded_state):
    """
    Alice tries to mint a token without her namespace prefix (e.g. "BTC") with ALLOW_MINT.
    Fails because asset does not start with f"{alice_pk}.".
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    tx = Transaction(
        alice_pk,
        bob_pk,
        amount={"BTC": 10},
        nonce=0,
        flags=ALLOW_MINT,
    )
    tx.sign(alice_sk)

    status, receipt = funded_state.validate_and_apply_with_status(tx)
    assert status == ValidationStatus.FAILED
    assert receipt is None
    assert funded_state.get_account(alice_pk)["nonce"] == 0


def test_malformed_amount_or_flags_rejected(alice, bob, funded_state):
    """
    Transactions with negative amounts, negative flags, or non-int values
    must be rejected with ValidationStatus.MALFORMED.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    # Negative amount
    tx_neg = Transaction(alice_pk, bob_pk, amount={"": -10}, nonce=0)
    tx_neg.sign(alice_sk)
    status, _ = funded_state.validate_and_apply_with_status(tx_neg)
    assert status == ValidationStatus.MALFORMED

    # Negative flags
    tx_flags = Transaction(alice_pk, bob_pk, amount=10, nonce=0, flags=-1)
    tx_flags.sign(alice_sk)
    status, _ = funded_state.validate_and_apply_with_status(tx_flags)
    assert status == ValidationStatus.MALFORMED

    # Boolean amount value
    tx_bool = Transaction(alice_pk, bob_pk, amount={"": True}, nonce=0)
    tx_bool.sign(alice_sk)
    status, _ = funded_state.validate_and_apply_with_status(tx_bool)
    assert status == ValidationStatus.MALFORMED


def test_contract_transfer_out_integer_coercion(alice, bob, funded_state):
    """
    Contract transfers out an integer amount (coerced to {"": int}).
    Verify recipient receives native currency.
    """
    alice_sk, alice_pk = alice
    _, bob_pk = bob

    code = """
target = msg['data']['target']
transfer_out(target, 42)
"""
    deploy_tx = Transaction(
        alice_pk,
        None,
        amount=100,
        nonce=0,
        data=code,
        gas_limit=50000,
        fee_per_gas=0,
    )
    deploy_tx.sign(alice_sk)
    receipt = funded_state.apply_transaction(deploy_tx)
    assert receipt is not None
    assert receipt.status == 1
    contract_addr = receipt.contract_address

    call_tx = Transaction(
        alice_pk,
        contract_addr,
        amount=0,
        nonce=1,
        data={"target": bob_pk},
        gas_limit=50000,
        fee_per_gas=0,
    )
    call_tx.sign(alice_sk)
    call_receipt = funded_state.apply_transaction(call_tx)
    assert call_receipt is not None
    assert call_receipt.status == 1

    assert funded_state.get_account(contract_addr)["balances"][""] == 100 - 42
    assert funded_state.get_account(bob_pk)["balances"][""] == 42


def test_transaction_to_dict_and_from_dict_multi_asset_roundtrip(alice, bob):
    """
    Transaction serialization retains amount dictionary and flags,
    omits legacy ticker and assets, and deserializes correctly.
    """
    _, alice_pk = alice
    _, bob_pk = bob

    amount_dict = {"": 10, f"{alice_pk}.FOO": 50}
    tx = Transaction(
        alice_pk,
        bob_pk,
        amount=amount_dict,
        nonce=42,
        flags=ALLOW_MINT,
        gas_limit=100,
        fee_per_gas=2,
    )

    d = tx.to_dict()
    assert "ticker" not in d
    assert "assets" not in d
    assert d["amount"] == amount_dict
    assert d["flags"] == ALLOW_MINT

    sd = tx.to_signing_dict()
    assert "ticker" not in sd
    assert "assets" not in sd
    assert sd["amount"] == amount_dict
    assert sd["flags"] == ALLOW_MINT

    restored = Transaction.from_dict(d)
    assert restored.sender == alice_pk
    assert restored.receiver == bob_pk
    assert restored.amount == amount_dict
    assert restored.flags == ALLOW_MINT
    assert restored.nonce == 42
    assert restored.gas_limit == 100
    assert restored.fee_per_gas == 2


def test_contract_call_with_allow_mint(alice, funded_state):
    """
    Verify that calling a contract with flags=ALLOW_MINT permits minting
    a custom token directly into the contract call.
    """
    alice_sk, alice_pk = alice
    token_asset = f"{alice_pk}.MINTCALL"

    # Contract code that stores received msg['value']
    code = "storage['v'] = msg['value']"
    deploy_tx = Transaction(
        alice_pk,
        None,
        amount=0,
        nonce=0,
        data=code,
        gas_limit=50000,
        fee_per_gas=0,
    )
    deploy_tx.sign(alice_sk)
    deploy_receipt = funded_state.apply_transaction(deploy_tx)
    assert deploy_receipt.status == 1
    contract_addr = deploy_receipt.contract_address

    # Alice has 0 balance of token_asset, but passes flags=ALLOW_MINT
    call_tx = Transaction(
        alice_pk,
        contract_addr,
        amount={token_asset: 77},
        nonce=1,
        data="invoke",
        gas_limit=50000,
        fee_per_gas=0,
        flags=ALLOW_MINT,
    )
    call_tx.sign(alice_sk)
    call_receipt = funded_state.apply_transaction(call_tx)
    assert call_receipt.status == 1

    # Contract received the minted tokens
    assert funded_state.get_account(contract_addr)["balances"][token_asset] == 77
    # Alice was not debited into negative balance
    assert funded_state.get_account(alice_pk)["balances"].get(token_asset, 0) == 0


def test_contract_call_failure_rollback_prevents_negative_balance(alice, funded_state):
    """
    Verify that when a contract call fails, balances are cleanly restored from
    pre-call snapshots so no negative balances occur.
    """
    alice_sk, alice_pk = alice
    token_asset = f"{alice_pk}.TOKFAIL"

    # Contract that throws an error during execution
    code = "raise ValueError('intentional error')"
    deploy_tx = Transaction(
        alice_pk,
        None,
        amount={token_asset: 50},
        nonce=0,
        data=code,
        gas_limit=50000,
        fee_per_gas=0,
        flags=ALLOW_MINT,
    )
    deploy_tx.sign(alice_sk)
    deploy_receipt = funded_state.apply_transaction(deploy_tx)
    assert deploy_receipt.status == 1
    contract_addr = deploy_receipt.contract_address

    # Contract initially has 50 TOKFAIL
    assert funded_state.get_account(contract_addr)["balances"][token_asset] == 50

    # Call the failing contract with additional minted tokens
    call_tx = Transaction(
        alice_pk,
        contract_addr,
        amount={token_asset: 30},
        nonce=1,
        data="boom",
        gas_limit=50000,
        fee_per_gas=0,
        flags=ALLOW_MINT,
    )
    call_tx.sign(alice_sk)
    call_receipt = funded_state.apply_transaction(call_tx)
    assert call_receipt.status == 0  # Reverts

    # Balances must be cleanly restored to pre-call state
    assert funded_state.get_account(contract_addr)["balances"][token_asset] == 50
    assert funded_state.get_account(alice_pk)["balances"].get(token_asset, 0) == 0


# ------------------------------------------------------------------
# Miner-Configurable Fee Assets
# ------------------------------------------------------------------

def test_fee_paid_in_custom_asset(alice, bob, funded_state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob
    token = f"{alice_pk}.FEECOIN"

    # Give Alice 100 FEECOIN
    funded_state.get_account(alice_pk)["balances"][token] = 100

    # Alice sends 5 native coins, paying gas fees in FEECOIN
    tx = Transaction(
        alice_pk,
        bob_pk,
        amount=5,
        nonce=0,
        gas_limit=10,
        fee_per_gas=2,
        fee_asset=token,
    )
    tx.sign(alice_sk)

    receipt = funded_state.apply_transaction(tx)
    assert receipt.status == 1

    # Native: Alice was at 100, spent 5 -> 95
    assert funded_state.get_account(alice_pk)["balances"][""] == 95
    # Token: Alice was at 100, spent 10 * 2 = 20 fee -> 80
    assert funded_state.get_account(alice_pk)["balances"][token] == 80
    # Bob received 5 native
    assert funded_state.get_account(bob_pk)["balances"][""] == 5


def test_fee_paid_in_custom_asset_insufficient_fee_balance(alice, bob, funded_state):
    alice_sk, alice_pk = alice
    _, bob_pk = bob
    token = f"{alice_pk}.FEECOIN"

    # Alice only has 10 FEECOIN
    funded_state.get_account(alice_pk)["balances"][token] = 10

    # Gas cost requires 10 * 2 = 20 FEECOIN
    tx = Transaction(
        alice_pk,
        bob_pk,
        amount=5,
        nonce=0,
        gas_limit=10,
        fee_per_gas=2,
        fee_asset=token,
    )
    tx.sign(alice_sk)

    status = funded_state.verify_transaction_logic(tx)
    assert status == ValidationStatus.FAILED


def test_block_miner_receives_custom_asset_fees(alice, bob):
    alice_sk, alice_pk = alice
    bob_sk, bob_pk = bob
    from minichain.chain import Blockchain
    from minichain.mempool import Mempool
    from main import mine_and_process_block

    bc = Blockchain()
    miner_pk = "11" * 32
    token = f"{alice_pk}.PAYFEE"
    mempool = Mempool(accepted_fee_assets=["", token])

    # Fund Alice with native and token
    bc.state.credit_mining_reward(alice_pk, 100)
    bc.state.get_account(alice_pk)["balances"][token] = 50

    tx = Transaction(
        alice_pk,
        bob_pk,
        amount=1,
        nonce=0,
        gas_limit=10,
        fee_per_gas=3,
        fee_asset=token,
    )
    tx.sign(alice_sk)
    assert mempool.add_transaction(tx) is True

    mined_block = mine_and_process_block(bc, mempool, miner_pk)
    assert mined_block is not None

    # Miner gets block reward (50 native) and custom fees (30 PAYFEE)
    miner_acc = bc.state.get_account(miner_pk)
    assert miner_acc["balances"][""] == bc.state.DEFAULT_MINING_REWARD
    assert miner_acc["balances"][token] == 30



