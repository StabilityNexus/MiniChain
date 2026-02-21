"""Merkle tree construction for transaction commitments."""

from __future__ import annotations

from minichain.crypto import blake2b_digest


def _hash_pair(left: bytes, right: bytes) -> bytes:
    return blake2b_digest(left + right)


def compute_merkle_root(leaves: list[bytes]) -> bytes:
    """Compute the Merkle root from pre-hashed leaf bytes."""
    if not leaves:
        return blake2b_digest(b"")

    level = [bytes(leaf) for leaf in leaves]
    while len(level) > 1:
        if len(level) % 2 == 1:
            level.append(level[-1])

        next_level: list[bytes] = []
        for i in range(0, len(level), 2):
            next_level.append(_hash_pair(level[i], level[i + 1]))
        level = next_level

    return level[0]
