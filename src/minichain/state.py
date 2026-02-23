"""Account state and ledger transitions."""

from __future__ import annotations

from dataclasses import dataclass

from minichain.block import Block, BlockValidationError
from minichain.transaction import Transaction


@dataclass
class Account:
    """Account state for an address."""

    balance: int = 0
    nonce: int = 0


class StateTransitionError(ValueError):
    """Raised when a transaction or block cannot be applied."""


class State:
    """Mutable account-state mapping and transition engine."""

    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}

    def copy(self) -> State:
        snapshot = State()
        snapshot.accounts = {
            address: Account(balance=account.balance, nonce=account.nonce)
            for address, account in self.accounts.items()
        }
        return snapshot

    def set_account(self, address: str, account: Account) -> None:
        self.accounts[address] = account

    def get_account(self, address: str) -> Account:
        if address not in self.accounts:
            self.accounts[address] = Account()
        return self.accounts[address]

    def apply_transaction(self, transaction: Transaction) -> None:
        if transaction.is_coinbase():
            raise StateTransitionError(
                "Coinbase transaction must be applied through apply_block"
            )
        if not transaction.verify():
            raise StateTransitionError("Transaction signature/identity verification failed")

        sender = self.get_account(transaction.sender)
        recipient = self.get_account(transaction.recipient)

        if sender.nonce != transaction.nonce:
            raise StateTransitionError(
                f"Nonce mismatch for sender {transaction.sender}: "
                f"expected {sender.nonce}, got {transaction.nonce}"
            )

        total_cost = transaction.amount + transaction.fee
        if sender.balance < total_cost:
            raise StateTransitionError(
                f"Insufficient balance for sender {transaction.sender}: "
                f"required {total_cost}, available {sender.balance}"
            )

        sender.balance -= total_cost
        sender.nonce += 1
        recipient.balance += transaction.amount

    def apply_coinbase_transaction(self, transaction: Transaction) -> None:
        if not transaction.is_coinbase():
            raise StateTransitionError("Invalid coinbase transaction")
        miner = self.get_account(transaction.recipient)
        miner.balance += transaction.amount

    def apply_block(self, block: Block, *, block_reward: int = 0) -> None:
        try:
            block.validate_coinbase(block_reward=block_reward)
        except BlockValidationError as exc:
            raise StateTransitionError(f"Block validation failed: {exc}") from exc

        snapshot = self.copy()
        try:
            self.apply_coinbase_transaction(block.transactions[0])
            for transaction in block.transactions[1:]:
                self.apply_transaction(transaction)
        except StateTransitionError as exc:
            self.accounts = snapshot.accounts
            raise StateTransitionError(f"Block application failed: {exc}") from exc


def apply_transaction(state: State, transaction: Transaction) -> None:
    """Apply a transaction to state with validation."""
    state.apply_transaction(transaction)


def apply_block(state: State, block: Block, *, block_reward: int = 0) -> None:
    """Apply all block transactions atomically, rolling back on failure."""
    state.apply_block(block, block_reward=block_reward)
