import pytest
import aiohttp
import asyncio
from minichain.chain import Blockchain
from minichain.mempool import Mempool
from minichain.p2p import P2PNetwork
from minichain.rpc import JSONRPCServer

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
