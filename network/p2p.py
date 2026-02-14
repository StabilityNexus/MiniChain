import json


class P2PNetwork:
    """
    A minimal abstraction for Peer-to-Peer networking.

    Expected incoming message interface for handle_message():
        msg must have attribute:
            - data: bytes (JSON-encoded payload)

    JSON structure:
        {
            "type": "tx" | "block",
            "data": {...}
        }
    """

    def __init__(self, handler_callback):
        self.peers = []
        self.handler_callback = handler_callback
        self.pubsub = None  # Will be set in real implementation

    async def start(self):
        print("Network: Listening on /ip4/0.0.0.0/tcp/0")
        # In real libp2p, we would await host.start() here

    async def broadcast_transaction(self, tx):
        msg = json.dumps({"type": "tx", "data": tx.to_dict()})
        print(f"Network: Broadcasting Tx from {tx.sender[:5]}...")

        # Publish if pubsub exists (real environment)
        if self.pubsub:
            await self.pubsub.publish("minichain-global", msg.encode())
        else:
            print("Network: pubsub not initialized (mock mode)")

    async def broadcast_block(self, block):
        msg = json.dumps({"type": "block", "data": block.to_dict()})
        print(f"Network: Broadcasting Block #{block.index}")

        if self.pubsub:
            await self.pubsub.publish("minichain-global", msg.encode())
        else:
            print("Network: pubsub not initialized (mock mode)")

    async def handle_message(self, msg):
        """
        Callback when a p2p message is received.

        Expected:
            msg.data -> bytes (JSON encoded)
        """

        try:
            # --- Guard message shape ---
            if not hasattr(msg, "data"):
                raise TypeError("Incoming message missing 'data' attribute")

            if not isinstance(msg.data, (bytes, bytearray)):
                raise TypeError("msg.data must be bytes")

            decoded = msg.data.decode()
            data = json.loads(decoded)

            # Validate JSON structure
            if not isinstance(data, dict) or "type" not in data or "data" not in data:
                raise ValueError("Invalid message format")

            await self.handler_callback(data)

        except Exception as e:
            print(f"Network Error: {e}")
