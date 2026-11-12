
import pytest
import aiohttp

from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

from minichain.chain import Blockchain
from minichain.mempool import Mempool
from minichain.p2p import P2PNetwork
from minichain.rpc import JSONRPCServer
from minichain.transaction import Transaction
from minichain.block import Block
from minichain.receipt import Receipt


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def rpc_server(free_tcp_port):
    chain = Blockchain()
    mempool = Mempool()
    network = P2PNetwork()

    server = JSONRPCServer(chain, mempool, network)

    port = free_tcp_port
    await server.start(host="127.0.0.1", port=port)

    yield server, port, chain, mempool

    await server.stop()


def create_signed_transaction(amount=1, nonce=0):
    """Create a validly signed transaction for testing."""
    sk = SigningKey.generate()
    pk = sk.verify_key.encode(
        encoder=HexEncoder
    ).decode()

    tx = Transaction(
        pk,
        "receiver",
        amount=amount,
        nonce=nonce
    )

    tx.sign(sk)

    return tx


def add_confirmed_block(chain, transactions, receipts):
    """Add a test block containing transactions and their receipts."""
    previous_block = chain.last_block

    block = Block(
        index=previous_block.index + 1,
        previous_hash=previous_block.hash,
        target=previous_block.target,
        transactions=transactions,
        receipts=receipts
    )

    chain.chain.append(block)

    return block


@pytest.mark.anyio
async def test_rpc_blockNumber(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_blockNumber",
            "id": 1
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            assert resp.status == 200

            data = await resp.json()

            assert data["result"] == 0
            assert data["id"] == 1


@pytest.mark.anyio
async def test_rpc_getBlockByNumber(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getBlockByNumber",
            "params": [0],
            "id": 2
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            assert resp.status == 200

            data = await resp.json()

            assert data["result"]["index"] == 0
            assert data["id"] == 2


@pytest.mark.anyio
async def test_rpc_invalid_request_format(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=1
        ) as resp:
            assert resp.status == 200

            data = await resp.json()

            assert "error" in data
            assert data["error"]["code"] == -32600
            assert data["id"] is None


@pytest.mark.anyio
async def test_rpc_invalid_method(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_unknown",
            "id": 3
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            assert resp.status == 200

            data = await resp.json()

            assert "error" in data
            assert data["error"]["code"] == -32601
            assert data["id"] == 3


@pytest.mark.anyio
async def test_rpc_malformed_json_body(rpc_server):
    """Invalid JSON must yield a Parse error (-32700)."""
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"http://127.0.0.1:{port}/",
            data="not-json",
            headers={"Content-Type": "application/json"}
        ) as resp:
            assert resp.status == 200

            data = await resp.json()

            assert data["error"]["code"] == -32700
            assert data["id"] is None


@pytest.mark.anyio
async def test_rpc_batch_request(rpc_server):
    """A JSON array body is treated as a batch."""
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = [
            {
                "jsonrpc": "2.0",
                "method": "mc_blockNumber",
                "id": 1
            },
            {
                "jsonrpc": "2.0",
                "method": "mc_unknown",
                "id": 2
            }
        ]

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            assert resp.status == 200

            data = await resp.json()

            assert isinstance(data, list)
            assert data[0]["result"] == 0
            assert data[1]["error"]["code"] == -32601


@pytest.mark.anyio
async def test_rpc_getBlockByNumber_latest(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getBlockByNumber",
            "params": ["latest"],
            "id": 4
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["result"]["index"] == chain.last_block.index


@pytest.mark.anyio
async def test_rpc_getBlockByNumber_out_of_range(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getBlockByNumber",
            "params": [999],
            "id": 5
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["result"] is None


@pytest.mark.anyio
async def test_rpc_getBlockByNumber_missing_params(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getBlockByNumber",
            "id": 6
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["error"]["code"] == -32000
            assert "Missing block number" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_getBalance_missing_params(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getBalance",
            "id": 7
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["error"]["code"] == -32000
            assert "Missing address" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_getBalance_unknown_address(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getBalance",
            "params": ["nobody"],
            "id": 8
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["result"] == 0


@pytest.mark.anyio
async def test_rpc_sendTransaction_missing_params(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_sendTransaction",
            "id": 9
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["error"]["code"] == -32000
            assert "Missing transaction payload" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_sendTransaction_invalid_signature(rpc_server):
    server, port, chain, mempool = rpc_server

    sk = SigningKey.generate()
    pk = sk.verify_key.encode(
        encoder=HexEncoder
    ).decode()

    tx = Transaction(
        pk,
        "receiver",
        amount=1,
        nonce=0
    )

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_sendTransaction",
            "params": [tx.to_dict()],
            "id": 10
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["error"]["code"] == -32000
            assert "Invalid signature" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_sendTransaction_fails_state_validation(rpc_server):
    """An unfunded sender must fail state validation."""
    server, port, chain, mempool = rpc_server

    tx = create_signed_transaction(amount=1000)

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_sendTransaction",
            "params": [tx.to_dict()],
            "id": 11
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["error"]["code"] == -32000
            assert "failed state validation" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_sendTransaction_success(rpc_server):
    server, port, chain, mempool = rpc_server

    sk = SigningKey.generate()
    pk = sk.verify_key.encode(
        encoder=HexEncoder
    ).decode()

    chain.state.credit_mining_reward(pk, reward=100)

    tx = Transaction(
        pk,
        "receiver",
        amount=1,
        nonce=0
    )

    tx.sign(sk)

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_sendTransaction",
            "params": [tx.to_dict()],
            "id": 12
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["result"] == tx.tx_id
            assert len(mempool) == 1


# --------------------------------------------------
# mc_getTransactionByHash tests
# --------------------------------------------------

@pytest.mark.anyio
async def test_rpc_getTransactionByHash_success(rpc_server):
    server, port, chain, mempool = rpc_server

    tx = create_signed_transaction()

    add_confirmed_block(chain, [tx], [])

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getTransactionByHash",
            "params": [tx.tx_id],
            "id": 13
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert resp.status == 200
            assert data["id"] == 13
            assert data["result"] == tx.to_dict()


@pytest.mark.anyio
async def test_rpc_getTransactionByHash_unknown(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getTransactionByHash",
            "params": ["unknown_transaction_hash"],
            "id": 14
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["result"] is None


@pytest.mark.anyio
async def test_rpc_getTransactionByHash_missing_params(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getTransactionByHash",
            "id": 15
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["error"]["code"] == -32000
            assert "Missing transaction hash" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_getTransactionByHash_pending_transaction(rpc_server):
    """Transactions in the mempool are not confirmed transactions."""
    server, port, chain, mempool = rpc_server

    tx = create_signed_transaction()

    mempool.add_transaction(tx)

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getTransactionByHash",
            "params": [tx.tx_id],
            "id": 16
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["result"] is None


# --------------------------------------------------
# mc_getTransactionReceipt tests
# --------------------------------------------------

@pytest.mark.anyio
async def test_rpc_getTransactionReceipt_success(rpc_server):
    server, port, chain, mempool = rpc_server

    tx = create_signed_transaction()

    receipt = Receipt(
        tx_hash=tx.tx_id,
        status=1,
        gas_used=21000,
        error_message="",
        logs=[],
        contract_address=""
    )

    add_confirmed_block(chain, [tx], [receipt])

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getTransactionReceipt",
            "params": [tx.tx_id],
            "id": 17
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert resp.status == 200
            assert data["id"] == 17
            assert data["result"] == receipt.to_dict()


@pytest.mark.anyio
async def test_rpc_getTransactionReceipt_unknown(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getTransactionReceipt",
            "params": ["unknown_transaction_hash"],
            "id": 18
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["result"] is None


@pytest.mark.anyio
async def test_rpc_getTransactionReceipt_missing_params(rpc_server):
    server, port, chain, mempool = rpc_server

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getTransactionReceipt",
            "id": 19
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["error"]["code"] == -32000
            assert "Missing transaction hash" in data["error"]["message"]


@pytest.mark.anyio
async def test_rpc_getTransactionReceipt_without_receipt(rpc_server):
    """A transaction without an available receipt returns null."""
    server, port, chain, mempool = rpc_server

    tx = create_signed_transaction()

    add_confirmed_block(chain, [tx], [])

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getTransactionReceipt",
            "params": [tx.tx_id],
            "id": 20
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["result"] is None


@pytest.mark.anyio
async def test_rpc_getTransactionReceipt_multiple_transactions(rpc_server):
    """Each transaction must return its own corresponding receipt."""
    server, port, chain, mempool = rpc_server

    tx1 = create_signed_transaction(amount=1, nonce=0)
    tx2 = create_signed_transaction(amount=2, nonce=1)

    receipt1 = Receipt(
        tx_hash=tx1.tx_id,
        status=1,
        gas_used=21000
    )

    receipt2 = Receipt(
        tx_hash=tx2.tx_id,
        status=0,
        gas_used=30000,
        error_message="Execution failed"
    )

    add_confirmed_block(
        chain,
        [tx1, tx2],
        [receipt1, receipt2]
    )

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getTransactionReceipt",
            "params": [tx2.tx_id],
            "id": 21
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["result"] == receipt2.to_dict()


@pytest.mark.anyio
async def test_rpc_getTransactionReceipt_pending_transaction(rpc_server):
    """Pending transactions do not have execution receipts."""
    server, port, chain, mempool = rpc_server

    tx = create_signed_transaction()

    mempool.add_transaction(tx)

    async with aiohttp.ClientSession() as session:
        payload = {
            "jsonrpc": "2.0",
            "method": "mc_getTransactionReceipt",
            "params": [tx.tx_id],
            "id": 22
        }

        async with session.post(
            f"http://127.0.0.1:{port}/",
            json=payload
        ) as resp:
            data = await resp.json()

            assert data["result"] is None
