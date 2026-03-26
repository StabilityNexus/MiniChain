import time
from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError, CryptoError
from .serialization import canonical_json_bytes, canonical_json_hash


class Transaction:
    _TX_FIELDS = frozenset({"sender", "receiver", "amount", "nonce", "data", "timestamp", "signature"})

    def __setattr__(self, name, value) -> None:
        if name in self._TX_FIELDS and getattr(self, "_sealed", False):
            raise AttributeError(f"Transaction is sealed; cannot modify '{name}'")
        super().__setattr__(name, value)
        if name in self._TX_FIELDS and hasattr(self, "_cached_tx_id"):
            super().__setattr__("_cached_tx_id", None)

    @staticmethod
    def _normalize_ts(ts) -> int:
        ts = int(ts)
        return ts * 1000 if ts < 1e12 else ts

    def __init__(self, sender, receiver, amount, nonce, data=None, signature=None, timestamp=None):
        self.sender = sender
        self.receiver = receiver
        self.amount = amount
        self.nonce = nonce
        self.data = data
        self.timestamp = self._normalize_ts(timestamp) if timestamp is not None else round(time.time() * 1000)
        self.signature = signature
        self._cached_tx_id = None
        self._sealed = False

    def to_dict(self):
        return {"sender": self.sender, "receiver": self.receiver, "amount": self.amount,
                "nonce": self.nonce, "data": self.data, "timestamp": self.timestamp,
                "signature": self.signature}

    def to_signing_dict(self):
        return {"sender": self.sender, "receiver": self.receiver, "amount": self.amount,
                "nonce": self.nonce, "data": self.data, "timestamp": self.timestamp}

    @classmethod
    def from_dict(cls, payload: dict):
        return cls(sender=payload["sender"], receiver=payload.get("receiver"),
                   amount=payload["amount"], nonce=payload["nonce"],
                   data=payload.get("data"), signature=payload.get("signature"),
                   timestamp=payload.get("timestamp"))

    @property
    def hash_payload(self):
        return canonical_json_bytes(self.to_signing_dict())

    @property
    def tx_id(self):
        if self._cached_tx_id is None:
            self._cached_tx_id = canonical_json_hash(self.to_dict())
        return self._cached_tx_id

    def sign(self, signing_key: SigningKey):
        if signing_key.verify_key.encode(encoder=HexEncoder).decode() != self.sender:
            raise ValueError("Signing key does not match sender")
        self.signature = signing_key.sign(self.hash_payload).signature.hex()
        self._sealed = True

    def verify(self):
        if not self.signature:
            return False
        try:
            VerifyKey(self.sender, encoder=HexEncoder).verify(
                self.hash_payload, bytes.fromhex(self.signature))
        except (BadSignatureError, CryptoError, ValueError, TypeError):
            return False
        else:
            return True