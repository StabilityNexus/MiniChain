"""End-to-end tests for examples/batch_auction.py, run through State.apply_transaction."""
import os
import random

import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain import State, Transaction

CODE_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "batch_auction.py")
with open(CODE_PATH) as f:
    CODE = f.read()


def keypair():
    sk = SigningKey.generate()
    return sk, sk.verify_key.encode(encoder=HexEncoder).decode()


class Market:
    """Deploys the contract, runs `init`, and sends calls as signed transactions."""

    def __init__(self, state, code=CODE):
        self.state = state
        self.deployer = keypair()
        self.gold = f"{self.deployer[1]}.GOLD"
        receipt = self.send(self.deployer, None, 0, code)
        assert receipt.status == 1, receipt.error_message
        self.address = receipt.contract_address
        assert self.call(self.deployer, f"init:{self.gold}:").status == 1

    def send(self, kp, receiver, amount, data):
        sk, pk = kp
        tx = Transaction(pk, receiver, amount=amount, nonce=self.state.get_account(pk)["nonce"],
                         data=data, gas_limit=10_000_000, fee_per_gas=0)
        tx.sign(sk)
        return self.state.apply_transaction(tx)

    def call(self, kp, payload, amount=0):
        return self.send(kp, self.address, amount, payload)

    def trader(self, gold=0, coins=0):
        kp = keypair()
        balances = self.state.get_account(kp[1])["balances"]
        balances[self.gold] = gold
        balances[""] = coins
        return kp

    def balance(self, kp_or_addr, asset):
        addr = kp_or_addr if isinstance(kp_or_addr, str) else kp_or_addr[1]
        return self.state.get_account(addr)["balances"].get(asset, 0)

    @property
    def storage(self):
        return self.state.get_account(self.address)["storage"]


@pytest.fixture
def market():
    return Market(State())


def test_crossing_book_clears_at_one_price(market):
    alice = market.trader(gold=10)
    dave = market.trader(gold=10)
    bob = market.trader(coins=56)
    carol = market.trader(coins=54)

    assert market.call(alice, "sell:10:5", {market.gold: 10}).status == 1
    assert market.call(dave, "sell:10:8", {market.gold: 10}).status == 1
    assert market.call(bob, "buy:8:7", {"": 56}).status == 1
    assert market.call(carol, "buy:6:9", {"": 54}).status == 1
    assert market.call(bob, "clear").status == 1

    # At 5: demand 14, supply 10 -> 10 GOLD trade, the most of any price.
    assert market.storage["last_price"] == 5 and market.storage["last_volume"] == 10
    # Carol (best bid) is filled first, Bob gets the rest; both pay 5 each.
    assert market.balance(carol, market.gold) == 6 and market.balance(carol, "") == 54 - 30
    assert market.balance(bob, market.gold) == 4 and market.balance(bob, "") == 56 - 20
    # Alice sold everything at 5; Dave's ask of 8 did not cross and is refunded.
    assert market.balance(alice, "") == 50 and market.balance(alice, market.gold) == 0
    assert market.balance(dave, market.gold) == 10 and market.balance(dave, "") == 0
    # The contract holds nothing once the batch is settled.
    assert market.balance(market.address, market.gold) == 0
    assert market.balance(market.address, "") == 0
    assert market.storage["orders"] == {}


def test_book_that_does_not_cross_refunds_everyone(market):
    seller = market.trader(gold=5)
    buyer = market.trader(coins=45)
    market.call(seller, "sell:5:10", {market.gold: 5})
    market.call(buyer, "buy:5:9", {"": 45})
    assert market.call(buyer, "clear").status == 1

    assert market.storage["last_volume"] == 0
    assert market.balance(seller, market.gold) == 5
    assert market.balance(buyer, "") == 45


def test_marginal_seller_is_partly_filled(market):
    seller = market.trader(gold=10)
    buyer = market.trader(coins=40)
    market.call(seller, "sell:10:4", {market.gold: 10})
    market.call(buyer, "buy:4:10", {"": 40})
    market.call(buyer, "clear")

    assert market.storage["last_price"] == 4 and market.storage["last_volume"] == 4
    assert market.balance(seller, "") == 16 and market.balance(seller, market.gold) == 6
    assert market.balance(buyer, market.gold) == 4 and market.balance(buyer, "") == 40 - 16


def test_only_the_owner_can_cancel(market):
    owner = market.trader(coins=20)
    other = market.trader()
    market.call(owner, "buy:2:10", {"": 20})
    assert market.call(other, "cancel:0").status == 0
    assert market.call(owner, "cancel:0").status == 1
    assert market.balance(owner, "") == 20
    assert market.storage["orders"] == {}


@pytest.mark.parametrize("payload, value", [
    ("buy:2:10", {"": 19}),            # escrow short by one
    ("buy:2:10", {"": 20, "x": 1}),    # an extra asset
    ("sell:2:10", {"": 2}),            # wrong asset for a sell
    ("sell:0:10", {}),                 # zero amount
    ("sell:abc:10", {}),               # not a number
])
def test_bad_orders_are_rejected_and_funds_stay_put(market, payload, value):
    trader = market.trader(gold=5, coins=50)
    trader_acc = market.state.get_account(trader[1])["balances"]
    trader_acc["x"] = 1
    before = dict(trader_acc)
    assert market.call(trader, payload, value).status == 0
    assert market.state.get_account(trader[1])["balances"] == before
    assert market.storage["orders"] == {}


def test_market_needs_init_once():
    market = Market(State())
    assert market.call(market.deployer, f"init:{market.gold}:").status == 0


def test_order_cap_is_enforced():
    market = Market(State(), code=CODE.replace("MAX_ORDERS = 100", "MAX_ORDERS = 2"))
    trader = market.trader(coins=30)
    assert market.call(trader, "buy:1:10", {"": 10}).status == 1
    assert market.call(trader, "buy:1:10", {"": 10}).status == 1
    assert market.call(trader, "buy:1:10", {"": 10}).status == 0
    assert market.call(trader, "clear").status == 1
    assert market.call(trader, "buy:1:10", {"": 10}).status == 1


def test_random_books_conserve_both_assets(market):
    rng = random.Random(42)
    traders = [market.trader(gold=50, coins=500) for _ in range(4)]
    total_before = {asset: sum(market.balance(t, asset) for t in traders) for asset in (market.gold, "")}

    for i in range(8):
        t = traders[i % 4]
        amount, price = rng.randint(1, 10), rng.randint(1, 9)
        if rng.random() < 0.5:
            receipt = market.call(t, f"buy:{amount}:{price}", {"": amount * price})
        else:
            receipt = market.call(t, f"sell:{amount}:{price}", {market.gold: amount})
        assert receipt.status == 1, receipt.error_message
    assert market.call(traders[0], "clear").status == 1

    for asset, before in total_before.items():
        assert sum(market.balance(t, asset) for t in traders) == before
        assert market.balance(market.address, asset) == 0
