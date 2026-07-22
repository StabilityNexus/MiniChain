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
        self.app.add_routes([web.post('/', self.handle_rpc)])

    async def start(self, host="127.0.0.1", port=8545):
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, host, port)
        await self.site.start()
        logger.info("🚀 JSON-RPC Server running on http://%s:%d", host, port)

    async def stop(self):
        if hasattr(self, 'site'):
            await self.site.stop()
        if hasattr(self, 'runner'):
            await self.runner.cleanup()

    @staticmethod
    def _ok(result, req_id):
        return {"jsonrpc": "2.0", "result": result, "id": req_id}

    @staticmethod
    def _err(code, message, req_id):
        return {"jsonrpc": "2.0", "error": {"code": code, "message": message}, "id": req_id}

    async def handle_rpc(self, request):
        try:
            req_data = await request.json()
        except json.JSONDecodeError:
            return web.json_response(self._err(-32700, "Parse error", None))

        if isinstance(req_data, list):
            return web.json_response([await self._process_single(req) for req in req_data])
        return web.json_response(await self._process_single(req_data))

    async def _process_single(self, req):
        if not isinstance(req, dict) or "method" not in req or req.get("jsonrpc") != "2.0":
            return self._err(-32600, "Invalid Request", req.get("id") if isinstance(req, dict) else None)

        method = req["method"]
        params = req.get("params", [])
        req_id = req.get("id")

        try:
            if method == "mc_blockNumber":
                result = self.chain.last_block.index
            elif method == "mc_getBlockByNumber":
                if not params:
                    raise ValueError("Missing block number")
                idx = params[0]
                if idx == "latest":
                    block = self.chain.last_block
                else:
                    idx = int(idx)
                    if idx < 0 or idx >= len(self.chain.chain):
                        block = None
                    else:
                        block = self.chain.chain[idx]
                result = block.to_dict() if block else None
            elif method == "mc_getBalance":
                if not params:
                    raise ValueError("Missing address")
                address = params[0]
                result = self.chain.state.get_account(address)["balance"]
            elif method == "mc_sendTransaction":
                if not params:
                    raise ValueError("Missing transaction payload")
                tx_data = params[0]
                tx = Transaction.from_dict(tx_data)
                if not tx.verify():
                    raise ValueError("Invalid signature")
                from .validators import ValidationStatus
                if self.chain.state.verify_transaction_logic(tx) != ValidationStatus.VALID:
                    raise ValueError("Transaction failed state validation")
                if self.mempool.add_transaction(tx):
                    asyncio.create_task(self.network.broadcast_transaction(tx))
                    result = tx.tx_id
                else:
                    raise ValueError("Transaction rejected by Mempool")
            else:
                return self._err(-32601, f"Method not found: {method}", req_id)

            return self._ok(result, req_id)
        except Exception as e:
            logger.error("RPC Error processing %s: %s", method, e)
            return self._err(-32000, str(e), req_id)
