"""
Persistent node identity.

Without this, `new_host()` mints a fresh libp2p keypair every time a node starts,
so its peer ID — the `/p2p/<id>` half of every multiaddr — changes on every restart.
That invalidates any address a peer saved, any bootstrap entry pointing at this node,
and any relay reservation, making the peer book and reconnection logic pointless.
Persisting the key is what lets an address stay valid across restarts, the same role
geth's `nodekey` file plays.
"""

import logging
import os

from libp2p.crypto.ed25519 import Ed25519PrivateKey, create_new_key_pair
from libp2p.crypto.keys import KeyPair

logger = logging.getLogger(__name__)

_NODEKEY_FILE = "nodekey"


def load_or_create_keypair(datadir: str) -> KeyPair:
    """Load the node's persistent Ed25519 keypair from *datadir*, creating one
    on first run. The same keypair yields the same peer ID every time."""
    path = os.path.join(datadir, _NODEKEY_FILE)

    if os.path.exists(path):
        try:
            with open(path, "rb") as f:
                seed = f.read()
            sk = Ed25519PrivateKey.from_bytes(seed)
            return KeyPair(sk, sk.get_public_key())
        except Exception as e:
            logger.warning("Failed to load node key from %s: %s — generating a new one", path, e)

    os.makedirs(datadir, exist_ok=True)
    keypair = create_new_key_pair()
    seed = keypair.private_key.to_bytes()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(seed)
    logger.info("Created new node identity at %s", path)
    return keypair
