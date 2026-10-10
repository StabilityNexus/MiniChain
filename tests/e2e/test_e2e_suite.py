"""
tests/e2e/test_e2e_suite.py

Comprehensive End-to-End Requirement Verification Test Suite for minichain.
Treats native currency and custom tokens uniformly across:
- Tier 1: Feature Coverage (R1-R4 core flows)
- Tier 2: Boundary & Corner Cases (Zero, negative, invalid, edge conditions)
- Tier 3: Cross-Feature Combinations (Pairwise mint+transfer, burn+transfer, flag 3, cross-contract)
- Tier 4: Real-World Application Scenarios (DEX, Token Mint & Distribute, Treasury Burn, Multi-Token Crowdfunding)

All tests adhere strictly to PROJECT.md and ORIGINAL_REQUEST.md interface contracts.
"""

import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain.transaction import Transaction
from minichain.state import State
from minichain.validators import ValidationStatus

# Protocol Constants
ALLOW_MINT = getattr(Transaction, "ALLOW_MINT", 1)
ALLOW_BURN = getattr(Transaction, "ALLOW_BURN", 2)
BURN_ADDRESS = getattr(Transaction, "BURN_ADDRESS", "0000000000000000000000000000000000000000")


# ---------------------------------------------------------------------------
# Fixtures and Helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def make_key():
    """Generates a cryptographic signing key and hex-encoded public key."""
    def _make():
        sk = SigningKey.generate()
        pk = sk.verify_key.encode(encoder=HexEncoder).decode()
        return sk, pk
    return _make


@pytest.fixture
def alice(make_key):
    return make_key()


@pytest.fixture
def bob(make_key):
    return make_key()


@pytest.fixture
def charlie(make_key):
    return make_key()


@pytest.fixture
def dave(make_key):
    return make_key()


@pytest.fixture
def state():
    """Returns a fresh State instance."""
    return State()


def sign_tx(tx: Transaction, sk: SigningKey) -> Transaction:
    """Signs transaction with signing key and returns the signed transaction."""
    tx.sign(sk)
    return tx


def build_and_sign_tx(sender_sk, sender_pk, receiver_pk, amount, nonce=0,
                      gas_limit=10000, fee_per_gas=1, data=None, flags=0,
                      chain_id="minichain-default") -> Transaction:
    """Helper to construct and sign a transaction."""
    tx = Transaction(
        sender=sender_pk,
        receiver=receiver_pk,
        amount=amount,
        nonce=nonce,
        gas_limit=gas_limit,
        fee_per_gas=fee_per_gas,
        data=data,
        chain_id=chain_id,
        flags=flags
    )
    tx.sign(sender_sk)
    return tx


# ===========================================================================
# TIER 1: FEATURE COVERAGE
# ===========================================================================

class TestTier1UnifiedAmountDict:
    """Tier 1 Feature 1: Unified amount dictionary in Transaction (R1)."""

    def test_t1_r1_init_with_dict_amount(self, alice, bob):
        """Transaction initialized with dict amount preserves amount mapping."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        expected_amount = {f"{alice_pk}.TOKEN": 50, "": 100}
        tx = Transaction(alice_pk, bob_pk, amount=expected_amount, nonce=0)
        assert tx.amount == expected_amount

    def test_t1_r1_int_amount_backwards_compatibility(self, alice, bob):
        """Transaction initialized with integer amount is coerced to {'': amount}."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount=250, nonce=0)
        assert tx.amount == {"": 250}

    def test_t1_r1_serialization_includes_amount_dict(self, alice, bob):
        """to_dict and to_signing_dict serialize amount as dictionary without ticker/assets."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        amt = {"": 100, f"{alice_pk}.ABC": 50}
        tx = Transaction(alice_pk, bob_pk, amount=amt, nonce=0, flags=1)
        d = tx.to_dict()
        sd = tx.to_signing_dict()

        assert d["amount"] == amt
        assert d["flags"] == 1
        assert "ticker" not in d
        assert "assets" not in d

        assert sd["amount"] == amt
        assert sd["flags"] == 1
        assert "ticker" not in sd
        assert "assets" not in sd

    def test_t1_r1_deserialization_from_dict(self, alice, bob):
        """from_dict reconstructs Transaction with dictionary amount and flags."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        payload = {
            "sender": alice_pk,
            "receiver": bob_pk,
            "amount": {f"{alice_pk}.XYZ": 75},
            "nonce": 3,
            "gas_limit": 5000,
            "fee_per_gas": 1,
            "flags": 1,
            "chain_id": "minichain-default"
        }
        tx = Transaction.from_dict(payload)
        assert tx.amount == {f"{alice_pk}.XYZ": 75}
        assert tx.flags == 1
        assert tx.nonce == 3

    def test_t1_r1_state_transfers_custom_asset_via_dict_amount(self, alice, bob, state):
        """State processes transfer with dictionary amount, updating sender and receiver balances."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token_name = f"{alice_pk}.GOLD"

        state.credit_mining_reward(alice_pk, 100000)
        state.get_account(alice_pk)["balances"][token_name] = 200

        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={token_name: 70}, nonce=0)
        receipt = state.apply_transaction(tx)

        assert receipt is not None
        assert receipt.status == 1
        assert state.get_account(alice_pk)["balances"][token_name] == 130
        assert state.get_account(bob_pk)["balances"][token_name] == 70


class TestTier1NativeEmptyString:
    """Tier 1 Feature 2: Native currency identified by empty string key '' (R1/R3)."""

    def test_t1_native_transfer_with_empty_string_key(self, alice, bob, state):
        """Direct transfer using {'': amount} transfers native balance correctly."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 1000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={"": 300}, nonce=0, gas_limit=10, fee_per_gas=1)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][""] == 300
        assert state.get_account(alice_pk)["balances"][""] == 1000 - 300 - 10

    def test_t1_native_gas_fee_debited_from_empty_string_balance(self, alice, bob, state):
        """Gas fees are debited strictly from sender's native balance ('')."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.TOK"

        state.credit_mining_reward(alice_pk, 500)
        state.get_account(alice_pk)["balances"][token] = 50

        # Moving token only, no native value transferred
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={token: 25}, nonce=0, gas_limit=50, fee_per_gas=2)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(alice_pk)["balances"][token] == 25
        assert state.get_account(alice_pk)["balances"][""] == 500 - (50 * 2)

    def test_t1_native_insufficient_balance_for_transfer_and_gas_fails(self, alice, bob, state):
        """Transaction fails when native balance cannot cover native amount plus gas fees."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 100)
        # 100 native transfer + (10 * 1) gas = 110 required, only 100 available
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={"": 100}, nonce=0, gas_limit=10, fee_per_gas=1)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t1_native_contract_deployment_funds_native_balance(self, alice, state):
        """Deploying contract with native amount credits contract's empty string balance."""
        alice_sk, alice_pk = alice
        state.credit_mining_reward(alice_pk, 100000)

        tx = build_and_sign_tx(alice_sk, alice_pk, None, amount={"": 500}, nonce=0, data="x = 1", gas_limit=50000, fee_per_gas=1)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert receipt.contract_address is not None
        assert state.get_account(receipt.contract_address)["balances"][""] == 500

    def test_t1_native_mining_reward_credited_to_empty_string(self, alice, state):
        """Mining reward increases the empty string key balance in accounts."""
        _, alice_pk = alice
        state.credit_mining_reward(alice_pk, 450)
        assert state.get_account(alice_pk)["balances"][""] == 450


class TestTier1FlagsBitmask:
    """Tier 1 Feature 3: Flags bitmask field for minting and burning (R2)."""

    def test_t1_flags_default_to_zero(self, alice, bob):
        """Transaction defaults flags to 0 if not specified."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={"": 10}, nonce=0)
        assert tx.flags == 0

    def test_t1_flags_serialized_and_deserialized(self, alice, bob):
        """Flags field survives to_dict, to_signing_dict, and from_dict roundtrip."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx1 = Transaction(alice_pk, bob_pk, amount={"": 10}, nonce=0, flags=3)
        d = tx1.to_dict()
        assert d["flags"] == 3

        tx2 = Transaction.from_dict(d)
        assert tx2.flags == 3

    def test_t1_flags_included_in_signature_verification(self, alice, bob):
        """Tampering with flags invalidates cryptographic signature verification."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={"": 10}, nonce=0, flags=1)
        tx.sign(alice_sk)
        assert tx.verify() is True

        # Manually alter flags after signing (bypass sealed guard to simulate wire tampering)
        object.__setattr__(tx, "flags", 2)
        assert tx.verify() is False

    def test_t1_flags_constants_values(self):
        """Verify protocol flag constants align with spec."""
        assert ALLOW_MINT == 1
        assert ALLOW_BURN == 2
        assert (ALLOW_MINT | ALLOW_BURN) == 3

    def test_t1_flags_well_formed_check(self, alice, bob, state):
        """Non-negative integer flags pass well-formed check."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        for flag in [0, 1, 2, 3, 7]:
            tx = Transaction(alice_pk, bob_pk, amount={"": 10}, nonce=0, flags=flag)
            assert state._amounts_well_formed(tx) is True


class TestTier1MintingAllowMint:
    """Tier 1 Feature 4: Minting with ALLOW_MINT (flags=1) (R2)."""

    def test_t1_mint_creates_new_supply_to_recipient(self, alice, bob, state):
        """Flag 1 creates supply of <sender>.TOKEN to recipient without debiting sender token balance."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.ALPHA"

        state.credit_mining_reward(alice_pk, 20000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={token: 500}, nonce=0, flags=ALLOW_MINT)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][token] == 500
        # Alice was not debited token balance (balance never negative)
        assert state.get_account(alice_pk)["balances"].get(token, 0) == 0

    def test_t1_mint_to_self(self, alice, state):
        """Sender can mint tokens directly to themselves with flags=ALLOW_MINT."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.SELF"

        state.credit_mining_reward(alice_pk, 20000)
        tx = build_and_sign_tx(alice_sk, alice_pk, alice_pk, amount={token: 1200}, nonce=0, flags=ALLOW_MINT)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(alice_pk)["balances"][token] == 1200

    def test_t1_transfer_without_allow_mint_fails_if_unfunded(self, alice, bob, state):
        """Attempting to transfer <sender>.TOKEN without ALLOW_MINT and without balance fails."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.BETA"

        state.credit_mining_reward(alice_pk, 10000)
        # flags=0
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={token: 100}, nonce=0, flags=0)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t1_mint_multiple_tokens_under_sender_namespace(self, alice, bob, state):
        """Sender can mint multiple distinct tokens under their namespace in a single transaction."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tok1 = f"{alice_pk}.T1"
        tok2 = f"{alice_pk}.T2"

        state.credit_mining_reward(alice_pk, 20000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={tok1: 300, tok2: 700}, nonce=0, flags=ALLOW_MINT)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][tok1] == 300
        assert state.get_account(bob_pk)["balances"][tok2] == 700

    def test_t1_mint_only_debits_gas_not_minted_amount(self, alice, bob, state):
        """Minting transaction charges only native gas fee from sender."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.CRED"

        state.credit_mining_reward(alice_pk, 5000)
        gas_limit = 20
        fee_per_gas = 3
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={token: 99999}, nonce=0,
                               gas_limit=gas_limit, fee_per_gas=fee_per_gas, flags=ALLOW_MINT)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        expected_remaining_native = 5000 - (gas_limit * fee_per_gas)
        assert state.get_account(alice_pk)["balances"][""] == expected_remaining_native


class TestTier1BurningAllowBurn:
    """Tier 1 Feature 5: Burning with ALLOW_BURN (flags=2) (R2)."""

    def test_t1_burn_native_currency_with_allow_burn(self, alice, state):
        """Native currency can be sent to BURN_ADDRESS when flags=ALLOW_BURN."""
        alice_sk, alice_pk = alice
        state.credit_mining_reward(alice_pk, 10000)

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={"": 400}, nonce=0,
                               gas_limit=10, fee_per_gas=1, flags=ALLOW_BURN)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(alice_pk)["balances"][""] == 10000 - 400 - 10

    def test_t1_burn_custom_token_with_allow_burn(self, alice, state):
        """Custom token can be sent to BURN_ADDRESS when flags=ALLOW_BURN."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.SHIB"

        state.credit_mining_reward(alice_pk, 20000)
        state.get_account(alice_pk)["balances"][token] = 1000

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={token: 450}, nonce=0, flags=ALLOW_BURN)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(alice_pk)["balances"][token] == 550

    def test_t1_burn_without_allow_burn_fails(self, alice, state):
        """Sending to BURN_ADDRESS with flags=0 is rejected."""
        alice_sk, alice_pk = alice
        state.credit_mining_reward(alice_pk, 10000)

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={"": 100}, nonce=0, flags=0)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t1_burn_with_allow_mint_only_fails(self, alice, state):
        """Sending to BURN_ADDRESS with flags=ALLOW_MINT (1) without ALLOW_BURN (2) is rejected."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.TOKEN"
        state.credit_mining_reward(alice_pk, 10000)

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={token: 100}, nonce=0, flags=ALLOW_MINT)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t1_burn_address_receives_burned_asset_or_gas_not_burned(self, alice, state):
        """Burn address receives the burned assets, but gas cost is debited separately."""
        alice_sk, alice_pk = alice
        state.credit_mining_reward(alice_pk, 5000)

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={"": 200}, nonce=0,
                               gas_limit=50, fee_per_gas=1, flags=ALLOW_BURN)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        burn_acc = state.get_account(BURN_ADDRESS)
        # Burn address contains 200 native tokens (not including gas)
        assert burn_acc["balances"].get("", 0) == 200


class TestTier1ContractDictAmounts:
    """Tier 1 Feature 6: Contract dictionary amounts and internal calls (R4)."""

    def test_t1_contract_deploy_with_multi_token_amount(self, alice, state):
        """Contract deployed with dictionary amount initializes contract balances for all assets."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.SUP"
        state.credit_mining_reward(alice_pk, 100000)
        state.get_account(alice_pk)["balances"][token] = 500

        amt = {"": 100, token: 200}
        tx = build_and_sign_tx(alice_sk, alice_pk, None, amount=amt, nonce=0, data="storage['init'] = 1",
                               gas_limit=50000, fee_per_gas=1)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        caddr = receipt.contract_address
        assert state.get_account(caddr)["balances"][""] == 100
        assert state.get_account(caddr)["balances"][token] == 200

    def test_t1_contract_call_receives_dict_msg_value(self, alice, state):
        """Contract execution receives msg['value'] as a dictionary."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.IN"
        state.credit_mining_reward(alice_pk, 100000)
        state.get_account(alice_pk)["balances"][token] = 300

        # Contract records received value dict into storage
        code = "storage['val'] = msg.get('value')"
        deploy_tx = build_and_sign_tx(alice_sk, alice_pk, None, amount=0, nonce=0, data=code,
                                      gas_limit=50000, fee_per_gas=1)
        receipt = state.apply_transaction(deploy_tx)
        caddr = receipt.contract_address

        # Call with multi-token value
        call_tx = build_and_sign_tx(alice_sk, alice_pk, caddr, amount={"": 10, token: 50}, nonce=1,
                                    data="run", gas_limit=50000, fee_per_gas=1)
        receipt2 = state.apply_transaction(call_tx)

        assert receipt2.status == 1
        stored_val = state.get_account(caddr)["storage"]["val"]
        assert stored_val == {"": 10, token: 50}

    def test_t1_contract_transfer_out_dict_custom_token(self, alice, bob, state):
        """Contract calls transfer_out with dictionary amount of custom token."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.TRANSFER"

        state.credit_mining_reward(alice_pk, 100000)
        state.get_account(alice_pk)["balances"][token] = 500

        code = f"""
target = msg['data']['target']
tok = msg['data']['tok']
transfer_out(target, {{tok: 75}})
"""
        deploy_tx = build_and_sign_tx(alice_sk, alice_pk, None, amount={"": 10, token: 200}, nonce=0,
                                      data=code, gas_limit=50000, fee_per_gas=1)
        receipt = state.apply_transaction(deploy_tx)
        caddr = receipt.contract_address

        call_tx = build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=1,
                                    data={"target": bob_pk, "tok": token}, gas_limit=50000, fee_per_gas=1)
        receipt2 = state.apply_transaction(call_tx)

        assert receipt2.status == 1
        assert state.get_account(bob_pk)["balances"][token] == 75
        assert state.get_account(caddr)["balances"][token] == 125

    def test_t1_contract_transfer_out_int_coerced_to_native(self, alice, bob, state):
        """Contract calls transfer_out with integer amount, correctly treated as native currency."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 100000)
        code = """
target = msg['data']['target']
transfer_out(target, 60)
"""
        deploy_tx = build_and_sign_tx(alice_sk, alice_pk, None, amount=100, nonce=0,
                                      data=code, gas_limit=50000, fee_per_gas=1)
        receipt = state.apply_transaction(deploy_tx)
        caddr = receipt.contract_address

        call_tx = build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=1,
                                    data={"target": bob_pk}, gas_limit=50000, fee_per_gas=1)
        receipt2 = state.apply_transaction(call_tx)

        assert receipt2.status == 1
        assert state.get_account(bob_pk)["balances"][""] == 60
        assert state.get_account(caddr)["balances"][""] == 40

    def test_t1_contract_call_contract_with_dict_value(self, alice, state):
        """Contract A calls Contract B passing dictionary value."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.SUBCALL"

        state.credit_mining_reward(alice_pk, 100000)
        state.get_account(alice_pk)["balances"][token] = 500

        # Contract B: records received value
        code_b = "storage['received'] = msg.get('value')"
        tx_b = build_and_sign_tx(alice_sk, alice_pk, None, amount=0, nonce=0, data=code_b,
                                 gas_limit=50000, fee_per_gas=1)
        caddr_b = state.apply_transaction(tx_b).contract_address

        # Contract A: calls B with custom token amount
        code_a = f"""
target = "{caddr_b}"
tok = "{token}"
call_contract(target, 'ping', {{tok: 45}})
"""
        tx_a = build_and_sign_tx(alice_sk, alice_pk, None, amount={token: 100}, nonce=1, data=code_a,
                                 gas_limit=50000, fee_per_gas=1)
        caddr_a = state.apply_transaction(tx_a).contract_address

        # Trigger A
        call_tx = build_and_sign_tx(alice_sk, alice_pk, caddr_a, amount=0, nonce=2, data="go",
                                    gas_limit=80000, fee_per_gas=1)
        receipt = state.apply_transaction(call_tx)

        assert receipt.status == 1
        assert state.get_account(caddr_b)["storage"]["received"] == {token: 45}
        assert state.get_account(caddr_b)["balances"][token] == 45
        assert state.get_account(caddr_a)["balances"][token] == 55


# ===========================================================================
# TIER 2: BOUNDARY & CORNER CASES
# ===========================================================================

class TestTier2ZeroAmounts:
    """Tier 2 Corner Case 1: Zero amounts handling."""

    def test_t2_zero_native_amount_transfer(self, alice, bob, state):
        """Transfer of {'': 0} succeeds and only debits gas fee."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 1000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={"": 0}, nonce=0, gas_limit=15, fee_per_gas=1)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(alice_pk)["balances"][""] == 1000 - 15
        assert state.get_account(bob_pk)["balances"].get("", 0) == 0

    def test_t2_zero_custom_token_amount_transfer(self, alice, bob, state):
        """Transfer of {token: 0} succeeds without altering balances."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.ZTOK"

        state.credit_mining_reward(alice_pk, 20000)
        state.get_account(alice_pk)["balances"][token] = 50

        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={token: 0}, nonce=0)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(alice_pk)["balances"][token] == 50
        assert state.get_account(bob_pk)["balances"].get(token, 0) == 0

    def test_t2_mixed_zero_and_nonzero_amount(self, alice, bob, state):
        """Transfer with zero native and nonzero custom asset updates only nonzero asset."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.MIX"

        state.credit_mining_reward(alice_pk, 20000)
        state.get_account(alice_pk)["balances"][token] = 100

        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={"": 0, token: 40}, nonce=0)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][token] == 40
        assert state.get_account(bob_pk)["balances"].get("", 0) == 0

    def test_t2_mint_zero_tokens_with_allow_mint(self, alice, bob, state):
        """Minting with amount=0 succeeds without adding supply."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.ZERO"

        state.credit_mining_reward(alice_pk, 20000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={token: 0}, nonce=0, flags=ALLOW_MINT)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"].get(token, 0) == 0

    def test_t2_contract_transfer_out_zero_rejected(self, alice, bob, state):
        """Contract calling transfer_out with 0 or empty dict raises error."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 100000)
        code = f"""
target = "{bob_pk}"
transfer_out(target, 0)
"""
        deploy_tx = build_and_sign_tx(alice_sk, alice_pk, None, amount=100, nonce=0, data=code,
                                      gas_limit=50000, fee_per_gas=1)
        caddr = state.apply_transaction(deploy_tx).contract_address

        call_tx = build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=1, data="go",
                                    gas_limit=50000, fee_per_gas=1)
        receipt = state.apply_transaction(call_tx)
        assert receipt.status == 0  # Reverted due to invalid transfer amount


class TestTier2NegativeAmountsRejected:
    """Tier 2 Corner Case 2: Negative amounts rejected as MALFORMED."""

    def test_t2_negative_native_amount_rejected(self, alice, bob, state):
        """Transaction with negative native amount is MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={"": -50}, nonce=0)
        assert state._amounts_well_formed(tx) is False
        tx.sign(alice_sk)
        status, _ = state.validate_and_apply_with_status(tx)
        assert status == ValidationStatus.MALFORMED

    def test_t2_negative_custom_asset_amount_rejected(self, alice, bob, state):
        """Transaction with negative custom token amount is MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={f"{alice_pk}.TOK": -10}, nonce=0)
        assert state._amounts_well_formed(tx) is False
        tx.sign(alice_sk)
        status, _ = state.validate_and_apply_with_status(tx)
        assert status == ValidationStatus.MALFORMED

    def test_t2_mixed_positive_and_negative_amounts_rejected(self, alice, bob, state):
        """Transaction with any negative value in amount dict is MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={"": 100, f"{alice_pk}.TOK": -1}, nonce=0)
        assert state._amounts_well_formed(tx) is False

    def test_t2_contract_transfer_out_negative_int_raises_error(self, alice, bob, state):
        """Contract calling transfer_out with negative integer fails execution."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 100000)
        code = f"""
target = "{bob_pk}"
transfer_out(target, -50)
"""
        caddr = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=100, nonce=0, data=code, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=1, data="go", gas_limit=50000, fee_per_gas=1)
        )
        assert receipt.status == 0

    def test_t2_contract_transfer_out_negative_dict_raises_error(self, alice, bob, state):
        """Contract calling transfer_out with negative dict value fails execution."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 100000)
        code = f"""
target = "{bob_pk}"
transfer_out(target, {{"": -20}})
"""
        caddr = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=100, nonce=0, data=code, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=1, data="go", gas_limit=50000, fee_per_gas=1)
        )
        assert receipt.status == 0


class TestTier2NonDictAmountsRejected:
    """Tier 2 Corner Case 3: Non-dict / malformed amount types rejected."""

    def test_t2_string_amount_rejected(self, alice, bob, state):
        """String amount passed directly is rejected as MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount="1000", nonce=0)
        assert state._amounts_well_formed(tx) is False

    def test_t2_list_amount_rejected(self, alice, bob, state):
        """List amount is rejected as MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount=[100], nonce=0)
        assert state._amounts_well_formed(tx) is False

    def test_t2_boolean_amount_rejected(self, alice, bob, state):
        """Boolean amount (True/False) is rejected as MALFORMED and not treated as int 1/0."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount=True, nonce=0)
        assert state._amounts_well_formed(tx) is False

    def test_t2_none_amount_rejected(self, alice, bob, state):
        """None amount is rejected as MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount=None, nonce=0)
        assert state._amounts_well_formed(tx) is False

    def test_t2_dict_with_non_string_key_or_non_int_val_rejected(self, alice, bob, state):
        """Dictionary with integer keys or string values is rejected as MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx1 = Transaction(alice_pk, bob_pk, amount={123: 10}, nonce=0)
        assert state._amounts_well_formed(tx1) is False

        tx2 = Transaction(alice_pk, bob_pk, amount={"": "10"}, nonce=0)
        assert state._amounts_well_formed(tx2) is False

        tx3 = Transaction(alice_pk, bob_pk, amount={"": True}, nonce=0)
        assert state._amounts_well_formed(tx3) is False


class TestTier2EmptyDictAmounts:
    """Tier 2 Corner Case 4: Empty dict amount handling."""

    def test_t2_empty_dict_amount_is_well_formed(self, alice, bob, state):
        """Empty dictionary amount is valid and well-formed."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={}, nonce=0)
        assert state._amounts_well_formed(tx) is True

    def test_t2_empty_dict_amount_transfer_succeeds(self, alice, bob, state):
        """Transfer with {} succeeds, debiting only gas and advancing nonce."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 1000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={}, nonce=0, gas_limit=10, fee_per_gas=1)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(alice_pk)["nonce"] == 1
        assert state.get_account(alice_pk)["balances"][""] == 990

    def test_t2_empty_dict_contract_deploy_succeeds(self, alice, state):
        """Deploying contract with amount={} initializes contract with empty balance."""
        alice_sk, alice_pk = alice
        state.credit_mining_reward(alice_pk, 100000)

        tx = build_and_sign_tx(alice_sk, alice_pk, None, amount={}, nonce=0, data="x = 10",
                               gas_limit=50000, fee_per_gas=1)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(receipt.contract_address)["balances"].get("", 0) == 0

    def test_t2_empty_dict_contract_call_succeeds(self, alice, state):
        """Calling contract with amount={} passes empty dictionary in msg['value']."""
        alice_sk, alice_pk = alice
        state.credit_mining_reward(alice_pk, 100000)

        code = "storage['v'] = msg.get('value')"
        caddr = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=0, nonce=0, data=code, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr, amount={}, nonce=1, data="test", gas_limit=50000, fee_per_gas=1)
        )
        assert receipt.status == 1
        assert state.get_account(caddr)["storage"]["v"] == {}

    def test_t2_empty_dict_with_invalid_nonce_fails(self, alice, bob, state):
        """Transaction with {} amount and invalid nonce fails verification."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 1000)
        # Expected nonce is 0, passing nonce 5
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={}, nonce=5)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None


class TestTier2MultipleTokenTransfers:
    """Tier 2 Corner Case 5: Multiple token transfers in a single transaction."""

    def test_t2_multi_token_transfer_all_succeed(self, alice, bob, state):
        """Multi-token transfer transfers native and multiple custom tokens atomically."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        t1, t2 = f"{alice_pk}.T1", f"{alice_pk}.T2"

        state.credit_mining_reward(alice_pk, 10000)
        state.get_account(alice_pk)["balances"][t1] = 100
        state.get_account(alice_pk)["balances"][t2] = 200

        amt = {"": 50, t1: 30, t2: 70}
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount=amt, nonce=0, gas_limit=10, fee_per_gas=1)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][""] == 50
        assert state.get_account(bob_pk)["balances"][t1] == 30
        assert state.get_account(bob_pk)["balances"][t2] == 70

        assert state.get_account(alice_pk)["balances"][t1] == 70
        assert state.get_account(alice_pk)["balances"][t2] == 130

    def test_t2_multi_token_transfer_insufficient_one_token_aborts_all(self, alice, bob, state):
        """If sender has insufficient balance for ANY token in multi-token dict, all transfers abort."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        t1, t2 = f"{alice_pk}.GOOD", f"{alice_pk}.SHORT"

        state.credit_mining_reward(alice_pk, 10000)
        state.get_account(alice_pk)["balances"][t1] = 100
        state.get_account(alice_pk)["balances"][t2] = 10  # Only 10 available

        # Trying to send 50 of SHORT
        amt = {"": 20, t1: 30, t2: 50}
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount=amt, nonce=0)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None
        # State unchanged
        assert state.get_account(alice_pk)["balances"][t1] == 100
        assert state.get_account(alice_pk)["balances"][t2] == 10
        assert state.get_account(bob_pk)["balances"].get(t1, 0) == 0

    def test_t2_multi_token_transfer_with_5_distinct_tokens(self, alice, bob, state):
        """Transfer containing 5 distinct custom tokens updates all 5 atomically."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 20000)
        tokens = [f"{alice_pk}.T{i}" for i in range(5)]
        for t in tokens:
            state.get_account(alice_pk)["balances"][t] = 100

        transfer_dict = {t: (i + 1) * 10 for i, t in enumerate(tokens)}
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount=transfer_dict, nonce=0)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        for i, t in enumerate(tokens):
            expected = (i + 1) * 10
            assert state.get_account(bob_pk)["balances"][t] == expected
            assert state.get_account(alice_pk)["balances"][t] == 100 - expected

    def test_t2_multi_token_transfer_with_partial_zero_values(self, alice, bob, state):
        """Multi-token transfer where some tokens are 0 and others nonzero updates only nonzeros."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        t1, t2 = f"{alice_pk}.NONZERO", f"{alice_pk}.ZEROKEY"

        state.credit_mining_reward(alice_pk, 20000)
        state.get_account(alice_pk)["balances"][t1] = 80
        state.get_account(alice_pk)["balances"][t2] = 50

        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={t1: 25, t2: 0}, nonce=0)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][t1] == 25
        assert state.get_account(bob_pk)["balances"].get(t2, 0) == 0
        assert state.get_account(alice_pk)["balances"][t2] == 50

    def test_t2_multi_token_transfer_reverts_on_contract_failure(self, alice, state):
        """Multi-token transfer to contract completely reverts if contract reverts."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.REV"

        state.credit_mining_reward(alice_pk, 100000)
        state.get_account(alice_pk)["balances"][token] = 200

        # Contract that reverts intentionally
        code = "raise Exception('Intentional revert')"
        caddr = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=0, nonce=0, data=code, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        tx = build_and_sign_tx(alice_sk, alice_pk, caddr, amount={"": 50, token: 100}, nonce=1,
                               data="fail", gas_limit=50000, fee_per_gas=1)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 0
        # Custom token balance was completely refunded/preserved
        assert state.get_account(alice_pk)["balances"][token] == 200
        assert state.get_account(caddr)["balances"].get(token, 0) == 0


class TestTier2BurnWithoutFlag2Rejected:
    """Tier 2 Corner Case 6: Transfers to BURN_ADDRESS require ALLOW_BURN flag."""

    def test_t2_burn_address_native_with_flag_0_fails(self, alice, state):
        """Native transfer to BURN_ADDRESS with flags=0 is rejected."""
        alice_sk, alice_pk = alice
        state.credit_mining_reward(alice_pk, 10000)

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={"": 100}, nonce=0, flags=0)
        status, receipt = state.validate_and_apply_with_status(tx)
        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t2_burn_address_native_with_flag_1_fails(self, alice, state):
        """Native transfer to BURN_ADDRESS with flags=ALLOW_MINT (1) without ALLOW_BURN fails."""
        alice_sk, alice_pk = alice
        state.credit_mining_reward(alice_pk, 10000)

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={"": 100}, nonce=0, flags=ALLOW_MINT)
        status, receipt = state.validate_and_apply_with_status(tx)
        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t2_burn_address_custom_token_with_flag_0_fails(self, alice, state):
        """Custom token transfer to BURN_ADDRESS with flags=0 is rejected."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.BURNME"
        state.credit_mining_reward(alice_pk, 10000)
        state.get_account(alice_pk)["balances"][token] = 500

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={token: 100}, nonce=0, flags=0)
        status, receipt = state.validate_and_apply_with_status(tx)
        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t2_burn_address_invalid_flag_bit_fails(self, alice, state):
        """Burn transfer with flags=4 (arbitrary flag not having ALLOW_BURN bit) fails."""
        alice_sk, alice_pk = alice
        state.credit_mining_reward(alice_pk, 10000)

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={"": 50}, nonce=0, flags=4)
        status, receipt = state.validate_and_apply_with_status(tx)
        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t2_burn_address_cannot_initiate_transactions(self, state):
        """Burn address has no private key and transactions from it cannot be verified."""
        tx = Transaction(BURN_ADDRESS, BURN_ADDRESS, amount={"": 10}, nonce=0)
        # Signature is None, verify() returns False
        assert tx.verify() is False
        status = state.verify_transaction_logic(tx)
        assert status == ValidationStatus.INVALID


class TestTier2UnauthorizedMintingRejected:
    """Tier 2 Corner Case 7: Unauthorized minting of other namespaces rejected."""

    def test_t2_unauthorized_mint_foreign_token_fails(self, alice, bob, state):
        """Alice cannot mint Bob's namespace token even if flags=ALLOW_MINT is set."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        foreign_token = f"{bob_pk}.DOLLARS"

        state.credit_mining_reward(alice_pk, 10000)
        # Alice tries to mint Bob's token
        tx = build_and_sign_tx(alice_sk, alice_pk, alice_pk, amount={foreign_token: 1000}, nonce=0, flags=ALLOW_MINT)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None
        assert state.get_account(alice_pk)["balances"].get(foreign_token, 0) == 0

    def test_t2_unauthorized_mint_native_token_fails(self, alice, state):
        """Sender cannot mint native currency ('') using flags=ALLOW_MINT without native balance."""
        alice_sk, alice_pk = alice
        state.credit_mining_reward(alice_pk, 10)  # Only 10 native coins

        # Alice attempts to mint 1,000,000 native coins
        tx = build_and_sign_tx(alice_sk, alice_pk, alice_pk, amount={"": 1000000}, nonce=0, flags=ALLOW_MINT)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t2_unauthorized_mint_prefix_substring_mismatch(self, alice, bob, state):
        """Namespace check must be exact prefix '<sender>.', not a partial substring."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        # Appends suffix to alice_pk before dot
        imposter_token = f"{alice_pk}fake.COIN"

        state.credit_mining_reward(alice_pk, 10000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={imposter_token: 100}, nonce=0, flags=ALLOW_MINT)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t2_unauthorized_mint_dot_missing(self, alice, bob, state):
        """Token lacking dot separator after sender address fails namespace check."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        no_dot_token = f"{alice_pk}NODOT"

        state.credit_mining_reward(alice_pk, 10000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={no_dot_token: 100}, nonce=0, flags=ALLOW_MINT)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t2_unauthorized_mint_in_multi_token_bundle_fails_atomically(self, alice, bob, state):
        """If Alice bundles her own token with Bob's token under ALLOW_MINT, entire tx fails."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        valid_tok = f"{alice_pk}.VALID"
        invalid_tok = f"{bob_pk}.INVALID"

        state.credit_mining_reward(alice_pk, 10000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={valid_tok: 100, invalid_tok: 50},
                               nonce=0, flags=ALLOW_MINT)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None
        assert state.get_account(bob_pk)["balances"].get(valid_tok, 0) == 0


class TestTier2InvalidFlagsRejected:
    """Tier 2 Corner Case 8: Invalid flags format rejected as MALFORMED."""

    def test_t2_string_flag_rejected(self, alice, bob, state):
        """String flag is rejected as MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={"": 10}, nonce=0, flags="1")
        assert state._amounts_well_formed(tx) is False

    def test_t2_negative_flag_rejected(self, alice, bob, state):
        """Negative flag integer is rejected as MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={"": 10}, nonce=0, flags=-1)
        assert state._amounts_well_formed(tx) is False

    def test_t2_boolean_flag_rejected(self, alice, bob, state):
        """Boolean flag (True/False) is rejected as MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={"": 10}, nonce=0, flags=True)
        assert state._amounts_well_formed(tx) is False

    def test_t2_none_flag_rejected(self, alice, bob, state):
        """None flag is rejected as MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={"": 10}, nonce=0, flags=None)
        assert state._amounts_well_formed(tx) is False

    def test_t2_float_flag_rejected(self, alice, bob, state):
        """Float flag is rejected as MALFORMED."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        tx = Transaction(alice_pk, bob_pk, amount={"": 10}, nonce=0, flags=2.5)
        assert state._amounts_well_formed(tx) is False


# ===========================================================================
# TIER 3: CROSS-FEATURE COMBINATIONS
# ===========================================================================

class TestTier3MintAndTransfer:
    """Tier 3 Pairwise 1: Minting combined with regular asset transfers."""

    def test_t3_mint_and_native_transfer_in_single_tx(self, alice, bob, state):
        """Single transaction mints custom asset (flags=1) and transfers pre-existing native currency."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.AIRDROP"

        state.credit_mining_reward(alice_pk, 5000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={"": 100, token: 500},
                               nonce=0, gas_limit=20, fee_per_gas=1, flags=ALLOW_MINT)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][""] == 100
        assert state.get_account(bob_pk)["balances"][token] == 500
        assert state.get_account(alice_pk)["balances"][""] == 5000 - 100 - 20

    def test_t3_mint_and_transfer_existing_foreign_asset(self, alice, bob, charlie, state):
        """Alice mints her own token and forwards pre-owned foreign token in same tx."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        _, charlie_pk = charlie

        foreign_token = f"{charlie_pk}.COIN"
        alice_token = f"{alice_pk}.REWARD"

        state.credit_mining_reward(alice_pk, 20000)
        # Alice already owns foreign token
        state.get_account(alice_pk)["balances"][foreign_token] = 80

        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={alice_token: 200, foreign_token: 30},
                               nonce=0, flags=ALLOW_MINT)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][alice_token] == 200
        assert state.get_account(bob_pk)["balances"][foreign_token] == 30
        assert state.get_account(alice_pk)["balances"][foreign_token] == 50

    def test_t3_mint_and_transfer_fails_if_insufficient_native_for_transfer(self, alice, bob, state):
        """If Alice has insufficient native currency to cover transfer portion, minting also aborts."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.FAILS"

        state.credit_mining_reward(alice_pk, 50)
        # 100 native requested, only 50 available
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={"": 100, token: 1000},
                               nonce=0, flags=ALLOW_MINT)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None
        assert state.get_account(bob_pk)["balances"].get(token, 0) == 0

    def test_t3_mint_and_transfer_fails_if_insufficient_foreign_asset(self, alice, bob, charlie, state):
        """If Alice owns 10 foreign tokens but tries to transfer 50 while minting, transaction aborts."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        _, charlie_pk = charlie
        foreign_tok = f"{charlie_pk}.C"
        alice_tok = f"{alice_pk}.A"

        state.credit_mining_reward(alice_pk, 10000)
        state.get_account(alice_pk)["balances"][foreign_tok] = 10

        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={alice_tok: 100, foreign_tok: 50},
                               nonce=0, flags=ALLOW_MINT)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None
        assert state.get_account(bob_pk)["balances"].get(alice_tok, 0) == 0


class TestTier3BurnAndTransfer:
    """Tier 3 Pairwise 2: Burning combined with asset transfers."""

    def test_t3_burn_native_and_burn_custom_token_simultaneously(self, alice, state):
        """Sending both native currency and custom tokens to BURN_ADDRESS with flags=2 burns both."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.DUALBURN"

        state.credit_mining_reward(alice_pk, 10000)
        state.get_account(alice_pk)["balances"][token] = 500

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={"": 300, token: 200},
                               nonce=0, gas_limit=10, fee_per_gas=1, flags=ALLOW_BURN)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(alice_pk)["balances"][""] == 10000 - 300 - 10
        assert state.get_account(alice_pk)["balances"][token] == 300
        burn_acc = state.get_account(BURN_ADDRESS)
        assert burn_acc["balances"][""] == 300
        assert burn_acc["balances"][token] == 200

    def test_t3_flags_2_to_normal_recipient_acts_as_transfer(self, alice, bob, state):
        """flags=ALLOW_BURN (2) sent to a regular user acts as a standard transfer without errors."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.SAFE"

        state.credit_mining_reward(alice_pk, 20000)
        state.get_account(alice_pk)["balances"][token] = 300

        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={token: 100}, nonce=0, flags=ALLOW_BURN)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][token] == 100
        assert state.get_account(alice_pk)["balances"][token] == 200

    def test_t3_burn_fails_if_insufficient_balance_of_any_burned_token(self, alice, state):
        """Burn transaction fails if sender does not possess enough of any token being burned."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.POOR"

        state.credit_mining_reward(alice_pk, 10000)
        state.get_account(alice_pk)["balances"][token] = 50

        # Attempt to burn 100
        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={token: 100}, nonce=0, flags=ALLOW_BURN)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None
        assert state.get_account(alice_pk)["balances"][token] == 50

    def test_t3_burn_multiple_custom_tokens_simultaneously(self, alice, state):
        """Multiple custom assets can be burned in one transaction."""
        alice_sk, alice_pk = alice
        t1, t2 = f"{alice_pk}.B1", f"{alice_pk}.B2"

        state.credit_mining_reward(alice_pk, 20000)
        state.get_account(alice_pk)["balances"][t1] = 400
        state.get_account(alice_pk)["balances"][t2] = 600

        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={t1: 150, t2: 250}, nonce=0, flags=ALLOW_BURN)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(alice_pk)["balances"][t1] == 250
        assert state.get_account(alice_pk)["balances"][t2] == 350
        assert state.get_account(BURN_ADDRESS)["balances"][t1] == 150
        assert state.get_account(BURN_ADDRESS)["balances"][t2] == 250


class TestTier3MintAndBurnFlag3:
    """Tier 3 Pairwise 3: Combined flags=3 (ALLOW_MINT | ALLOW_BURN)."""

    def test_t3_flag_3_mint_directly_to_burn_address(self, alice, state):
        """Alice can mint new supply directly into the burn address using flags=3."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.BURNONCREATE"

        state.credit_mining_reward(alice_pk, 20000)
        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={token: 777}, nonce=0,
                               flags=ALLOW_MINT | ALLOW_BURN)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(BURN_ADDRESS)["balances"][token] == 777
        assert state.get_account(alice_pk)["balances"].get(token, 0) == 0

    def test_t3_flag_3_mint_custom_and_burn_native(self, alice, state):
        """With flags=3, Alice can burn existing native currency while minting custom token to burn address."""
        alice_sk, alice_pk = alice
        token = f"{alice_pk}.COMBO"

        state.credit_mining_reward(alice_pk, 10000)
        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={"": 150, token: 500},
                               nonce=0, gas_limit=10, fee_per_gas=1, flags=3)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(alice_pk)["balances"][""] == 10000 - 150 - 10
        assert state.get_account(BURN_ADDRESS)["balances"][""] == 150
        assert state.get_account(BURN_ADDRESS)["balances"][token] == 500

    def test_t3_flag_3_unauthorized_mint_to_burn_address_fails(self, alice, bob, state):
        """flags=3 still rejects unauthorized minting of Bob's tokens even to BURN_ADDRESS."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        foreign_tok = f"{bob_pk}.FORBIDDEN"

        state.credit_mining_reward(alice_pk, 10000)
        tx = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={foreign_tok: 100}, nonce=0, flags=3)
        status, receipt = state.validate_and_apply_with_status(tx)

        assert status == ValidationStatus.FAILED
        assert receipt is None

    def test_t3_flag_3_to_normal_recipient_mints_and_transfers(self, alice, bob, state):
        """flags=3 to regular recipient allows minting without requiring burn address."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.MINTNORMAL"

        state.credit_mining_reward(alice_pk, 20000)
        tx = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={token: 250}, nonce=0, flags=3)
        receipt = state.apply_transaction(tx)

        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][token] == 250


class TestTier3ContractCrossCallsMultiAsset:
    """Tier 3 Pairwise 4: Contract-to-contract calls passing custom tokens."""

    def test_t3_contract_calls_contract_with_custom_token(self, alice, state):
        """Contract A calls Contract B passing custom token amount."""
        alice_sk, alice_pk = alice
        tok = f"{alice_pk}.XCALL"

        state.credit_mining_reward(alice_pk, 200000)
        state.get_account(alice_pk)["balances"][tok] = 500

        # B records sender and received value
        code_b = """
storage['from'] = msg['sender']
storage['val'] = msg['value']
"""
        tx_b = build_and_sign_tx(alice_sk, alice_pk, None, amount=0, nonce=0, data=code_b,
                                 gas_limit=50000, fee_per_gas=1)
        caddr_b = state.apply_transaction(tx_b).contract_address

        # A forwards token to B
        code_a = f"""
target = "{caddr_b}"
asset = "{tok}"
call_contract(target, "hello", {{asset: 40}})
"""
        tx_a = build_and_sign_tx(alice_sk, alice_pk, None, amount={tok: 100}, nonce=1, data=code_a,
                                 gas_limit=50000, fee_per_gas=1)
        caddr_a = state.apply_transaction(tx_a).contract_address

        call_tx = build_and_sign_tx(alice_sk, alice_pk, caddr_a, amount=0, nonce=2, data="run",
                                    gas_limit=80000, fee_per_gas=1)
        receipt = state.apply_transaction(call_tx)

        assert receipt.status == 1
        assert state.get_account(caddr_b)["storage"]["from"] == caddr_a
        assert state.get_account(caddr_b)["storage"]["val"] == {tok: 40}
        assert state.get_account(caddr_b)["balances"][tok] == 40
        assert state.get_account(caddr_a)["balances"][tok] == 60

    def test_t3_contract_calls_contract_with_multi_tokens(self, alice, state):
        """Contract A calls Contract B passing both native currency and custom token."""
        alice_sk, alice_pk = alice
        tok = f"{alice_pk}.DUALX"

        state.credit_mining_reward(alice_pk, 200000)
        state.get_account(alice_pk)["balances"][tok] = 500

        code_b = "storage['got'] = msg['value']"
        caddr_b = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=0, nonce=0, data=code_b, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        code_a = f"""
target = "{caddr_b}"
asset = "{tok}"
call_contract(target, "dual", {{"": 15, asset: 35}})
"""
        caddr_a = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount={"": 50, tok: 50}, nonce=1, data=code_a,
                              gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr_a, amount=0, nonce=2, data="go", gas_limit=80000, fee_per_gas=1)
        )
        assert receipt.status == 1
        assert state.get_account(caddr_b)["storage"]["got"] == {"": 15, tok: 35}
        assert state.get_account(caddr_b)["balances"][""] == 15
        assert state.get_account(caddr_b)["balances"][tok] == 35

    def test_t3_contract_calls_contract_insufficient_balance_reverts(self, alice, state):
        """Cross-contract call reverts if caller contract has insufficient balance of custom token."""
        alice_sk, alice_pk = alice
        tok = f"{alice_pk}.SHORTX"

        state.credit_mining_reward(alice_pk, 100000)
        code_b = "storage['b'] = 1"
        caddr_b = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=0, nonce=0, data=code_b, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        # Contract A has only 10 tokens, tries to call B with 50
        code_a = f"""
target = "{caddr_b}"
asset = "{tok}"
call_contract(target, "overdraft", {{asset: 50}})
"""
        state.get_account(alice_pk)["balances"][tok] = 10
        caddr_a = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount={tok: 10}, nonce=1, data=code_a, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr_a, amount=0, nonce=2, data="go", gas_limit=80000, fee_per_gas=1)
        )
        assert receipt.status == 0  # Reverts atomically

    def test_t3_chained_contract_calls_forwarding_custom_tokens(self, alice, state):
        """Chained call: Contract A calls B, B forwards custom token to C."""
        alice_sk, alice_pk = alice
        tok = f"{alice_pk}.CHAIN"

        state.credit_mining_reward(alice_pk, 300000)
        state.get_account(alice_pk)["balances"][tok] = 500

        # C: end receiver
        code_c = "storage['c_val'] = msg['value']"
        caddr_c = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=0, nonce=0, data=code_c, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        # B: middleman forwards part to C
        code_b = f"""
dest = "{caddr_c}"
asset = "{tok}"
storage['b_received'] = msg['value']
call_contract(dest, "forward", {{asset: 15}})
"""
        caddr_b = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=0, nonce=1, data=code_b, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        # A: initial sender
        code_a = f"""
dest = "{caddr_b}"
asset = "{tok}"
call_contract(dest, "start", {{asset: 40}})
"""
        caddr_a = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount={tok: 100}, nonce=2, data=code_a, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr_a, amount=0, nonce=3, data="start", gas_limit=100000, fee_per_gas=1)
        )
        assert receipt.status == 1
        assert state.get_account(caddr_a)["balances"][tok] == 60
        assert state.get_account(caddr_b)["balances"][tok] == 25  # 40 received - 15 forwarded
        assert state.get_account(caddr_c)["balances"][tok] == 15


class TestTier3ContractTransferOutDictAndInt:
    """Tier 3 Pairwise 5: Contract transfer_out accepting dict and int."""

    def test_t3_contract_transfer_out_int_native(self, alice, bob, state):
        """Contract calls transfer_out(addr, int) transfers native currency."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 100000)
        code = f"""
target = "{bob_pk}"
transfer_out(target, 80)
"""
        caddr = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=100, nonce=0, data=code, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=1, data="go", gas_limit=50000, fee_per_gas=1)
        )
        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][""] == 80
        assert state.get_account(caddr)["balances"][""] == 20

    def test_t3_contract_transfer_out_dict_native(self, alice, bob, state):
        """Contract calls transfer_out(addr, {'': int}) transfers native currency."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob

        state.credit_mining_reward(alice_pk, 100000)
        code = f"""
target = "{bob_pk}"
transfer_out(target, {{"": 65}})
"""
        caddr = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=100, nonce=0, data=code, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=1, data="go", gas_limit=50000, fee_per_gas=1)
        )
        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][""] == 65
        assert state.get_account(caddr)["balances"][""] == 35

    def test_t3_contract_transfer_out_dict_custom_token(self, alice, bob, state):
        """Contract calls transfer_out(addr, {token: int}) transfers custom token."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.OUT"

        state.credit_mining_reward(alice_pk, 100000)
        state.get_account(alice_pk)["balances"][token] = 500

        code = f"""
target = "{bob_pk}"
tok = "{token}"
transfer_out(target, {{tok: 120}})
"""
        caddr = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount={token: 200}, nonce=0, data=code,
                              gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=1, data="go", gas_limit=50000, fee_per_gas=1)
        )
        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][token] == 120
        assert state.get_account(caddr)["balances"][token] == 80

    def test_t3_contract_transfer_out_multi_token_dict(self, alice, bob, state):
        """Contract calls transfer_out(addr, {'': native, token: custom}) transfers both simultaneously."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        token = f"{alice_pk}.BUNDLE"

        state.credit_mining_reward(alice_pk, 100000)
        state.get_account(alice_pk)["balances"][token] = 500

        code = f"""
target = "{bob_pk}"
tok = "{token}"
transfer_out(target, {{"": 30, tok: 90}})
"""
        caddr = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount={"": 100, token: 200}, nonce=0, data=code,
                              gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=1, data="go", gas_limit=50000, fee_per_gas=1)
        )
        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][""] == 30
        assert state.get_account(bob_pk)["balances"][token] == 90
        assert state.get_account(caddr)["balances"][""] == 70
        assert state.get_account(caddr)["balances"][token] == 110

    def test_t3_contract_multiple_transfer_outs_mixed_types(self, alice, bob, charlie, state):
        """Contract performs multiple transfer_out calls with int and dict in sequence."""
        alice_sk, alice_pk = alice
        _, bob_pk = bob
        _, charlie_pk = charlie
        token = f"{alice_pk}.MULTI"

        state.credit_mining_reward(alice_pk, 100000)
        state.get_account(alice_pk)["balances"][token] = 500

        code = f"""
transfer_out("{bob_pk}", 40)
transfer_out("{charlie_pk}", {{"{token}": 85}})
"""
        caddr = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount={"": 100, token: 200}, nonce=0, data=code,
                              gas_limit=50000, fee_per_gas=1)
        ).contract_address

        receipt = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=1, data="go", gas_limit=50000, fee_per_gas=1)
        )
        assert receipt.status == 1
        assert state.get_account(bob_pk)["balances"][""] == 40
        assert state.get_account(charlie_pk)["balances"][token] == 85
        assert state.get_account(caddr)["balances"][""] == 60
        assert state.get_account(caddr)["balances"][token] == 115


# ===========================================================================
# TIER 4: REAL-WORLD APPLICATION SCENARIOS
# ===========================================================================

class TestTier4RealWorldScenarios:
    """Tier 4: Realistic end-to-end decentralized application workflows."""

    def test_scenario_multi_token_dex(self, alice, bob, state):
        """
        Scenario 1: Multi-Token Decentralized Exchange (DEX).
        Alice deploys DEX contract and provides initial liquidity (Native + USD token).
        Bob sends native currency to DEX and swaps it for USD token at a fixed 1:2 rate.
        """
        alice_sk, alice_pk = alice
        bob_sk, bob_pk = bob
        usd_token = f"{alice_pk}.USD"

        # 1. Fund Alice with native and mint USD tokens
        state.credit_mining_reward(alice_pk, 200000)
        mint_tx = build_and_sign_tx(alice_sk, alice_pk, alice_pk, amount={usd_token: 10000}, nonce=0, flags=ALLOW_MINT)
        assert state.apply_transaction(mint_tx).status == 1

        # 2. Deploy DEX Contract
        dex_code = f"""
msg_val = msg.get('value', {{}})
native_in = msg_val.get('', 0)
usd_tok = "{usd_token}"

if msg.get('data') == 'swap':
    if native_in <= 0:
        raise Exception("Must send native coins to swap")
    usd_out = native_in + native_in
    transfer_out(msg['sender'], {{usd_tok: usd_out}})
    storage['total_native'] = storage.get('total_native', 0) + native_in
"""
        # Deploy with initial liquidity: 1,000 native and 5,000 USD tokens
        deploy_tx = build_and_sign_tx(alice_sk, alice_pk, None, amount={"": 1000, usd_token: 5000},
                                      nonce=1, data=dex_code, gas_limit=50000, fee_per_gas=1)
        dex_receipt = state.apply_transaction(deploy_tx)
        assert dex_receipt.status == 1
        dex_addr = dex_receipt.contract_address

        # 3. Bob swaps 100 native currency for 200 USD tokens
        state.credit_mining_reward(bob_pk, 5000)
        bob_swap_tx = build_and_sign_tx(bob_sk, bob_pk, dex_addr, amount={"": 100}, nonce=0,
                                        data="swap", gas_limit=4000, fee_per_gas=1)
        swap_receipt = state.apply_transaction(bob_swap_tx)
        assert swap_receipt.status == 1

        # 4. Verifications
        assert state.get_account(bob_pk)["balances"][usd_token] == 200
        # Bob spent 100 native + gas fee
        assert state.get_account(bob_pk)["balances"][""] == 5000 - 100 - swap_receipt.gas_used
        # DEX received 100 native and transferred out 200 USD tokens
        assert state.get_account(dex_addr)["balances"][""] == 1000 + 100
        assert state.get_account(dex_addr)["balances"][usd_token] == 5000 - 200

    def test_scenario_token_mint_and_distribute(self, alice, bob, charlie, dave, state):
        """
        Scenario 2: Token Mint & Distribution Pipeline.
        Organization (Alice) mints supply of governance tokens and distributes
        allocations to core team (Bob), advisor (Charlie), and community (Dave).
        Team member then makes downstream transfers.
        """
        alice_sk, alice_pk = alice
        bob_sk, bob_pk = bob
        _, charlie_pk = charlie
        _, dave_pk = dave
        gov_token = f"{alice_pk}.GOV"

        state.credit_mining_reward(alice_pk, 500000)

        # 1. Alice mints 1,000,000 tokens to herself
        mint_tx = build_and_sign_tx(alice_sk, alice_pk, alice_pk, amount={gov_token: 1000000}, nonce=0, flags=ALLOW_MINT)
        assert state.apply_transaction(mint_tx).status == 1
        assert state.get_account(alice_pk)["balances"][gov_token] == 1000000

        # 2. Distribute allocations
        # To Bob: 100,000
        tx_bob = build_and_sign_tx(alice_sk, alice_pk, bob_pk, amount={gov_token: 100000}, nonce=1)
        assert state.apply_transaction(tx_bob).status == 1

        # To Charlie: 50,000
        tx_charlie = build_and_sign_tx(alice_sk, alice_pk, charlie_pk, amount={gov_token: 50000}, nonce=2)
        assert state.apply_transaction(tx_charlie).status == 1

        # To Dave: 25,000
        tx_dave = build_and_sign_tx(alice_sk, alice_pk, dave_pk, amount={gov_token: 25000}, nonce=3)
        assert state.apply_transaction(tx_dave).status == 1

        # 3. Bob forwards 10,000 of his tokens to Charlie
        state.credit_mining_reward(bob_pk, 20000)
        bob_to_charlie = build_and_sign_tx(bob_sk, bob_pk, charlie_pk, amount={gov_token: 10000}, nonce=0)
        assert state.apply_transaction(bob_to_charlie).status == 1

        # 4. Verifications
        assert state.get_account(alice_pk)["balances"][gov_token] == 825000
        assert state.get_account(bob_pk)["balances"][gov_token] == 90000
        assert state.get_account(charlie_pk)["balances"][gov_token] == 60000
        assert state.get_account(dave_pk)["balances"][gov_token] == 25000

        total_circulating = (
            state.get_account(alice_pk)["balances"][gov_token] +
            state.get_account(bob_pk)["balances"][gov_token] +
            state.get_account(charlie_pk)["balances"][gov_token] +
            state.get_account(dave_pk)["balances"][gov_token]
        )
        assert total_circulating == 1000000

    def test_scenario_treasury_burning(self, alice, state):
        """
        Scenario 3: Treasury Deflationary Burning Mechanism.
        Protocol treasury executes scheduled burns to permanently decrease token supply.
        """
        alice_sk, alice_pk = alice
        fee_token = f"{alice_pk}.FEE"

        state.credit_mining_reward(alice_pk, 50000)
        # Mint initial treasury balance
        mint_tx = build_and_sign_tx(alice_sk, alice_pk, alice_pk, amount={fee_token: 10000}, nonce=0, flags=ALLOW_MINT)
        assert state.apply_transaction(mint_tx).status == 1

        # Burn Cycle 1: Burn 2,500 tokens
        burn1 = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={fee_token: 2500}, nonce=1, flags=ALLOW_BURN)
        receipt1 = state.apply_transaction(burn1)
        assert receipt1.status == 1

        # Burn Cycle 2: Burn another 2,500 tokens
        burn2 = build_and_sign_tx(alice_sk, alice_pk, BURN_ADDRESS, amount={fee_token: 2500}, nonce=2, flags=ALLOW_BURN)
        receipt2 = state.apply_transaction(burn2)
        assert receipt2.status == 1

        # Verify treasury and burn address balances
        assert state.get_account(alice_pk)["balances"][fee_token] == 5000
        assert state.get_account(BURN_ADDRESS)["balances"][fee_token] == 5000

    def test_scenario_fee_in_native_currency_moving_tokens(self, alice, bob, charlie, state):
        """
        Scenario 4: Gas fees paid in native currency while moving custom tokens.
        Alice pays for multiple transfers of Bob's stablecoin entirely in native currency.
        """
        alice_sk, alice_pk = alice
        bob_sk, bob_pk = bob
        _, charlie_pk = charlie
        stable_token = f"{bob_pk}.USDT"

        state.credit_mining_reward(alice_pk, 10000)
        state.credit_mining_reward(bob_pk, 10000)

        # Bob mints stablecoins and sends 5,000 to Alice
        state.apply_transaction(build_and_sign_tx(bob_sk, bob_pk, alice_pk, amount={stable_token: 5000},
                                                  nonce=0, flags=ALLOW_MINT))

        # Alice executes 3 successive merchant payments of 1,000 USDT to Charlie
        total_gas_debited = 0
        for i in range(3):
            tx = build_and_sign_tx(alice_sk, alice_pk, charlie_pk, amount={stable_token: 1000}, nonce=i,
                                   gas_limit=50, fee_per_gas=2)
            receipt = state.apply_transaction(tx)
            assert receipt.status == 1
            total_gas_debited += (50 * 2)

        # Verify Alice's stablecoin decreased by exactly 3,000, and native decreased by gas only
        assert state.get_account(alice_pk)["balances"][stable_token] == 2000
        assert state.get_account(alice_pk)["balances"][""] == 10000 - total_gas_debited

        # Charlie received exactly 3,000 stablecoins and 0 native coins
        assert state.get_account(charlie_pk)["balances"][stable_token] == 3000
        assert state.get_account(charlie_pk)["balances"].get("", 0) == 0

    def test_scenario_crowdfunding_multi_token(self, alice, bob, charlie, state):
        """
        Scenario 5: Multi-Token Crowdfunding Campaign Contract.
        Contract accepts contributions in both native currency and custom token.
        Once target is reached, owner claims all raised multi-asset funds.
        """
        alice_sk, alice_pk = alice
        bob_sk, bob_pk = bob
        charlie_sk, charlie_pk = charlie
        token = f"{alice_pk}.CROWD"

        state.credit_mining_reward(alice_pk, 200000)
        state.credit_mining_reward(bob_pk, 100000)
        state.credit_mining_reward(charlie_pk, 100000)

        # Alice mints tokens and gives some to Charlie to contribute
        state.apply_transaction(build_and_sign_tx(alice_sk, alice_pk, charlie_pk, amount={token: 1000},
                                                  nonce=0, flags=ALLOW_MINT))

        # 1. Deploy Crowdfunding Contract
        cf_code = f"""
creator = "{alice_pk}"
tok = "{token}"

if msg.get('data') == 'contribute':
    val = msg.get('value', {{}})
    storage['native_raised'] = storage.get('native_raised', 0) + val.get('', 0)
    storage['tok_raised'] = storage.get('tok_raised', 0) + val.get(tok, 0)

elif msg.get('data') == 'claim':
    if msg['sender'] != creator:
        raise Exception("Only creator can claim")
    native_amt = storage.get('native_raised', 0)
    tok_amt = storage.get('tok_raised', 0)
    transfer_out(creator, {{"": native_amt, tok: tok_amt}})
"""
        caddr = state.apply_transaction(
            build_and_sign_tx(alice_sk, alice_pk, None, amount=0, nonce=1, data=cf_code, gas_limit=50000, fee_per_gas=1)
        ).contract_address

        # 2. Bob contributes 200 native currency
        tx_bob = build_and_sign_tx(bob_sk, bob_pk, caddr, amount={"": 200}, nonce=0,
                                   data="contribute", gas_limit=50000, fee_per_gas=1)
        assert state.apply_transaction(tx_bob).status == 1

        # 3. Charlie contributes 600 custom tokens
        tx_charlie = build_and_sign_tx(charlie_sk, charlie_pk, caddr, amount={token: 600}, nonce=0,
                                       data="contribute", gas_limit=50000, fee_per_gas=1)
        assert state.apply_transaction(tx_charlie).status == 1

        # 4. Alice claims funds
        claim_tx = build_and_sign_tx(alice_sk, alice_pk, caddr, amount=0, nonce=2,
                                     data="claim", gas_limit=80000, fee_per_gas=1)
        claim_receipt = state.apply_transaction(claim_tx)
        assert claim_receipt.status == 1

        # 5. Verifications
        assert state.get_account(alice_pk)["balances"][token] == 600
        assert state.get_account(caddr)["balances"].get("", 0) == 0
        assert state.get_account(caddr)["balances"].get(token, 0) == 0
        assert state.get_account(caddr)["storage"]["native_raised"] == 200
        assert state.get_account(caddr)["storage"]["tok_raised"] == 600
