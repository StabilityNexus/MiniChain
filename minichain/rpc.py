import logging
import json
import asyncio
from aiohttp import web
from minichain.transaction import Transaction

logger = logging.getLogger(__name__)


class JSONRPCServer:
    def __init__(self, chain, mempool, network):
        self.chain = chain
        self.mempool = mempool
        self.network = network
        self.app = web.Application()
        self.app.add_routes([web.post("/", self.handle_rpc)])

    async def start(self, host="127.0.0.1", port=8545):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, host, port)
        await self.site.start()
        logger.info("🚀 JSON-RPC Server running on http://%s:%d", host, port)

    async def stop(self):
        if hasattr(self, "site"):
            await self.site.stop()
        if hasattr(self, "runner"):
            await self.runner.cleanup()

    async def handle_rpc(self, request):
        try:
            req_data = await request.json()
        except json.JSONDecodeError:
            return web.json_response(
                {
                    "jsonrpc": "2.0",
                    "error": {"code": -32700, "message": "Parse error"},
                    "id": None,
                }
            )

        if isinstance(req_data, list):
            responses = []
            for req in req_data:
                responses.append(await self._process_single(req))
            return web.json_response(responses)
        else:
            response = await self._process_single(req_data)
            return web.json_response(response)

    def _rpc_blockNumber(self, params):
        return self.chain.last_block.index

    def _rpc_getBlockByNumber(self, params):
        if not params:
            raise ValueError("Missing block number")
        idx = params[0]
        if idx == "latest":
            block = self.chain.last_block
        else:
            idx = int(idx)
            block = self.chain.chain[idx] if 0 <= idx < len(self.chain.chain) else None
        return block.to_dict() if block else None

    def _rpc_getBalance(self, params):
        if not params:
            raise ValueError("Missing address")
        account = self.chain.state.get_account(params[0])
        return account["balance"] if account else 0

    def _rpc_sendTransaction(self, params):
        if not params:
            raise ValueError("Missing transaction payload")
        tx = Transaction.from_dict(params[0])
        if not tx.verify():
            raise ValueError("Invalid signature")
        if self.mempool.add_transaction(tx):
            asyncio.create_task(self.network.broadcast_transaction(tx))
            return tx.tx_id
        raise ValueError("Transaction rejected by Mempool")

    async def _process_single(self, req):
        if (
            not isinstance(req, dict)
            or "method" not in req
            or req.get("jsonrpc") != "2.0"
        ):
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": "Invalid Request"},
                "id": req.get("id") if isinstance(req, dict) else None,
            }

        method = req["method"]
        params = req.get("params", [])
        req_id = req.get("id")

        METHODS = {
            "mc_blockNumber": self._rpc_blockNumber,
            "mc_getBlockByNumber": self._rpc_getBlockByNumber,
            "mc_getBalance": self._rpc_getBalance,
            "mc_sendTransaction": self._rpc_sendTransaction,
        }

        try:
            if method not in METHODS:
                return {
                    "jsonrpc": "2.0",
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                    "id": req_id,
                }

            result = METHODS[method](params)
            return {"jsonrpc": "2.0", "result": result, "id": req_id}
        except Exception as e:
            logger.error("RPC Error processing %s: %s", method, e)
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": str(e)},
                "id": req_id,
            }
