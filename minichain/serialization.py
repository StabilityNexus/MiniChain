import hashlib
import json


def canonical_json_dumps(payload) -> str:
    """Serialize payloads deterministically for signing and hashing."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_json_bytes(payload) -> bytes:
    return canonical_json_dumps(payload).encode("utf-8")


def canonical_json_hash(payload) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
