from typing import Optional
from trie import HexaryTrie


class Trie:
    """
    A Merkle Patricia Trie (MPT) for MiniChain backed by the `trie` library.
    Provides O(log N) state verification via cryptographic state roots.
    """
    def __init__(self):
        self._trie = HexaryTrie({})

    def root_hash(self) -> str:
        """Returns the 32-byte hex hash of the trie's root."""
        return self._trie.root_hash.hex()

    def get(self, key_hex: str) -> Optional[str]:
        key = bytes.fromhex(key_hex)
        val = self._trie.get(key)
        return val.decode() if val is not None else None

    def put(self, key_hex: str, value: str):
        key = bytes.fromhex(key_hex)
        self._trie.set(key, value.encode())
