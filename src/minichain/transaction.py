"""Transaction data structures and validation rules."""

from __future__ import annotations

from dataclasses import dataclass

from minichain.crypto import (
    blake2b_digest,
    derive_address,
    deserialize_verify_key,
    serialize_verify_key,
    sign_message,
    verify_signature,
)
from minichain.serialization import serialize_transaction

ADDRESS_HEX_LENGTH = 40
PUBLIC_KEY_HEX_LENGTH = 64
SIGNATURE_HEX_LENGTH = 128


def _is_lower_hex(value: str, expected_length: int) -> bool:
    if len(value) != expected_length:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


@dataclass
class Transaction:
    """A signed account-transfer transaction."""

    sender: str
    recipient: str
    amount: int
    nonce: int
    fee: int
    timestamp: int
    signature: str = ""
    public_key: str = ""

    def signing_payload(self) -> dict[str, int | str]:
        """Return the canonical transaction payload that is signed."""
        return {
            "sender": self.sender,
            "recipient": self.recipient,
            "amount": self.amount,
            "nonce": self.nonce,
            "fee": self.fee,
            "timestamp": self.timestamp,
        }

    def signing_bytes(self) -> bytes:
        """Return canonical bytes for signature generation/verification."""
        return serialize_transaction(self.signing_payload())

    def transaction_id(self) -> bytes:
        """Return a deterministic transaction hash for Merkle commitments."""
        digest_input = bytearray(self.signing_bytes())
        if self.signature:
            digest_input.extend(bytes.fromhex(self.signature))
        if self.public_key:
            digest_input.extend(bytes.fromhex(self.public_key))
        return blake2b_digest(bytes(digest_input))

    def _validate_common_fields(self) -> bool:
        if not _is_lower_hex(self.sender, ADDRESS_HEX_LENGTH):
            return False
        if not _is_lower_hex(self.recipient, ADDRESS_HEX_LENGTH):
            return False
        if not isinstance(self.amount, int) or self.amount < 0:
            return False
        if not isinstance(self.nonce, int) or self.nonce < 0:
            return False
        if not isinstance(self.fee, int) or self.fee < 0:
            return False
        if not isinstance(self.timestamp, int) or self.timestamp < 0:
            return False
        return True

    def sign(self, signing_key: object) -> None:
        """Sign this transaction in-place and populate auth fields."""
        if not self._validate_common_fields():
            raise ValueError("Invalid transaction fields")
        verify_key = signing_key.verify_key
        self.public_key = serialize_verify_key(verify_key)
        self.signature = sign_message(self.signing_bytes(), signing_key).hex()

    def verify(self) -> bool:
        """Verify transaction structure, signer identity, and signature."""
        if not self._validate_common_fields():
            return False
        if not _is_lower_hex(self.public_key, PUBLIC_KEY_HEX_LENGTH):
            return False
        if not _is_lower_hex(self.signature, SIGNATURE_HEX_LENGTH):
            return False

        try:
            verify_key = deserialize_verify_key(self.public_key)
        except Exception:
            return False

        if derive_address(verify_key) != self.sender:
            return False
        signature_bytes = bytes.fromhex(self.signature)
        return verify_signature(self.signing_bytes(), signature_bytes, verify_key)
