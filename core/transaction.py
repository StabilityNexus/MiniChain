import json
import time
from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError

class Transaction:
    def __init__(self, sender, receiver, amount, nonce, data=None, signature=None):
        self.sender = sender        # Public key (Hex str)
        self.receiver = receiver    # Public key (Hex str) or None for Deploy
        self.amount = amount
        self.nonce = nonce
        self.data = data or ""      # Smart Contract Code or Input Data
        self.timestamp = time.time()
        self.signature = signature  # Hex str

    def to_dict(self):
        return {
            "sender": self.sender,
            "receiver": self.receiver,
            "amount": self.amount,
            "nonce": self.nonce,
            "data": self.data,
            "timestamp": self.timestamp,
            "signature": self.signature
        }

    @property
    def hash_payload(self):
        """Returns the bytes to be signed."""
        payload = {
            "sender": self.sender,
            "receiver": self.receiver,
            "amount": self.amount,
            "nonce": self.nonce,
            "data": self.data
        }
        return json.dumps(payload, sort_keys=True).encode('utf-8')

    def sign(self, signing_key: SigningKey):
        signed = signing_key.sign(self.hash_payload)
        self.signature = signed.signature.hex()

    def verify(self):
        if not self.signature:
            return False
        try:
            verify_key = VerifyKey(self.sender, encoder=HexEncoder)
            verify_key.verify(self.hash_payload, bytes.fromhex(self.signature))
            return True
        except BadSignatureError:
            return False