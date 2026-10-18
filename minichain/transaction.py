import time
from dataclasses import dataclass, asdict
from typing import Optional, Union, List, Dict
from nacl.signing import SigningKey, VerifyKey
from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError, CryptoError
from .serialization import canonical_json_bytes, canonical_json_hash


ALLOW_MINT = 1
ALLOW_BURN = 2
BURN_ADDRESS = "0000000000000000000000000000000000000000"

Amount = Union[int, Dict[str, int]]


def _normalize_amount(amount):
    if isinstance(amount, int) and not isinstance(amount, bool):
        return {"": amount}
    if isinstance(amount, dict):
        return dict(amount)
    return amount


@dataclass
class Withdrawal:
    sender: str
    amount: Amount

    def __post_init__(self):
        self.amount = _normalize_amount(self.amount)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Withdrawal":
        return cls(data["sender"], data["amount"])


@dataclass
class Deposit:
    receiver: str
    amount: Amount

    def __post_init__(self):
        self.amount = _normalize_amount(self.amount)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Deposit":
        return cls(data["receiver"], data["amount"])


@dataclass
class ContractCall:
    caller: str
    address: Optional[str] = None
    amount: Amount = 0
    data: Optional[str] = None

    def __post_init__(self):
        self.amount = _normalize_amount(self.amount)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ContractCall":
        return cls(data["caller"], data.get("address"), data.get("amount", {}), data.get("data"))


@dataclass
class Transfer:
    sender: str
    receiver: str
    amount: Amount

    def __post_init__(self):
        self.amount = _normalize_amount(self.amount)

    def to_withdrawal_and_deposit(self) -> tuple:
        return Withdrawal(self.sender, self.amount), Deposit(self.receiver, self.amount)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Transfer":
        return cls(data["sender"], data["receiver"], data["amount"])


def _coerce(items, cls):
    return [cls.from_dict(i) if isinstance(i, dict) else i for i in items or []]


class Transaction:
    _TX_FIELDS = frozenset({
        "sender", "receiver", "amount", "gas_limit", "fee_per_gas", "nonce", "data",
        "timestamp", "chain_id", "signature", "flags", "fee_asset", "fee_payer",
        "withdrawals", "deposits", "calls", "transfers", "signatures", "nonces",
    })
    _GENERALIZED_KEYS = ("withdrawals", "deposits", "calls", "transfers", "signatures", "nonces", "fee_payer")

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
        fee_payer=None,
    ):
        self._sealed = False
        self._cached_tx_id = None
        self._is_legacy = all(
            v is None for v in (withdrawals, deposits, calls, transfers, signatures, nonces, fee_payer)
        )

        # `withdrawals`/`deposits` hold the canonical expansion (transfers first, then
        # explicit entries); only the explicit tail is signed and sent.
        self.transfers: List[Transfer] = _coerce(transfers, Transfer)
        self.calls: List[ContractCall] = _coerce(calls, ContractCall)
        pairs = [t.to_withdrawal_and_deposit() for t in self.transfers]
        self.withdrawals: List[Withdrawal] = [w for w, _ in pairs] + _coerce(withdrawals, Withdrawal)
        self.deposits: List[Deposit] = [d for _, d in pairs] + _coerce(deposits, Deposit)
        self.signatures: Dict[str, str] = dict(signatures or {})
        self.nonces: Dict[str, int] = dict(nonces or {})

        self.sender = sender
        self.receiver = receiver
        self.amount = _normalize_amount(amount)
        self.nonce = nonce if nonce is not None else 0
        self.data = data
        self.signature = signature

        if signature and sender:
            self.signatures[sender] = signature
        if nonce is not None and sender:
            self.nonces[sender] = nonce

        if self._is_legacy and sender:
            if data is not None:
                self.calls.append(ContractCall(sender, receiver, self.amount, data))
                if any(v > 0 for v in self.amount.values()):
                    self.withdrawals.append(Withdrawal(sender, self.amount))
            else:
                self.withdrawals.append(Withdrawal(sender, self.amount))
                if receiver is not None:
                    self.deposits.append(Deposit(receiver, self.amount))

        if not self._is_legacy:
            if self.sender is None:
                self.sender = fee_payer or (
                    self.withdrawals[0].sender if self.withdrawals
                    else self.calls[0].caller if self.calls else None
                )
            if self.receiver is None:
                self.receiver = (
                    self.deposits[0].receiver if self.deposits
                    else self.calls[0].address if self.calls else None
                )
            if not self.amount:
                self.amount = (
                    self.withdrawals[0].amount if self.withdrawals
                    else self.deposits[0].amount if self.deposits else self.amount
                )
            if self.sender in self.nonces:
                self.nonce = self.nonces[self.sender]
            elif self.nonces:
                self.nonce = next(iter(self.nonces.values()))
            if self.sender in self.signatures:
                self.signature = self.signatures[self.sender]
            fee_payer = fee_payer or self.sender

        self.fee_payer = fee_payer
        self.gas_limit = gas_limit
        self.fee_per_gas = fee_per_gas
        self.chain_id = chain_id
        self.timestamp = self._normalize_ts(timestamp) if timestamp is not None else round(time.time() * 1000)
        self.flags = int(flags) if isinstance(flags, int) and not isinstance(flags, bool) else flags
        self.fee_asset = str(fee_asset) if fee_asset is not None else ""

    @property
    def required_signers(self) -> set:
        if self._is_legacy:
            return {self.sender} if self.sender else set()
        signers = {w.sender for w in self.withdrawals if w.sender}
        signers |= {c.caller for c in self.calls if c.caller}
        if not signers and self.sender:
            signers.add(self.sender)
        return signers

    def to_signing_dict(self) -> dict:
        common = {
            "gas_limit": self.gas_limit,
            "fee_per_gas": self.fee_per_gas,
            "chain_id": self.chain_id,
            "timestamp": self.timestamp,
            "flags": self.flags,
            "fee_asset": self.fee_asset,
        }
        if self._is_legacy:
            return {
                "sender": self.sender,
                "receiver": self.receiver,
                "amount": self.amount,
                "nonce": self.nonce,
                "data": self.data,
                **common,
            }
        parts = {
            "calls": self.calls,
            "transfers": self.transfers,
            "withdrawals": self.withdrawals[len(self.transfers):],
            "deposits": self.deposits[len(self.transfers):],
        }
        return {
            **common,
            "fee_payer": self.fee_payer,
            "nonces": self.nonces,
            **{k: [i.to_dict() for i in v] for k, v in parts.items() if v or k == "calls"},
        }

    def to_dict(self) -> dict:
        d = self.to_signing_dict()
        if self._is_legacy:
            d["signature"] = self.signature
        else:
            d["signatures"] = self.signatures
        return d

    @classmethod
    def from_dict(cls, payload: dict) -> "Transaction":
        common = dict(
            gas_limit=payload.get("gas_limit", 0),
            fee_per_gas=payload.get("fee_per_gas", 0),
            chain_id=payload.get("chain_id", "minichain-default"),
            timestamp=payload.get("timestamp"),
            flags=payload.get("flags", 0),
            fee_asset=payload.get("fee_asset", ""),
        )
        if any(k in payload for k in cls._GENERALIZED_KEYS):
            return cls(**{k: payload.get(k) for k in cls._GENERALIZED_KEYS}, **common)
        return cls(
            sender=payload["sender"],
            receiver=payload.get("receiver"),
            amount=payload.get("amount"),
            nonce=payload["nonce"],
            data=payload.get("data"),
            signature=payload.get("signature"),
            **common,
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
        required = self.required_signers
        if signer_pk not in required and (required or self._is_legacy):
            raise ValueError(
                "Signing key does not match sender" if self._is_legacy
                else "Signing key does not match any sender or caller"
            )
        sig = signing_key.sign(self.hash_payload).signature.hex()
        self.signatures[signer_pk] = sig
        if not self.signature or self.sender == signer_pk:
            self.signature = sig
        if len(self.signatures) >= len(required):
            self._sealed = True

    def verify(self) -> bool:
        required = self.required_signers
        if not required or set(self.signatures) != required:
            return False
        if not self._is_legacy and (self.fee_payer not in required or set(self.nonces) != required):
            return False
        for signer, sig in self.signatures.items():
            try:
                VerifyKey(signer, encoder=HexEncoder).verify(self.hash_payload, bytes.fromhex(sig))
            except (BadSignatureError, CryptoError, ValueError, TypeError):
                return False
        return True
