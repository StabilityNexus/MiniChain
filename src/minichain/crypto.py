"""Cryptographic identity and signature helpers."""

from __future__ import annotations

from typing import Any

try:
    from nacl.encoding import HexEncoder, RawEncoder
    from nacl.exceptions import BadSignatureError
    from nacl.hash import blake2b
    from nacl.signing import SigningKey, VerifyKey
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-light envs
    _NACL_IMPORT_ERROR = exc
    HexEncoder = RawEncoder = None  # type: ignore[assignment]
    BadSignatureError = Exception  # type: ignore[assignment]
    SigningKey = VerifyKey = Any  # type: ignore[assignment]

ADDRESS_LENGTH_BYTES = 20


def _require_nacl() -> None:
    if "blake2b" not in globals():
        msg = "PyNaCl is required for minichain.crypto. Install with: pip install PyNaCl"
        raise RuntimeError(msg) from _NACL_IMPORT_ERROR


def generate_key_pair() -> tuple[SigningKey, VerifyKey]:
    """Generate a new Ed25519 keypair."""
    _require_nacl()
    signing_key = SigningKey.generate()
    return signing_key, signing_key.verify_key


def derive_address(verify_key: VerifyKey) -> str:
    """Derive a 20-byte address from a verify key as lowercase hex."""
    _require_nacl()
    digest = blake2b_digest(verify_key.encode())
    return digest[:ADDRESS_LENGTH_BYTES].hex()


def blake2b_digest(data: bytes) -> bytes:
    """Compute a 32-byte BLAKE2b digest."""
    _require_nacl()
    return blake2b(data, encoder=RawEncoder)


def serialize_signing_key(signing_key: SigningKey) -> str:
    """Serialize a signing key into a hex string."""
    _require_nacl()
    return signing_key.encode(encoder=HexEncoder).decode("ascii")


def deserialize_signing_key(signing_key_hex: str) -> SigningKey:
    """Deserialize a signing key from a hex string."""
    _require_nacl()
    return SigningKey(signing_key_hex, encoder=HexEncoder)


def serialize_verify_key(verify_key: VerifyKey) -> str:
    """Serialize a verify key into a hex string."""
    _require_nacl()
    return verify_key.encode(encoder=HexEncoder).decode("ascii")


def deserialize_verify_key(verify_key_hex: str) -> VerifyKey:
    """Deserialize a verify key from a hex string."""
    _require_nacl()
    return VerifyKey(verify_key_hex, encoder=HexEncoder)


def sign_message(message: bytes, signing_key: SigningKey) -> bytes:
    """Sign bytes and return the detached signature bytes."""
    _require_nacl()
    return signing_key.sign(message).signature


def verify_signature(message: bytes, signature: bytes, verify_key: VerifyKey) -> bool:
    """Verify a detached Ed25519 signature."""
    _require_nacl()
    try:
        verify_key.verify(message, signature)
    except BadSignatureError:
        return False
    return True
