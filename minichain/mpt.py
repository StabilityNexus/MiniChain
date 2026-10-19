from typing import Optional
from trie import HexaryTrie


class _Overlay(dict):
    """Copy-on-write node store: writes stay local, reads fall through to `base`."""

    def __init__(self, base):
        super().__init__()
        self.base = base

    def __missing__(self, key):
        return self.base[key]


class Trie:
    """
    A Merkle Patricia Trie (MPT) for MiniChain backed by the `trie` library.
    Provides O(log N) state verification via cryptographic state roots.
    """
    def __init__(self, _trie=None):
        self._trie = _trie if _trie is not None else HexaryTrie({})

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

    def delete(self, key_hex: str):
        try:
            key = bytes.fromhex(key_hex)
        except ValueError:
            return  # not valid hex, so it was never stored
        self._trie.delete(key)

    def fork(self) -> "Trie":
        """A trie at the same root whose later changes never touch this one (O(1))."""
        return Trie(HexaryTrie(_Overlay(self._trie.db), self._trie.root_hash))

    def node_count(self) -> int:
        return len(self._trie.db)
