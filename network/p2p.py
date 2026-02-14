import json

class P2PNetwork:
    """
    A minimal abstraction for Peer-to-Peer networking.
    Requires 'py-libp2p' for full implementation.
    Currently implements a mock for simulation.
    """
    def __init__(self, handler_callback):
        self.peers = []
        self.handler_callback = handler_callback

    async def start(self):
        print("Network: Listening on /ip4/0.0.0.0/tcp/0")
        # In real libp2p, we would await host.start() here

    async def broadcast_transaction(self, tx):
        msg = json.dumps({"type": "tx", "data": tx.to_dict()})
        print(f"Network: Broadcasting Tx from {tx.sender[:5]}...")
        # await self.pubsub.publish("minichain-global", msg.encode())

    async def broadcast_block(self, block):
        msg = json.dumps({"type": "block", "data": block.to_dict()})
        print(f"Network: Broadcasting Block #{block.index}")
        # await self.pubsub.publish("minichain-global", msg.encode())

    async def handle_message(self, msg):
        """Callback when p2p message is received"""
        try:
            data = json.loads(msg.data.decode())
            await self.handler_callback(data)
        except Exception as e:
            print(f"Network Error: {e}")