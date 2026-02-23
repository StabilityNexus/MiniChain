"""Mempool data structures and transaction selection logic."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from heapq import heappop, heappush
from typing import Iterable

from minichain.state import Account, State
from minichain.transaction import Transaction


class MempoolValidationError(ValueError):
    """Raised when a transaction cannot be accepted into the mempool."""


@dataclass
class _PoolEntry:
    transaction: Transaction
    transaction_id: str
    received_at: int

    @property
    def fee(self) -> int:
        return self.transaction.fee


@dataclass
class _SenderPool:
    entries: dict[int, _PoolEntry] = field(default_factory=dict)
    ready_nonces: set[int] = field(default_factory=set)
    waiting_nonces: set[int] = field(default_factory=set)


class Mempool:
    """Holds validated pending transactions and exposes mining selection."""

    def __init__(self, *, max_size: int = 1_000, max_age_seconds: int = 3_600) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be positive")
        if max_age_seconds <= 0:
            raise ValueError("max_age_seconds must be positive")

        self.max_size = max_size
        self.max_age_seconds = max_age_seconds
        self._entries_by_id: dict[str, _PoolEntry] = {}
        self._sender_pools: dict[str, _SenderPool] = {}
        self._id_by_sender_nonce: dict[tuple[str, int], str] = {}

    def size(self) -> int:
        return len(self._entries_by_id)

    def ready_count(self) -> int:
        return sum(len(pool.ready_nonces) for pool in self._sender_pools.values())

    def waiting_count(self) -> int:
        return sum(len(pool.waiting_nonces) for pool in self._sender_pools.values())

    def contains(self, transaction_id: str) -> bool:
        return transaction_id in self._entries_by_id

    def add_transaction(
        self,
        transaction: Transaction,
        state: State,
        *,
        received_at: int | None = None,
    ) -> str:
        """Validate and enqueue a transaction, returning its transaction id."""
        if transaction.is_coinbase():
            raise MempoolValidationError("Coinbase transactions are not accepted")
        if not transaction.verify():
            raise MempoolValidationError("Transaction failed signature/identity validation")

        transaction_id = transaction.transaction_id().hex()
        if transaction_id in self._entries_by_id:
            raise MempoolValidationError("Duplicate transaction")

        sender = transaction.sender
        nonce_key = (sender, transaction.nonce)
        if nonce_key in self._id_by_sender_nonce:
            raise MempoolValidationError("Duplicate sender nonce in mempool")

        sender_account = state.accounts.get(sender, Account())
        if transaction.nonce < sender_account.nonce:
            raise MempoolValidationError("Transaction nonce is stale")

        if transaction.nonce == sender_account.nonce:
            immediate_cost = transaction.amount + transaction.fee
            if immediate_cost > sender_account.balance:
                raise MempoolValidationError("Insufficient balance for pending transaction")

        entry = _PoolEntry(
            transaction=transaction,
            transaction_id=transaction_id,
            received_at=int(time.time()) if received_at is None else received_at,
        )

        pool = self._sender_pools.setdefault(sender, _SenderPool())
        pool.entries[transaction.nonce] = entry
        self._entries_by_id[transaction_id] = entry
        self._id_by_sender_nonce[nonce_key] = transaction_id
        self._recompute_sender_pool(sender, state)
        self.evict(state, current_time=entry.received_at)
        return transaction_id

    def get_transactions_for_mining(
        self, state: State, *, limit: int, current_time: int | None = None
    ) -> list[Transaction]:
        """Return up to `limit` ready transactions, prioritized by fee."""
        if limit <= 0:
            return []

        now = int(time.time()) if current_time is None else current_time
        self.evict(state, current_time=now)

        sender_ready: dict[str, list[_PoolEntry]] = {}
        for sender, pool in self._sender_pools.items():
            self._recompute_sender_pool(sender, state)
            ready_entries = sorted(
                (pool.entries[nonce] for nonce in pool.ready_nonces),
                key=lambda entry: entry.transaction.nonce,
            )
            if ready_entries:
                sender_ready[sender] = ready_entries

        heap: list[tuple[int, int, str, int]] = []
        for sender, entries in sender_ready.items():
            first = entries[0]
            heappush(heap, (-first.fee, first.transaction.nonce, sender, 0))

        selected: list[Transaction] = []
        while heap and len(selected) < limit:
            _neg_fee, _nonce, sender, index = heappop(heap)
            entry = sender_ready[sender][index]
            selected.append(entry.transaction)

            next_index = index + 1
            if next_index < len(sender_ready[sender]):
                nxt = sender_ready[sender][next_index]
                heappush(heap, (-nxt.fee, nxt.transaction.nonce, sender, next_index))

        return selected

    def remove_confirmed_transactions(
        self,
        transactions: Iterable[Transaction],
        state: State,
    ) -> None:
        """Remove transactions confirmed in a block and revalidate sender queues."""
        touched_senders: set[str] = set()
        for transaction in transactions:
            transaction_id = transaction.transaction_id().hex()
            entry = self._entries_by_id.get(transaction_id)
            if entry is None:
                continue
            touched_senders.add(entry.transaction.sender)
            self._remove_entry(entry)

        for sender in touched_senders:
            self._recompute_sender_pool(sender, state)

        for sender in list(self._sender_pools):
            self._recompute_sender_pool(sender, state)

    def evict(self, state: State, *, current_time: int | None = None) -> list[str]:
        """Evict stale transactions and, if oversized, low-fee transactions."""
        now = int(time.time()) if current_time is None else current_time
        evicted_ids: list[str] = []

        stale_ids = [
            tx_id
            for tx_id, entry in self._entries_by_id.items()
            if now - entry.received_at > self.max_age_seconds
        ]
        for tx_id in stale_ids:
            entry = self._entries_by_id.get(tx_id)
            if entry is None:
                continue
            evicted_ids.append(tx_id)
            self._remove_entry(entry)

        while len(self._entries_by_id) > self.max_size:
            entry = min(
                self._entries_by_id.values(),
                key=lambda item: (item.fee, item.received_at),
            )
            evicted_ids.append(entry.transaction_id)
            self._remove_entry(entry)

        for sender in list(self._sender_pools):
            self._recompute_sender_pool(sender, state)

        return evicted_ids

    def _recompute_sender_pool(self, sender: str, state: State) -> None:
        pool = self._sender_pools.get(sender)
        if pool is None:
            return

        account = state.accounts.get(sender, Account())
        state_nonce = account.nonce
        available_balance = account.balance

        for nonce in [nonce for nonce in pool.entries if nonce < state_nonce]:
            self._remove_entry(pool.entries[nonce])

        pool = self._sender_pools.get(sender)
        if pool is None:
            return

        ready_nonces: set[int] = set()
        expected_nonce = state_nonce
        while expected_nonce in pool.entries:
            candidate = pool.entries[expected_nonce].transaction
            candidate_cost = candidate.amount + candidate.fee
            if candidate_cost > available_balance:
                break
            ready_nonces.add(expected_nonce)
            available_balance -= candidate_cost
            expected_nonce += 1

        all_nonces = set(pool.entries.keys())
        pool.ready_nonces = ready_nonces
        pool.waiting_nonces = all_nonces - ready_nonces

        if not pool.entries:
            self._sender_pools.pop(sender, None)

    def _remove_entry(self, entry: _PoolEntry) -> None:
        transaction = entry.transaction
        sender = transaction.sender
        nonce = transaction.nonce

        self._entries_by_id.pop(entry.transaction_id, None)
        self._id_by_sender_nonce.pop((sender, nonce), None)

        pool = self._sender_pools.get(sender)
        if pool is None:
            return

        pool.entries.pop(nonce, None)
        pool.ready_nonces.discard(nonce)
        pool.waiting_nonces.discard(nonce)
        if not pool.entries:
            self._sender_pools.pop(sender, None)
