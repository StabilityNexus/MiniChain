import time
from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError, CryptoError
from .serialization import canonical_json_bytes, canonical_json_hash


class Transaction:
    def __init__(self, sender, receiver, amount, nonce, data=None, signature=None, timestamp=None):
        self.sender = sender        # Public key (Hex str)
        self.receiver = receiver    # Public key (Hex str) or None for Deploy
        self.amount = amount
        self.nonce = nonce
        self.data = data            # Preserve None (do NOT normalize to "")
        if timestamp is None:
            self.timestamp = round(time.time() * 1000)  # New tx: seconds → ms
        elif timestamp > 1e12:
            self.timestamp = int(timestamp)              # Already in ms (from network)
        else:
            self.timestamp = round(timestamp * 1000)     # Seconds → ms
        self.signature = signature  # Hex str

    def to_dict(self):
        return {
            "sender": self.sender,
            "receiver": self.receiver,
            "amount": self.amount,
            "nonce": self.nonce,
            "data": self.data,
            "timestamp": self.timestamp,
            "signature": self.signature,
        }

    def to_signing_dict(self):
        return {
            "sender": self.sender,
            "receiver": self.receiver,
            "amount": self.amount,
            "nonce": self.nonce,
            "data": self.data,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, payload: dict):
        return cls(
            sender=payload["sender"],
            receiver=payload.get("receiver"),
            amount=payload["amount"],
            nonce=payload["nonce"],
            data=payload.get("data"),
            signature=payload.get("signature"),
            timestamp=payload.get("timestamp"),
        )

    @property
    def hash_payload(self):
        """Returns the bytes to be signed."""
        return canonical_json_bytes(self.to_signing_dict())

    @property
    def tx_id(self):
        """Deterministic identifier for the signed transaction."""
        return canonical_json_hash(self.to_dict())

    def sign(self, signing_key: SigningKey):
        # Validate that the signing key matches the sender
        if signing_key.verify_key.encode(encoder=HexEncoder).decode() != self.sender:
            raise ValueError("Signing key does not match sender")
        signed = signing_key.sign(self.hash_payload)
        self.signature = signed.signature.hex()

    def verify(self):
        if not self.signature:
            return False

        try:
            verify_key = VerifyKey(self.sender, encoder=HexEncoder)
            verify_key.verify(self.hash_payload, bytes.fromhex(self.signature))
            return True

        except (BadSignatureError, CryptoError, ValueError, TypeError):
            # Covers:
            # - Invalid signature
            # - Malformed public key hex
            # - Invalid hex in signature
            return False
