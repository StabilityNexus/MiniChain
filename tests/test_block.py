"""
tests/test_block.py

Unit tests for minichain.block.Block.

Covers:
  1. Merkle root construction over multiple transactions (even and odd counts).
  2. Receipt root auto-computed from receipts when not explicitly provided.
  3. from_dict validation: invalid/missing target, tampered merkle/receipt root.
  4. canonical_payload guards against a missing or tampered hash.
"""

import pytest

from minichain.block import Block, calculate_receipt_root
from minichain.receipt import Receipt
from minichain.transaction import Transaction


def _tx(nonce):
    return Transaction("sender", "receiver", amount=1, nonce=nonce)


# ------------------------------------------------------------------
# 1. Merkle root over multiple transactions
# ------------------------------------------------------------------

def test_merkle_root_none_when_no_transactions():
    block = Block(index=1, previous_hash="0x0", target=100, transactions=[])
    assert block.merkle_root is None


def test_merkle_root_even_number_of_transactions():
    txs = [_tx(0), _tx(1)]
    block = Block(index=1, previous_hash="0x0", target=100, transactions=txs)
    assert block.merkle_root is not None
    assert isinstance(block.merkle_root, str)


def test_merkle_root_odd_number_of_transactions_pads_last_hash():
    """An odd transaction count must duplicate the last hash to build the tree."""
    txs = [_tx(0), _tx(1), _tx(2)]
    block = Block(index=1, previous_hash="0x0", target=100, transactions=txs)
    assert block.merkle_root is not None

    # A different odd-length set of transactions must produce a different root.
    other_txs = [_tx(0), _tx(1), _tx(3)]
    other_block = Block(index=1, previous_hash="0x0", target=100, transactions=other_txs)
    assert other_block.merkle_root != block.merkle_root


# ------------------------------------------------------------------
# 2. Receipt root
# ------------------------------------------------------------------

def test_receipt_root_none_when_no_receipts():
    assert calculate_receipt_root([]) is None


def test_receipt_root_auto_computed_from_receipts():
    receipts = [Receipt(tx_hash="a", status=1), Receipt(tx_hash="b", status=0)]
    block = Block(index=1, previous_hash="0x0", target=100, receipts=receipts)
    assert block.receipt_root is not None
    assert block.receipt_root == calculate_receipt_root(receipts)


def test_receipt_root_explicit_value_not_overwritten():
    receipts = [Receipt(tx_hash="a", status=1)]
    block = Block(index=1, previous_hash="0x0", target=100, receipts=receipts, receipt_root="explicit")
    assert block.receipt_root == "explicit"


# ------------------------------------------------------------------
# 3. from_dict validation
# ------------------------------------------------------------------

def test_from_dict_missing_target_raises():
    payload = {"index": 1, "previous_hash": "0x0"}
    with pytest.raises(ValueError, match="missing target"):
        Block.from_dict(payload)


def test_from_dict_out_of_range_target_raises():
    from minichain.network_config import MAX_TARGET
    payload = {"index": 1, "previous_hash": "0x0", "target": hex(MAX_TARGET + 1)}
    with pytest.raises(ValueError, match="invalid target"):
        Block.from_dict(payload)


def test_from_dict_zero_target_raises():
    payload = {"index": 1, "previous_hash": "0x0", "target": "0x0"}
    with pytest.raises(ValueError, match="invalid target"):
        Block.from_dict(payload)


def test_from_dict_tampered_merkle_root_raises():
    txs = [_tx(0), _tx(1)]
    block = Block(index=1, previous_hash="0x0", target=100, transactions=txs)
    block.hash = block.compute_hash()
    payload = block.to_dict()
    payload["merkle_root"] = "tampered"
    with pytest.raises(ValueError, match="merkle_root does not match"):
        Block.from_dict(payload)


def test_from_dict_tampered_receipt_root_raises():
    """The header's receipt_root is bound into the block hash, so tampering the
    body's receipts (not the header field) is what isolates the receipt_root check."""
    receipts = [Receipt(tx_hash="a", status=1)]
    block = Block(index=1, previous_hash="0x0", target=100, receipts=receipts)
    block.hash = block.compute_hash()
    payload = block.to_dict()
    payload["receipts"][0]["status"] = 0  # tamper the body, leave the header's receipt_root untouched
    with pytest.raises(ValueError, match="receipt_root does not match"):
        Block.from_dict(payload)


def test_from_dict_roundtrip_succeeds():
    txs = [_tx(0), _tx(1), _tx(2)]
    block = Block(index=1, previous_hash="0x0", target=100, transactions=txs)
    block.hash = block.compute_hash()
    payload = block.to_dict()

    restored = Block.from_dict(payload)
    assert restored.hash == block.hash
    assert restored.merkle_root == block.merkle_root


# ------------------------------------------------------------------
# 4. canonical_payload guards
# ------------------------------------------------------------------

def test_canonical_payload_missing_hash_raises():
    block = Block(index=1, previous_hash="0x0", target=100)
    with pytest.raises(ValueError, match="block hash is missing"):
        _ = block.canonical_payload


def test_canonical_payload_tampered_hash_raises():
    block = Block(index=1, previous_hash="0x0", target=100)
    block.hash = block.compute_hash()
    block.nonce = 999  # tamper after hashing, without recomputing the hash
    with pytest.raises(ValueError, match="block hash does not match header"):
        _ = block.canonical_payload


def test_canonical_payload_succeeds_when_hash_valid():
    block = Block(index=1, previous_hash="0x0", target=100)
    block.hash = block.compute_hash()
    payload = block.canonical_payload
    assert isinstance(payload, bytes)
