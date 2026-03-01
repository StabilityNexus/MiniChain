import json
import logging

logger = logging.getLogger(__name__)


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

    def __init__(self, handler_callback=None):
        self._handler_callback = None
        if handler_callback is not None:
            self.register_handler(handler_callback)
        self.pubsub = None  # Will be set in real implementation

    def register_handler(self, handler_callback):
        if not callable(handler_callback):
            raise ValueError("handler_callback must be callable")
        self._handler_callback = handler_callback

    async def start(self):
        logger.info("Network: Listening on /ip4/0.0.0.0/tcp/0")
        # In real libp2p, we would await host.start() here

    async def stop(self):
        """Clean up network resources cleanly upon shutdown."""
        logger.info("Network: Shutting down")
        if self.pubsub:
            try:
                shutdown_meth = None
                for method_name in ('close', 'stop', 'aclose', 'shutdown'):
                    if hasattr(self.pubsub, method_name):
                        shutdown_meth = getattr(self.pubsub, method_name)
                        break
                
                if shutdown_meth:
                    import asyncio
                    res = shutdown_meth()
                    if asyncio.iscoroutine(res):
                        await res
            except Exception as e:
                logger.error("Network: Error shutting down pubsub: %s", e)
            finally:
                self.pubsub = None

    async def _broadcast_message(self, topic, msg_type, payload):
        msg = json.dumps({"type": msg_type, "data": payload})
        if self.pubsub:
            try:
                await self.pubsub.publish(topic, msg.encode())
            except Exception as e:
                logger.error("Network: Publish failed: %s", e)
        else:
            logger.debug("Network: pubsub not initialized (mock mode)")

    async def broadcast_transaction(self, tx):
        sender = getattr(tx, "sender", "<unknown>")
        logger.info("Network: Broadcasting Tx from %s...", sender[:5])
        try:
            payload = tx.to_dict()
        except (TypeError, ValueError) as e:
            logger.error("Network: Failed to serialize tx: %s", e)
            return
        await self._broadcast_message("minichain-global", "tx", payload)

    async def broadcast_block(self, block):
        logger.info("Network: Broadcasting Block #%d", block.index)
        await self._broadcast_message("minichain-global", "block", block.to_dict())

    async def handle_message(self, msg):
        """
        Callback when a p2p message is received.
        """

        try:
            if not hasattr(msg, "data"):
                raise TypeError("Incoming message missing 'data' attribute")

            if not isinstance(msg.data, (bytes, bytearray)):
                raise TypeError("msg.data must be bytes")

            if len(msg.data) > 1024 * 1024:  # 1MB limit
                logger.warning("Network: Message too large")
                return

            try:
                decoded = msg.data.decode('utf-8')
            except UnicodeDecodeError as e:
                logger.warning("Network Error: UnicodeDecodeError during message decode: %s", e)
                return
            data = json.loads(decoded)

            if not isinstance(data, dict) or "type" not in data or "data" not in data:
                raise ValueError("Invalid message format")

        except (TypeError, ValueError, json.JSONDecodeError) as e:
            logger.warning("Network Error parsing message: %s", e)
            return

        try:
            if self._handler_callback:
                await self._handler_callback(data)
            else:
                logger.warning("Network Error: No handler_callback registered")
        except Exception:
            logger.exception("Error in network handler callback for data: %s", data)
