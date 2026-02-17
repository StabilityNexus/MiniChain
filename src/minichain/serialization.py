"""Deterministic serialization helpers for consensus-critical data."""

from __future__ import annotations

import json
from typing import Any, Mapping

TRANSACTION_FIELD_ORDER = (
    "sender",
    "recipient",
    "amount",
    "nonce",
    "fee",
    "timestamp",
)

BLOCK_HEADER_FIELD_ORDER = (
    "version",
    "previous_hash",
    "merkle_root",
    "timestamp",
    "difficulty_target",
    "nonce",
    "block_height",
)


def _to_field_map(
    value: Mapping[str, Any] | object, field_order: tuple[str, ...]
) -> dict[str, Any]:
    if isinstance(value, Mapping):
        source = dict(value)
    else:
        source = {field: getattr(value, field) for field in field_order if hasattr(value, field)}

    missing = [field for field in field_order if field not in source]
    if missing:
        raise ValueError(f"Missing required fields: {', '.join(missing)}")

    extras = sorted(set(source) - set(field_order))
    if extras:
        raise ValueError(f"Unexpected fields: {', '.join(extras)}")

    return {field: source[field] for field in field_order}


def serialize_canonical(value: Mapping[str, Any] | object, field_order: tuple[str, ...]) -> bytes:
    """Serialize a structure to canonical UTF-8 JSON bytes."""
    canonical_map = _to_field_map(value, field_order)
    text = json.dumps(
        canonical_map,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return text.encode("utf-8")


def serialize_transaction(value: Mapping[str, Any] | object) -> bytes:
    """Serialize a transaction using the canonical transaction field order."""
    return serialize_canonical(value, TRANSACTION_FIELD_ORDER)


def serialize_block_header(value: Mapping[str, Any] | object) -> bytes:
    """Serialize a block header using the canonical block header field order."""
    return serialize_canonical(value, BLOCK_HEADER_FIELD_ORDER)
