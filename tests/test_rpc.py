import pytest
import aiohttp
import asyncio
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder
from minichain.chain import Blockchain
from minichain.mempool import Mempool
from minichain.p2p import P2PNetwork
from minichain.rpc import JSONRPCServer
from minichain.transaction import Transaction

@pytest.fixture
def anyio_backend():
    return 'asyncio'

@pytest.fixture
async def rpc_server(free_tcp_port):
    chain = Blockchain()
    mempool = Mempool()
    network = P2PNetwork()
    
    server = JSONRPCServer(chain, mempool, network)
    port = free_tcp_port
    await server.start(host="127.0.0.1", port=port)
    
    yield server, port, chain, mempool
    
    await server.app.cleanup()

@pytest.mark.anyio
async def test_rpc_blockNumber(rpc_server):
    server, port, chain, mempool = rpc_server
    
    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_blockNumber", "id": 1}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["result"] == 0
            assert data["id"] == 1

@pytest.mark.anyio
async def test_rpc_getBlockByNumber(rpc_server):
    server, port, chain, mempool = rpc_server
    
    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_getBlockByNumber", "params": [0], "id": 2}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["result"]["index"] == 0
            assert data["id"] == 2

@pytest.mark.anyio
async def test_rpc_invalid_request_format(rpc_server):
    server, port, chain, mempool = rpc_server
    
    async with aiohttp.ClientSession() as session:
        payload = 1
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "error" in data
            assert data["error"]["code"] == -32600
            assert data["id"] is None

@pytest.mark.anyio
async def test_rpc_invalid_method(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_unknown", "id": 3}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert "error" in data
            assert data["error"]["code"] == -32601
            assert data["id"] == 3


@pytest.mark.anyio
async def test_rpc_malformed_json_body(rpc_server):
    """A body that isn't valid JSON must yield a Parse error (-32700)."""
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/",
            data="not-json",
            headers={"Content-Type": "application/json"},
        ) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert data["error"]["code"] == -32700
            assert data["id"] is None


@pytest.mark.anyio
async def test_rpc_batch_request(rpc_server):
    """A JSON array body is treated as a batch and answered per-item."""
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = [
            {"jsonrpc": "2.0", "method": "mc_blockNumber", "id": 1},
            {"jsonrpc": "2.0", "method": "mc_unknown", "id": 2},
        ]
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            assert resp.status == 200
            data = await resp.json()
            assert isinstance(data, list)
            assert data[0]["result"] == 0
            assert data[1]["error"]["code"] == -32601


@pytest.mark.anyio
async def test_rpc_getBlockByNumber_latest(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_getBlockByNumber", "params": ["latest"], "id": 4}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            data = await resp.json()
            assert data["result"]["index"] == chain.last_block.index


@pytest.mark.anyio
async def test_rpc_getBlockByNumber_out_of_range(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_getBlockByNumber", "params": [999], "id": 5}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            data = await resp.json()
            assert data["result"] is None


@pytest.mark.anyio
async def test_rpc_getBlockByNumber_missing_params(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_getBlockByNumber", "id": 6}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            data = await resp.json()
            assert data["error"]["code"] == -32000
            assert "Missing block number" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_getBalance_missing_params(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_getBalance", "id": 7}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            data = await resp.json()
            assert data["error"]["code"] == -32000
            assert "Missing address" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_getBalance_unknown_address(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_getBalance", "params": ["nobody"], "id": 8}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            data = await resp.json()
            assert data["result"] == 0


@pytest.mark.anyio
async def test_rpc_sendTransaction_missing_params(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_sendTransaction", "id": 9}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            data = await resp.json()
            assert data["error"]["code"] == -32000
            assert "Missing transaction payload" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_sendTransaction_invalid_signature(rpc_server):
    server, port, chain, mempool = rpc_server

    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    tx = Transaction(pk, "receiver", amount=1, nonce=0)  # never signed

    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_sendTransaction", "params": [tx.to_dict()], "id": 10}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            data = await resp.json()
            assert data["error"]["code"] == -32000
            assert "Invalid signature" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_sendTransaction_fails_state_validation(rpc_server):
    """A validly signed tx from an unfunded sender must fail state validation, not crash."""
    server, port, chain, mempool = rpc_server

    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    tx = Transaction(pk, "receiver", amount=1000, nonce=0)
    tx.sign(sk)

    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_sendTransaction", "params": [tx.to_dict()], "id": 11}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            data = await resp.json()
            assert data["error"]["code"] == -32000
            assert "failed state validation" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_sendTransaction_success(rpc_server):
    server, port, chain, mempool = rpc_server

    sk = SigningKey.generate()
    pk = sk.verify_key.encode(encoder=HexEncoder).decode()
    chain.state.credit_mining_reward(pk, reward=100)

    tx = Transaction(pk, "receiver", amount=1, nonce=0)
    tx.sign(sk)

    async with aiohttp.ClientSession() as session:
        payload = {"jsonrpc": "2.0", "method": "mc_sendTransaction", "params": [tx.to_dict()], "id": 12}
        async with session.post(f"http://127.0.0.1:{port}/", json=payload) as resp:
            data = await resp.json()
            assert data["result"] == tx.tx_id
            assert len(mempool) == 1
