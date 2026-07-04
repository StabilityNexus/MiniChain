from typing import List, Optional


class Receipt:
    """
    Represents the execution result of a transaction.
    """

    def __init__(
        self,
        tx_hash: str,
        status: int,
        gas_used: int = 0,
        error_message: Optional[str] = None,
        logs: Optional[List[dict]] = None,
        contract_address: Optional[str] = None,
    ):
        self.tx_hash = tx_hash
        self.status = status  # 1 for success, 0 for failure
        self.gas_used = gas_used
        self.error_message = error_message
        self.logs = logs or []
        self.contract_address = contract_address

    def to_dict(self) -> dict:
        return {
            "tx_hash": self.tx_hash,
            "status": self.status,
            "gas_used": self.gas_used,
            "error_message": self.error_message,
            "logs": self.logs,
            "contract_address": self.contract_address,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "Receipt":
        return cls(
            tx_hash=payload["tx_hash"],
            status=payload["status"],
            gas_used=payload.get("gas_used", 0),
            error_message=payload.get("error_message"),
            logs=payload.get("logs", []),
            contract_address=payload.get("contract_address"),
        )
