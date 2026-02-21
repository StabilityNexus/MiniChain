"""Genesis block/state creation and application."""

from __future__ import annotations

from dataclasses import dataclass, field

from minichain.block import Block, BlockHeader
from minichain.crypto import blake2b_digest
from minichain.state import Account, State

GENESIS_PREVIOUS_HASH = "00" * 32


def _is_lower_hex(value: str, expected_length: int) -> bool:
    if len(value) != expected_length:
        return False
    return all(ch in "0123456789abcdef" for ch in value)


@dataclass(frozen=True)
class GenesisConfig:
    """Configurable parameters for building genesis artifacts."""

    initial_balances: dict[str, int] = field(default_factory=dict)
    timestamp: int = 1_739_000_000
    difficulty_target: int = (1 << 255) - 1
    version: int = 0

    def validate(self) -> None:
        if self.timestamp < 0:
            raise ValueError("Genesis timestamp must be non-negative")
        if self.difficulty_target <= 0:
            raise ValueError("Genesis difficulty_target must be positive")
        for address, balance in self.initial_balances.items():
            if not _is_lower_hex(address, 40):
                raise ValueError(f"Invalid genesis address: {address}")
            if balance < 0:
                raise ValueError(f"Negative genesis balance for {address}")


def create_genesis_block(config: GenesisConfig) -> Block:
    """Build the genesis block (height 0, no PoW check required)."""
    config.validate()
    header = BlockHeader(
        version=config.version,
        previous_hash=GENESIS_PREVIOUS_HASH,
        merkle_root=blake2b_digest(b"").hex(),
        timestamp=config.timestamp,
        difficulty_target=config.difficulty_target,
        nonce=0,
        block_height=0,
    )
    return Block(header=header, transactions=[])


def apply_genesis_block(state: State, block: Block, config: GenesisConfig) -> None:
    """Apply genesis allocations to an empty state."""
    config.validate()
    if state.accounts:
        raise ValueError("Genesis can only be applied to an empty state")
    if block.header.block_height != 0:
        raise ValueError("Genesis block height must be 0")
    if block.header.previous_hash != GENESIS_PREVIOUS_HASH:
        raise ValueError("Genesis previous_hash must be all zeros")
    if block.transactions:
        raise ValueError("Genesis block must not contain transactions")

    expected_merkle_root = blake2b_digest(b"").hex()
    if block.header.merkle_root != expected_merkle_root:
        raise ValueError("Genesis merkle_root must commit to an empty tx list")

    for address, balance in config.initial_balances.items():
        state.set_account(address, Account(balance=balance, nonce=0))


def create_genesis_state(config: GenesisConfig) -> tuple[Block, State]:
    """Create genesis block and initialized state in one step."""
    block = create_genesis_block(config)
    state = State()
    apply_genesis_block(state, block, config)
    return block, state
