"""
tests/test_mpt.py

Unit tests for minichain.mpt.Trie.
"""

from minichain.mpt import Trie


def test_empty_trie_root_hash_is_stable():
    trie = Trie()
    assert isinstance(trie.root_hash(), str)
    assert len(trie.root_hash()) == 64  # 32 bytes, hex-encoded


def test_get_missing_key_returns_empty_string():
    """The underlying HexaryTrie returns b'' (not None) for an absent key,
    so Trie.get()'s `is not None` guard never actually short-circuits to None."""
    trie = Trie()
    key = "00" * 20
    assert trie.get(key) == ""


def test_put_then_get_roundtrips_value():
    trie = Trie()
    key = "11" * 20
    trie.put(key, "hello")
    assert trie.get(key) == "hello"


def test_put_changes_root_hash():
    trie = Trie()
    before = trie.root_hash()
    trie.put("22" * 20, "value")
    after = trie.root_hash()
    assert before != after
