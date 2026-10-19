import aiohttp
import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

import main
from minichain import Transaction, Transfer
from minichain.chain import Blockchain
from minichain.mempool import Mempool
from minichain.p2p import P2PNetwork
from minichain.rpc import JSONRPCServer


def keypair():
    sk = SigningKey.generate()
    return sk, sk.verify_key.encode(encoder=HexEncoder).decode()


def swap(alice, bob, a_nonce=0, b_nonce=0, fee=1, ts=None, gas_limit=0):
    """Two-signer atomic transfer: alice -> bob 5, bob -> alice 3."""
    (a_sk, a), (b_sk, b) = alice, bob
    tx = Transaction(
        transfers=[Transfer(a, b, 5), Transfer(b, a, 3)],
        nonces={a: a_nonce, b: b_nonce},
        fee_per_gas=fee,
        gas_limit=gas_limit,
        timestamp=ts,
    )
    tx.sign(a_sk)
    tx.sign(b_sk)
    return tx


def single(kp, nonce, fee=1, ts=None):
    sk, pk = kp
    tx = Transaction(pk, "r" * 40, amount=1, nonce=nonce, fee_per_gas=fee, timestamp=ts)
    tx.sign(sk)
    return tx


@pytest.fixture
def alice():
    return keypair()


@pytest.fixture
def bob():
    return keypair()


def test_partially_signed_multi_party_tx_rejected(alice, bob):
    (a_sk, a), (_, b) = alice, bob
    tx = Transaction(transfers=[Transfer(a, b, 5), Transfer(b, a, 3)], nonces={a: 0, b: 0})
    tx.sign(a_sk)
    assert not Mempool().add_transaction(tx)


def test_replacement_conflicts_on_any_signer(alice, bob):
    pool = Mempool()
    assert pool.add_transaction(swap(alice, bob, fee=10, ts=1_000_000_000_000))
    # Single-signer tx from bob re-using bob's nonce 0 conflicts with the swap.
    assert not pool.add_transaction(single(bob, 0, fee=10, ts=1_000_000_000_001))  # fee too low
    assert pool.add_transaction(single(bob, 0, fee=20, ts=1_000_000_000_002))
    assert len(pool) == 1


def test_replacement_must_outbid_every_conflict(alice, bob):
    pool = Mempool()
    assert pool.add_transaction(single(alice, 0, fee=10, ts=1_000_000_000_000))
    assert pool.add_transaction(single(bob, 0, fee=50, ts=1_000_000_000_000))
    # Beats alice's tx but not bob's, so it must be rejected.
    assert not pool.add_transaction(swap(alice, bob, fee=20, ts=1_000_000_000_001))
    assert len(pool) == 2
    assert pool.add_transaction(swap(alice, bob, fee=60, ts=1_000_000_000_002))
    assert len(pool) == 1


def test_nonce_order_respected_across_signers(alice, bob):
    pool = Mempool()
    low = single(alice, 1, fee=1)
    pool.add_transaction(low)
    high_fee_swap_after = swap(alice, bob, a_nonce=2, b_nonce=0, fee=100)
    assert pool.add_transaction(high_fee_swap_after)
    order = [t.tx_id for t in pool.get_transactions_for_block()]
    assert order.index(low.tx_id) < order.index(high_fee_swap_after.tx_id)


def test_contradictory_ordering_rejected(alice, bob):
    pool = Mempool()
    x, y = single(alice, 1), single(bob, 1)
    pool._list = [x, y]
    # Must precede x (alice nonce 0 < 1) yet follow y (bob nonce 2 > 1): impossible.
    assert not pool.add_transaction(swap(alice, bob, a_nonce=0, b_nonce=2))
    assert pool._list == [x, y]


def test_remove_purges_every_spent_signer_nonce(alice, bob):
    pool = Mempool()
    pool.add_transaction(single(alice, 0))
    pool.add_transaction(single(bob, 0))
    pool.add_transaction(single(bob, 1))
    mined = swap(alice, bob)  # spends (alice,0) and (bob,0)
    pool.remove_transactions([mined])
    remaining = pool.get_transactions_for_block()
    assert [(t.sender, t.nonce) for t in remaining] == [(bob[1], 1)]


def test_mine_block_with_multi_party_tx(alice, bob):
    chain = Blockchain()
    miner_sk, miner = keypair()
    for _, pk in (alice, bob):
        chain.state.credit_mining_reward(pk, 100)
    pool = Mempool()
    assert pool.add_transaction(swap(alice, bob))
    block = main.mine_and_process_block(chain, pool, miner)
    assert block is not None and len(block.transactions) == 1
    assert chain.state.accounts[alice[1]]["nonce"] == 1
    assert chain.state.accounts[bob[1]]["nonce"] == 1
    assert chain.state.accounts[alice[1]]["balances"][""] == 98
    assert chain.state.accounts[bob[1]]["balances"][""] == 102
    assert len(pool) == 0


@pytest.fixture
def anyio_backend():
    return 'asyncio'


@pytest.fixture
async def rpc(free_tcp_port):
    chain, pool = Blockchain(), Mempool()
    server = JSONRPCServer(chain, pool, P2PNetwork())
    await server.start(host="127.0.0.1", port=free_tcp_port)
    yield free_tcp_port, chain, pool
    await server.app.cleanup()


async def call(port, method, params):
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/", json={"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        ) as resp:
            return await resp.json()


@pytest.mark.anyio
async def test_rpc_accepts_multi_party_tx_and_reports_nonce(rpc, alice, bob):
    port, chain, pool = rpc
    for _, pk in (alice, bob):
        chain.state.credit_mining_reward(pk, 100)
    tx = swap(alice, bob)
    data = await call(port, "mc_sendTransaction", [tx.to_dict()])
    assert data["result"] == tx.tx_id
    assert len(pool) == 1
    assert (await call(port, "mc_getNonce", [alice[1]]))["result"] == 0
    assert (await call(port, "mc_getNonce", ["unknown"]))["result"] == 0
    assert "unknown" not in chain.state.accounts


@pytest.mark.anyio
async def test_rpc_rejects_partial_and_malformed(rpc, alice, bob):
    port, chain, pool = rpc
    (a_sk, a), (_, b) = alice, bob
    partial = Transaction(transfers=[Transfer(a, b, 5), Transfer(b, a, 3)], nonces={a: 0, b: 0})
    partial.sign(a_sk)
    data = await call(port, "mc_sendTransaction", [partial.to_dict()])
    assert "every sender and caller must sign" in data["error"]["message"]
    data = await call(port, "mc_sendTransaction", [{"transfers": [{"sender": "x"}]}])
    assert "Malformed transaction payload" in data["error"]["message"]
    assert len(pool) == 0
