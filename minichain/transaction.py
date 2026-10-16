import time
from typing import Optional, Union, List, Dict, Any
from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError, CryptoError
from .serialization import canonical_json_bytes, canonical_json_hash


ALLOW_MINT = 1
ALLOW_BURN = 2
BURN_ADDRESS = "0000000000000000000000000000000000000000"


def _normalize_amount(amount):
    if isinstance(amount, int) and not isinstance(amount, bool):
        return {"": amount}
    elif isinstance(amount, dict):
        return dict(amount)
    return amount


class Withdrawal:
    def __init__(self, sender: str, amount: Union[int, Dict[str, int]]):
        self.sender = str(sender) if sender is not None else ""
        self.amount = _normalize_amount(amount)

    def to_dict(self) -> dict:
        return {
            "sender": self.sender,
            "amount": self.amount,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Withdrawal":
        return cls(sender=data["sender"], amount=data["amount"])

    def __eq__(self, other):
        return isinstance(other, Withdrawal) and self.sender == other.sender and self.amount == other.amount

    def __repr__(self):
        return f"Withdrawal(sender={self.sender!r}, amount={self.amount!r})"


class Deposit:
    def __init__(self, receiver: str, amount: Union[int, Dict[str, int]]):
        self.receiver = str(receiver) if receiver is not None else ""
        self.amount = _normalize_amount(amount)

    def to_dict(self) -> dict:
        return {
            "receiver": self.receiver,
            "amount": self.amount,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Deposit":
        return cls(receiver=data["receiver"], amount=data["amount"])

    def __eq__(self, other):
        return isinstance(other, Deposit) and self.receiver == other.receiver and self.amount == other.amount

    def __repr__(self):
        return f"Deposit(receiver={self.receiver!r}, amount={self.amount!r})"


class ContractCall:
    def __init__(
        self,
        caller: str,
        address: Optional[str] = None,
        amount: Union[int, Dict[str, int]] = 0,
        data: Optional[str] = None,
    ):
        self.caller = str(caller) if caller is not None else ""
        self.address = address
        self.amount = _normalize_amount(amount)
        self.data = data

    def to_dict(self) -> dict:
        return {
            "caller": self.caller,
            "address": self.address,
            "amount": self.amount,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ContractCall":
        return cls(
            caller=data["caller"],
            address=data.get("address"),
            amount=data.get("amount", {}),
            data=data.get("data"),
        )

    def __eq__(self, other):
        return (
            isinstance(other, ContractCall)
            and self.caller == other.caller
            and self.address == other.address
            and self.amount == other.amount
            and self.data == other.data
        )

    def __repr__(self):
        return f"ContractCall(caller={self.caller!r}, address={self.address!r}, amount={self.amount!r}, data={self.data!r})"


class Transfer:
    def __init__(self, sender: str, receiver: str, amount: Union[int, Dict[str, int]]):
        self.sender = str(sender) if sender is not None else ""
        self.receiver = str(receiver) if receiver is not None else ""
        self.amount = _normalize_amount(amount)

    def to_withdrawal_and_deposit(self) -> tuple:
        return Withdrawal(self.sender, self.amount), Deposit(self.receiver, self.amount)

    def to_dict(self) -> dict:
        return {
            "sender": self.sender,
            "receiver": self.receiver,
            "amount": self.amount,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Transfer":
        return cls(
            sender=data["sender"],
            receiver=data["receiver"],
            amount=data["amount"],
        )

    def __eq__(self, other):
        return (
            isinstance(other, Transfer)
            and self.sender == other.sender
            and self.receiver == other.receiver
            and self.amount == other.amount
        )

    def __repr__(self):
        return f"Transfer(sender={self.sender!r}, receiver={self.receiver!r}, amount={self.amount!r})"


class Transaction:
    _TX_FIELDS = frozenset({
        "sender",
        "receiver",
        "amount",
        "gas_limit",
        "fee_per_gas",
        "nonce",
        "data",
        "timestamp",
        "chain_id",
        "signature",
        "flags",
        "fee_asset",
        "withdrawals",
        "deposits",
        "calls",
        "transfers",
        "signatures",
        "nonces",
    })

    def __setattr__(self, name, value) -> None:
        if name in self._TX_FIELDS and getattr(self, "_sealed", False):
            raise AttributeError(f"Transaction is sealed; cannot modify '{name}'")
        super().__setattr__(name, value)
        if name in self._TX_FIELDS and hasattr(self, "_cached_tx_id"):
            super().__setattr__("_cached_tx_id", None)

    @staticmethod
    def _normalize_ts(ts) -> int:
        if ts < 1e12:
            return round(ts * 1000)
        return int(ts)

    def __init__(
        self,
        sender=None,
        receiver=None,
        amount=None,
        nonce=None,
        gas_limit=0,
        fee_per_gas=0,
        data=None,
        chain_id="minichain-default",
        signature=None,
        timestamp=None,
        flags=0,
        fee_asset="",
        withdrawals=None,
        deposits=None,
        calls=None,
        transfers=None,
        signatures=None,
        nonces=None,
    ):
        self._sealed = False
        self._cached_tx_id = None

        is_gen = (
            withdrawals is not None
            or deposits is not None
            or calls is not None
            or transfers is not None
            or signatures is not None
            or (nonces is not None and isinstance(nonces, dict))
        )
        self._is_legacy = not is_gen

        self.withdrawals: List[Withdrawal] = []
        self.deposits: List[Deposit] = []
        self.calls: List[ContractCall] = []
        self.transfers: List[Transfer] = []
        self.signatures: Dict[str, str] = {}
        self.nonces: Dict[str, int] = {}

        if transfers:
            for t in transfers:
                if isinstance(t, dict):
                    t = Transfer.from_dict(t)
                self.transfers.append(t)
                w, d = t.to_withdrawal_and_deposit()
                self.withdrawals.append(w)
                self.deposits.append(d)

        if withdrawals:
            for w in withdrawals:
                if isinstance(w, dict):
                    w = Withdrawal.from_dict(w)
                elif isinstance(w, (list, tuple)) and len(w) == 2:
                    w = Withdrawal(w[0], w[1])
                self.withdrawals.append(w)

        if deposits:
            for d in deposits:
                if isinstance(d, dict):
                    d = Deposit.from_dict(d)
                elif isinstance(d, (list, tuple)) and len(d) == 2:
                    d = Deposit(d[0], d[1])
                self.deposits.append(d)

        if calls:
            for c in calls:
                if isinstance(c, dict):
                    c = ContractCall.from_dict(c)
                elif isinstance(c, (list, tuple)):
                    c = ContractCall(*c)
                self.calls.append(c)

        if signatures:
            if isinstance(signatures, dict):
                self.signatures = dict(signatures)
            elif isinstance(signatures, list):
                for item in signatures:
                    if isinstance(item, dict) and "signer" in item and "signature" in item:
                        self.signatures[item["signer"]] = item["signature"]
                    elif isinstance(item, (list, tuple)) and len(item) == 2:
                        self.signatures[item[0]] = item[1]

        if nonces:
            if isinstance(nonces, dict):
                self.nonces = dict(nonces)
            elif isinstance(nonces, int):
                if sender:
                    self.nonces[sender] = nonces

        # Populate legacy fields
        self.sender = sender
        self.receiver = receiver
        self.amount = _normalize_amount(amount)
        self.nonce = nonce if nonce is not None else (0 if self._is_legacy else 0)
        self.data = data
        self.signature = signature

        if signature and sender:
            self.signatures[sender] = signature
        if nonce is not None and sender:
            self.nonces[sender] = nonce

        # If legacy invocation, derive generalized structures
        if self._is_legacy and sender:
            if data is not None:
                self.calls.append(ContractCall(caller=sender, address=receiver, amount=self.amount, data=data))
                has_val = any(v > 0 for v in self.amount.values()) if isinstance(self.amount, dict) else False
                if has_val:
                    self.withdrawals.append(Withdrawal(sender=sender, amount=self.amount))
            else:
                self.withdrawals.append(Withdrawal(sender=sender, amount=self.amount))
                if receiver is not None:
                    self.deposits.append(Deposit(receiver=receiver, amount=self.amount))

        # If generalized invocation without explicit sender/receiver/amount, derive them for ergonomics
        if not self._is_legacy:
            if self.sender is None:
                if self.withdrawals:
                    self.sender = self.withdrawals[0].sender
                elif self.calls:
                    self.sender = self.calls[0].caller
            if self.receiver is None:
                if self.deposits:
                    self.receiver = self.deposits[0].receiver
                elif self.calls:
                    self.receiver = self.calls[0].address
            if not self.amount:
                if self.withdrawals:
                    self.amount = self.withdrawals[0].amount
                elif self.deposits:
                    self.amount = self.deposits[0].amount
            if self.sender and self.sender in self.nonces:
                self.nonce = self.nonces[self.sender]
            elif self.nonces:
                self.nonce = next(iter(self.nonces.values()))
            if self.sender and self.sender in self.signatures:
                self.signature = self.signatures[self.sender]

        self.gas_limit = gas_limit
        self.fee_per_gas = fee_per_gas
        self.chain_id = chain_id
        self.timestamp = self._normalize_ts(timestamp) if timestamp is not None else round(time.time() * 1000)
        self.flags = int(flags) if isinstance(flags, int) and not isinstance(flags, bool) else flags
        self.fee_asset = str(fee_asset) if fee_asset is not None else ""

    @property
    def required_signers(self) -> set:
        signers = set()
        for w in self.withdrawals:
            if w.sender:
                signers.add(w.sender)
        for c in self.calls:
            if c.caller:
                signers.add(c.caller)
        if not signers and getattr(self, "sender", None):
            signers.add(self.sender)
        return signers

    @property
    def signature_list(self) -> list:
        return list(self.signatures.values())

    def to_signing_dict(self) -> dict:
        if self._is_legacy:
            return {
                "sender": self.sender,
                "receiver": self.receiver,
                "amount": self.amount,
                "gas_limit": self.gas_limit,
                "fee_per_gas": self.fee_per_gas,
                "nonce": self.nonce,
                "data": self.data,
                "chain_id": self.chain_id,
                "timestamp": self.timestamp,
                "flags": self.flags,
                "fee_asset": self.fee_asset,
            }

        d = {
            "gas_limit": self.gas_limit,
            "fee_per_gas": self.fee_per_gas,
            "fee_asset": self.fee_asset,
            "chain_id": self.chain_id,
            "timestamp": self.timestamp,
            "flags": self.flags,
            "nonces": self.nonces,
            "calls": [c.to_dict() for c in self.calls],
        }
        if self.transfers:
            d["transfers"] = [t.to_dict() for t in self.transfers]
        else:
            d["withdrawals"] = [w.to_dict() for w in self.withdrawals]
            d["deposits"] = [dp.to_dict() for dp in self.deposits]
        return d

    def to_dict(self) -> dict:
        d = self.to_signing_dict()
        if self._is_legacy:
            d["signature"] = self.signature
        else:
            d["signatures"] = self.signatures
        return d

    @classmethod
    def from_dict(cls, payload: dict) -> "Transaction":
        flags = payload.get("flags", 0)
        if "withdrawals" in payload or "transfers" in payload or "calls" in payload or "signatures" in payload:
            return cls(
                withdrawals=payload.get("withdrawals"),
                deposits=payload.get("deposits"),
                calls=payload.get("calls"),
                transfers=payload.get("transfers"),
                signatures=payload.get("signatures"),
                nonces=payload.get("nonces"),
                gas_limit=payload.get("gas_limit", 0),
                fee_per_gas=payload.get("fee_per_gas", 0),
                chain_id=payload.get("chain_id", "minichain-default"),
                timestamp=payload.get("timestamp"),
                flags=flags,
                fee_asset=payload.get("fee_asset", ""),
            )
        return cls(
            sender=payload["sender"],
            receiver=payload.get("receiver"),
            amount=payload.get("amount"),
            nonce=payload["nonce"],
            gas_limit=payload.get("gas_limit", 0),
            fee_per_gas=payload.get("fee_per_gas", 0),
            data=payload.get("data"),
            chain_id=payload.get("chain_id", "minichain-default"),
            signature=payload.get("signature"),
            timestamp=payload.get("timestamp"),
            flags=flags,
            fee_asset=payload.get("fee_asset", ""),
        )

    @property
    def hash_payload(self) -> bytes:
        return canonical_json_bytes(self.to_signing_dict())

    @property
    def tx_id(self) -> str:
        if self._cached_tx_id is None:
            self._cached_tx_id = canonical_json_hash(self.to_dict())
        return self._cached_tx_id

    def sign(self, signing_key: SigningKey) -> None:
        signer_pk = signing_key.verify_key.encode(encoder=HexEncoder).decode()
        if self._is_legacy:
            if signer_pk != self.sender:
                raise ValueError("Signing key does not match sender")
            sig = signing_key.sign(self.hash_payload).signature.hex()
            self.signature = sig
            self.signatures[signer_pk] = sig
            self._sealed = True
            return

        required = self.required_signers
        if required and signer_pk not in required:
            raise ValueError("Signing key does not match any sender or caller")
        sig = signing_key.sign(self.hash_payload).signature.hex()
        self.signatures[signer_pk] = sig
        if not self.signature or self.sender == signer_pk:
            self.signature = sig
        if len(self.signatures) >= len(required):
            self._sealed = True

    def verify(self) -> bool:
        if self._is_legacy:
            if not self.signature or not self.sender:
                return False
            try:
                VerifyKey(self.sender, encoder=HexEncoder).verify(
                    self.hash_payload, bytes.fromhex(self.signature)
                )
            except (BadSignatureError, CryptoError, ValueError, TypeError):
                return False
            else:
                return True

        required = self.required_signers
        if not required:
            return False
        if not self.signatures:
            return False
        for signer in required:
            sig = self.signatures.get(signer)
            if not sig:
                return False
            try:
                VerifyKey(signer, encoder=HexEncoder).verify(
                    self.hash_payload, bytes.fromhex(sig)
                )
            except (BadSignatureError, CryptoError, ValueError, TypeError):
                return False
        return True