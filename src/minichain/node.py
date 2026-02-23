"""Node orchestration layer for MiniChain."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from minichain.block import Block
from minichain.chain import ChainConfig, ChainManager, ChainValidationError
from minichain.genesis import GenesisConfig, create_genesis_state
from minichain.mempool import Mempool, MempoolValidationError
from minichain.mining import build_candidate_block, mine_candidate_block
from minichain.state import State
from minichain.storage import SQLiteStorage, StorageError
from minichain.transaction import ADDRESS_HEX_LENGTH, Transaction


class NodeError(ValueError):
    """Raised when node lifecycle or orchestration operations fail."""


@dataclass(frozen=True)
class NodeConfig:
    """Runtime configuration for a MiniChain node."""

    data_dir: Path | str
    database_filename: str = "chain.sqlite3"
    miner_address: str | None = None
    max_block_transactions: int = 1_000
    mempool_max_size: int = 1_000
    mempool_max_age_seconds: int = 3_600
    genesis_config: GenesisConfig = field(default_factory=GenesisConfig)
    chain_config: ChainConfig = field(default_factory=ChainConfig)

    def validate(self) -> None:
        if self.max_block_transactions < 0:
            raise NodeError("max_block_transactions must be non-negative")
        if self.mempool_max_size <= 0:
            raise NodeError("mempool_max_size must be positive")
        if self.mempool_max_age_seconds <= 0:
            raise NodeError("mempool_max_age_seconds must be positive")
        if self.miner_address is not None and not _is_lower_hex(
            self.miner_address, ADDRESS_HEX_LENGTH
        ):
            raise NodeError("miner_address must be a 20-byte lowercase hex string")
        self.genesis_config.validate()
        self.chain_config.validate()


class MiniChainNode:
    """Top-level node that coordinates chain, mempool, mining, and storage."""

    def __init__(self, config: NodeConfig) -> None:
        self.config = config
        self.config.validate()

        self._storage: SQLiteStorage | None = None
        self._chain_manager: ChainManager | None = None
        self._mempool: Mempool | None = None
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def chain_manager(self) -> ChainManager:
        if self._chain_manager is None:
            raise NodeError("Node is not started")
        return self._chain_manager

    @property
    def mempool(self) -> Mempool:
        if self._mempool is None:
            raise NodeError("Node is not started")
        return self._mempool

    @property
    def storage(self) -> SQLiteStorage:
        if self._storage is None:
            raise NodeError("Node is not started")
        return self._storage

    @property
    def height(self) -> int:
        return self.chain_manager.height

    @property
    def tip_hash(self) -> str:
        return self.chain_manager.tip_hash

    def start(self) -> None:
        """Start node components and load or initialize persistent chain state."""
        if self._running:
            return

        data_dir = Path(self.config.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / self.config.database_filename

        storage = SQLiteStorage(db_path)
        chain_manager = self._initialize_chain_manager(storage)
        mempool = Mempool(
            max_size=self.config.mempool_max_size,
            max_age_seconds=self.config.mempool_max_age_seconds,
        )

        self._storage = storage
        self._chain_manager = chain_manager
        self._mempool = mempool
        self._running = True

    def stop(self) -> None:
        """Stop node components and close persistent resources."""
        if not self._running:
            return
        try:
            if self._storage is not None:
                self._storage.close()
        finally:
            self._storage = None
            self._chain_manager = None
            self._mempool = None
            self._running = False

    def submit_transaction(self, transaction: Transaction) -> str:
        """Validate and enqueue a transaction into the mempool."""
        self._require_started()
        try:
            return self.mempool.add_transaction(transaction, self.chain_manager.state)
        except MempoolValidationError as exc:
            raise NodeError(f"Transaction rejected by mempool: {exc}") from exc

    def accept_block(self, block: Block) -> str:
        """Validate and apply a block; persist state on canonical updates."""
        self._require_started()
        try:
            result = self.chain_manager.add_block(block)
        except ChainValidationError as exc:
            raise NodeError(f"Block rejected: {exc}") from exc

        if result in {"extended", "reorg"}:
            self.mempool.remove_confirmed_transactions(block.transactions, self.chain_manager.state)
            self._persist_head()

        return result

    def mine_one_block(
        self,
        *,
        timestamp: int | None = None,
        max_nonce: int = (1 << 64) - 1,
        max_transactions: int | None = None,
    ) -> Block:
        """Build, mine, and apply one block on top of the canonical tip."""
        self._require_started()
        miner_address = self.config.miner_address
        if miner_address is None:
            raise NodeError("miner_address must be configured to mine blocks")

        limit = (
            self.config.max_block_transactions
            if max_transactions is None
            else max_transactions
        )
        candidate = build_candidate_block(
            chain_manager=self.chain_manager,
            mempool=self.mempool,
            miner_address=miner_address,
            max_transactions=limit,
            timestamp=timestamp,
        )
        mined_block, _digest = mine_candidate_block(block_template=candidate, max_nonce=max_nonce)
        result = self.accept_block(mined_block)
        if result not in {"extended", "reorg"}:
            raise NodeError(f"Mined block was not canonicalized: {result}")
        return mined_block

    def _persist_head(self) -> None:
        try:
            self.storage.persist_block_state_and_metadata(
                block=self.chain_manager.tip_block,
                state=self.chain_manager.state,
                height=self.chain_manager.height,
                head_hash=self.chain_manager.tip_hash,
            )
        except StorageError as exc:
            raise NodeError(f"Failed to persist canonical head: {exc}") from exc

    def _initialize_chain_manager(self, storage: SQLiteStorage) -> ChainManager:
        metadata = storage.load_chain_metadata()
        genesis_block, genesis_state = create_genesis_state(self.config.genesis_config)
        manager = ChainManager(
            genesis_block=genesis_block,
            genesis_state=genesis_state,
            config=self.config.chain_config,
        )

        if metadata is None:
            storage.persist_block_state_and_metadata(
                block=manager.tip_block,
                state=manager.state,
                height=0,
                head_hash=manager.tip_hash,
            )
            return manager

        stored_genesis = storage.get_block_by_height(0)
        if stored_genesis is None:
            raise NodeError("Storage metadata exists but genesis block is missing")
        if stored_genesis.hash().hex() != manager.tip_hash:
            raise NodeError("Stored genesis does not match configured genesis")

        target_height = int(metadata["height"])
        for height in range(1, target_height + 1):
            block = storage.get_block_by_height(height)
            if block is None:
                raise NodeError(f"Missing persisted block at height {height}")
            result = manager.add_block(block)
            if result not in {"extended", "reorg"}:
                raise NodeError(
                    f"Unexpected replay result at height {height}: {result}"
                )

        expected_head_hash = str(metadata["head_hash"])
        if manager.tip_hash != expected_head_hash:
            raise NodeError(
                "Persisted head hash mismatch: "
                f"expected {expected_head_hash}, got {manager.tip_hash}"
            )

        persisted_state = storage.load_state()
        if not _states_equal(persisted_state, manager.state):
            raise NodeError("Persisted state does not match replayed canonical state")

        return manager

    def _require_started(self) -> None:
        if not self._running:
            raise NodeError("Node is not started")


def start_node(host: str, port: int) -> None:
    """Start a MiniChain node with local defaults and print its status."""
    data_dir = Path(".minichain")
    default_config = NodeConfig(data_dir=data_dir)
    node = MiniChainNode(default_config)
    node.start()
    try:
        print(f"MiniChain node started on {host}:{port}")
        print(f"chain_height={node.height} tip={node.tip_hash}")
    finally:
        node.stop()


def _is_lower_hex(value: str, expected_length: int) -> bool:
    if len(value) != expected_length:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


def _states_equal(left: State, right: State) -> bool:
    left_accounts = {
        address: (account.balance, account.nonce)
        for address, account in left.accounts.items()
    }
    right_accounts = {
        address: (account.balance, account.nonce)
        for address, account in right.accounts.items()
    }
    return left_accounts == right_accounts
