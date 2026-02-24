# MiniChain Implementation Reference

This document describes the current implementation of MiniChain v0 component by
component.

## 1. Crypto Layer (`src/minichain/crypto.py`)

- Uses PyNaCl (libsodium bindings) for:
  - Ed25519 key generation/sign/verify
  - BLAKE2b hashing
- Provides:
  - `generate_key_pair()`
  - `derive_address(verify_key)` (first 20 bytes of BLAKE2b digest)
  - key serialization/deserialization helpers
  - detached signature helpers (`sign_message`, `verify_signature`)
- Fails fast with actionable error if PyNaCl is missing.

## 2. Canonical Serialization (`src/minichain/serialization.py`)

- Deterministic UTF-8 JSON serialization with strict field sets/order.
- Rejects missing/extra fields to avoid consensus divergence.
- Consensus-critical helpers:
  - `serialize_transaction(...)`
  - `serialize_block_header(...)`

## 3. Transaction Model (`src/minichain/transaction.py`)

- `Transaction` fields:
  - sender, recipient, amount, nonce, fee, timestamp
  - signature, public_key
- Coinbase support:
  - canonical coinbase sender constant
  - `create_coinbase_transaction(...)`
  - coinbase shape validation (no signature/public key, nonce=0, fee=0)
- Signing and verification:
  - verifies signature bytes
  - verifies sender matches derived address from included public key
- Deterministic `transaction_id()` includes signing payload + auth fields.

## 4. Merkle Layer (`src/minichain/merkle.py`)

- Computes deterministic Merkle root for transaction-id bytes.
- Handles empty and odd-length input sets deterministically.

## 5. Block Layer (`src/minichain/block.py`)

- `BlockHeader` contains version, previous hash, Merkle root, timestamp,
  difficulty target, nonce, block height.
- Block hash is BLAKE2b over canonical header serialization.
- `Block` supports:
  - transaction hash extraction
  - computed/header Merkle root consistency checks
  - coinbase placement and amount validation (`reward + fees`)

## 6. Consensus / PoW (`src/minichain/consensus.py`)

- PoW rule: `int(hash(header)) <= difficulty_target`
- Difficulty target bounds checked against 256-bit range.
- Retargeting:
  - interval-based proportional adjustment
  - bounded by 0.5x to 2x per interval
- Mining:
  - nonce search over configured range
  - optional interruption via `threading.Event`

## 7. State Machine (`src/minichain/state.py`)

- Account model: `{address -> (balance, nonce)}`
- Transaction application:
  - validates sender/recipient
  - enforces exact nonce progression
  - enforces sufficient balance for `amount + fee`
- Block application:
  - validates coinbase and non-coinbase tx list
  - applies transfers atomically
  - supports rollback behavior on failure

## 8. Mempool (`src/minichain/mempool.py`)

- Validates tx before acceptance (signature/identity/nonce/balance checks).
- Per-sender nonce queueing:
  - tracks ready vs waiting nonces
  - recomputes readiness against latest state
- Selection for mining:
  - fee-prioritized across senders
  - preserves per-sender nonce ordering
- Eviction:
  - by staleness (age)
  - by lowest fee when over capacity

## 9. Chain Manager / Fork Choice (`src/minichain/chain.py`)

- Tracks all known blocks and canonical path.
- Validates parent linkage, expected height, target, Merkle, and PoW.
- Replays candidate path from genesis state to evaluate fork validity.
- Fork choice:
  - extend tip when direct valid successor
  - reorg to longer valid chain
  - store shorter forks without reorg

## 10. Genesis (`src/minichain/genesis.py`)

- Configurable genesis timestamp/target/version and initial balances.
- Builds genesis block with empty tx commitment.
- Applies initial account allocations into empty state.

## 11. Mining Orchestration (`src/minichain/mining.py`)

- `build_candidate_block(...)`:
  - selects mempool txs
  - computes fees
  - inserts coinbase tx
  - computes next target and Merkle root
- `mine_candidate_block(...)`:
  - finds valid nonce
  - returns mined block with immutable tx body copy

## 12. Persistence (`src/minichain/storage.py`)

- SQLite-backed storage for:
  - blocks (by hash and by height)
  - state snapshot (accounts)
  - chain metadata (height, tip hash)
- Supports atomic persistence of block + state + metadata.
- On startup/recovery, node replays persisted chain and verifies snapshot
  consistency.

## 13. Network Layer (`src/minichain/network.py`)

- Peer discovery and connectivity:
  - TCP server/client handshake (`hello`)
  - bootstrap peer connections
  - peer exchange (`peers`)
  - multicast mDNS-style announcements with local fallback
- Transaction gossip protocol:
  - `/minichain/tx/1.0.0`
  - dedup cache for seen transaction ids
  - forwards valid unseen tx to peers (excluding source peer)
- Block propagation protocol:
  - `/minichain/block/1.0.0`
  - block payload serialization + Merkle/hash validation
  - seen-block dedup and forwarding logic
- Sync protocol:
  - `/minichain/sync/1.0.0`
  - `sync_status` (height announce)
  - `sync_request` (range by height)
  - `sync_blocks` (batched block responses)
  - catch-up loop with configurable batch size

## 14. Node Orchestration (`src/minichain/node.py`)

- `MiniChainNode` composes:
  - chain manager
  - mempool
  - storage
- Startup:
  - validate config
  - open/create sqlite db
  - load/create genesis
  - replay persisted blocks and verify canonical state
- Runtime APIs:
  - `submit_transaction(...)`
  - `accept_block(...)`
  - `mine_one_block(...)`
- Shutdown closes persistence cleanly.

## 15. CLI (`src/minichain/__main__.py`)

- Commands:
  - `start`
  - `generate-key`
  - `balance --address`
  - `submit-tx --private-key --recipient --amount --fee [--nonce]`
  - `mine --count [--max-transactions]`
  - `block --height | --hash`
  - `chain-info`
- Each invocation runs as a command-style operation:
  - starts node context
  - performs action
  - stops cleanly
- Persistence is controlled by `--data-dir`, so repeated commands share chain
  state.

## 16. Test Coverage (`tests/`)

- Unit coverage spans all core modules.
- Integration coverage includes:
  - peer discovery
  - tx gossip
  - block propagation
  - chain sync
  - comprehensive multi-node convergence, fork/reorg, and double-spend rejection

Collectively, this forms a complete v0 educational blockchain implementation
with deterministic behavior and reproducible local testing via CLI.
