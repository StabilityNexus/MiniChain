import json
from types import SimpleNamespace

import pytest
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

import main
from minichain import State, Mempool


def keypair():
    sk = SigningKey.generate()
    return sk, sk.verify_key.encode(encoder=HexEncoder).decode()


@pytest.fixture
def parties():
    return keypair(), keypair()


@pytest.fixture
def chain(parties):
    (_, a), (_, b) = parties
    state = State()
    state.chain_id = "test-chain"
    for pk in (a, b):
        state.credit_mining_reward(pk, 100)
    state.get_account(b)["nonce"] = 3
    return SimpleNamespace(state=state, chain_id="test-chain")


@pytest.fixture
def spec_file(tmp_path, parties):
    (_, a), (_, b) = parties
    path = tmp_path / "swap.json"
    path.write_text(json.dumps({"transfers": [
        {"sender": a, "receiver": b, "amount": 5},
        {"sender": b, "receiver": a, "amount": 3},
    ]}))
    return path


def test_first_signer_fills_chain_id_and_nonces(spec_file, parties, chain):
    (a_sk, a), (_, b) = parties
    main.sign_tx_file(spec_file, a_sk, a, chain)
    saved = json.loads(spec_file.read_text())
    assert saved["chain_id"] == "test-chain"
    assert saved["nonces"] == {a: 0, b: 3}
    assert set(saved["signatures"]) == {a}


def test_nonces_are_locked_after_first_signature(spec_file, parties, chain):
    (a_sk, a), (b_sk, b) = parties
    main.sign_tx_file(spec_file, a_sk, a, chain)
    chain.state.get_account(b)["nonce"] = 9  # the chain moved on between signers
    tx = main.sign_tx_file(spec_file, b_sk, b, chain)
    assert tx.nonces[b] == 3
    assert tx.verify()


def test_non_signer_is_refused(spec_file, parties, chain):
    outsider_sk, outsider = keypair()
    with pytest.raises(ValueError):
        main.sign_tx_file(spec_file, outsider_sk, outsider, chain)


def test_partially_signed_file_cannot_be_submitted(spec_file, parties, chain):
    (a_sk, a), (_, b) = parties
    main.sign_tx_file(spec_file, a_sk, a, chain)
    with pytest.raises(ValueError, match="Still waiting"):
        main.load_signed_tx_file(spec_file)


def test_tampered_file_is_rejected(spec_file, parties, chain):
    (a_sk, a), (b_sk, b) = parties
    main.sign_tx_file(spec_file, a_sk, a, chain)
    main.sign_tx_file(spec_file, b_sk, b, chain)
    saved = json.loads(spec_file.read_text())
    saved["transfers"][0]["amount"] = {"": 50}
    spec_file.write_text(json.dumps(saved))
    with pytest.raises(ValueError, match="invalid signature"):
        main.load_signed_tx_file(spec_file)


@pytest.mark.anyio
async def test_fully_signed_file_reaches_mempool_and_network(spec_file, parties, chain):
    (a_sk, a), (b_sk, b) = parties
    chain.state.get_account(b)["nonce"] = 0
    main.sign_tx_file(spec_file, a_sk, a, chain)
    main.sign_tx_file(spec_file, b_sk, b, chain)
    tx = main.load_signed_tx_file(spec_file)

    sent = []

    async def broadcast_transaction(t):
        sent.append(t)

    mempool = Mempool()
    await main.submit_and_broadcast(chain, mempool, SimpleNamespace(broadcast_transaction=broadcast_transaction), tx, "ok", "rejected")
    assert len(mempool) == 1
    assert sent == [tx]


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize("content", [
    json.dumps({"sender": "a" * 64, "receiver": "b" * 40, "amount": 1, "nonce": 0}),
    json.dumps({"fee_payer": "a" * 64}),
    "{not json",
])
def test_files_that_are_not_multi_party_txs_are_refused(tmp_path, parties, chain, content):
    (a_sk, a), _ = parties
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(ValueError):
        main.sign_tx_file(path, a_sk, a, chain)


def test_signed_file_is_mined_into_a_block(tmp_path, parties):
    from minichain.chain import Blockchain

    (a_sk, a), (b_sk, b) = parties
    chain = Blockchain()
    for pk in (a, b):
        chain.state.credit_mining_reward(pk, 100)
    path = tmp_path / "swap.json"
    path.write_text(json.dumps({"transfers": [
        {"sender": a, "receiver": b, "amount": 5},
        {"sender": b, "receiver": a, "amount": 3},
    ]}))
    main.sign_tx_file(path, a_sk, a, chain)
    main.sign_tx_file(path, b_sk, b, chain)

    mempool = Mempool()
    assert mempool.add_transaction(main.load_signed_tx_file(path))
    block = main.mine_and_process_block(chain, mempool, keypair()[1])

    assert block is not None and len(block.transactions) == 1
    assert chain.state.accounts[a]["nonce"] == chain.state.accounts[b]["nonce"] == 1
    assert chain.state.accounts[a]["balances"][""] == 98
    assert chain.state.accounts[b]["balances"][""] == 102
