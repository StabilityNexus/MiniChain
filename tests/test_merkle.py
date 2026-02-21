"""Unit tests for Merkle tree construction."""

from __future__ import annotations

import pytest

pytest.importorskip("nacl")

from minichain.crypto import blake2b_digest
from minichain.merkle import compute_merkle_root


def test_empty_leaf_list_has_well_defined_root() -> None:
    assert compute_merkle_root([]) == blake2b_digest(b"")


def test_merkle_root_is_deterministic() -> None:
    leaves = [blake2b_digest(b"tx-a"), blake2b_digest(b"tx-b"), blake2b_digest(b"tx-c")]
    first = compute_merkle_root(leaves)
    second = compute_merkle_root(list(leaves))
    assert first == second


def test_merkle_root_changes_when_leaf_changes() -> None:
    base = [blake2b_digest(b"tx-a"), blake2b_digest(b"tx-b"), blake2b_digest(b"tx-c")]
    modified = [blake2b_digest(b"tx-a"), blake2b_digest(b"tx-b-mutated"), blake2b_digest(b"tx-c")]
    assert compute_merkle_root(base) != compute_merkle_root(modified)


def test_odd_leaf_count_duplicates_last_leaf() -> None:
    leaves = [blake2b_digest(b"tx-a"), blake2b_digest(b"tx-b"), blake2b_digest(b"tx-c")]

    left = blake2b_digest(leaves[0] + leaves[1])
    right = blake2b_digest(leaves[2] + leaves[2])
    expected = blake2b_digest(left + right)

    assert compute_merkle_root(leaves) == expected
