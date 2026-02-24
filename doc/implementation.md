# MiniChain Implementation Reference

This document summarizes the current MiniChain v0 implementation by subsystem.

## 1) Crypto and Identity (`src/minichain/crypto.py`)

- Ed25519 key generation/sign/verify through PyNaCl.
- Address derivation from public key digest (20-byte lowercase hex address).
- Serialization helpers for private/public keys.

## 2) Canonical Serialization (`src/minichain/serialization.py`)

- Deterministic JSON serialization for consensus-critical payloads.
- Strict field presence/order checks to avoid cross-node divergence.

## 3) Transactions (`src/minichain/transaction.py`)

- Account-model transfer transaction with: sender, recipient, amount, nonce, fee, timestamp.
- Signature + public key included for validation.
- Coinbase transaction shape and validation helpers.
- Deterministic transaction id generation.

## 4) Merkle and Blocks (`src/minichain/merkle.py`, `src/minichain/block.py`)

- Deterministic Merkle root construction for block transaction lists.
- Block header contains parent hash, height, timestamp, target, nonce, root.
- Block hash = canonical header hash.
- Header/body consistency checks for Merkle root.

## 5) Consensus and Mining (`src/minichain/consensus.py`, `src/minichain/mining.py`)

- Proof-of-work target validation and nonce search.
- Difficulty bounds and retarget policy.
- Candidate block building from mempool with coinbase reward + fees.
- Interrupt-capable mining loop support.

## 6) State and Chain Logic (`src/minichain/state.py`, `src/minichain/chain.py`, `src/minichain/genesis.py`)

- Account state tracks balance and nonce per address.
- Deterministic tx apply rules: nonce progression, amount+fee affordability, signer identity.
- Canonical chain selection by valid longest chain.
- Reorg support with state replay and validation.
- Configurable genesis creation and initial state.

## 7) Mempool (`src/minichain/mempool.py`)

- Signature/identity checks before acceptance.
- Per-sender nonce queueing (ready vs waiting).
- Mining selection prioritizes fee while preserving sender nonce order.
- Eviction by age and capacity.

## 8) Persistence (`src/minichain/storage.py`)

- SQLite storage for blocks, chain metadata, and state snapshots.
- Canonical head persistence and restart recovery.
- Replay + snapshot consistency checks on startup.

## 9) Node Orchestration (`src/minichain/node.py`)

- `MiniChainNode` composes chain manager, mempool, and storage.
- APIs for transaction submission, block acceptance, and mining.
- Clean startup/shutdown lifecycle with config validation.

## 10) Networking (`src/minichain/network.py`)

- TCP handshake (`hello`) and peer management.
- Peer discovery via bootstrap and mDNS/local discovery fallback.
- Gossip protocols:
  - `/minichain/tx/1.0.0`
  - `/minichain/block/1.0.0`
- Sync protocol (`/minichain/sync/1.0.0`) with range requests.
- Block/tx dedup caches.
- Automatic reconnect loop to bootstrap/known peers.
- Transaction gossip returns explicit `tx_result` ack (accepted/reason).

## 11) CLI (`src/minichain/__main__.py`)

Primary command groups:

- `node start`
- `node run [--peer host:port] [--mine]`
- `node stop`
- `wallet generate-key|balance|details|list`
- `tx submit`
- `chain info|block|accounts`
- `mine`
- `shell`

Current daemon-aware behavior:

- If `node run` is active for the same `--data-dir`, `tx submit` routes over network to the running daemon.
- `chain info` includes `connected_peers`.
- Daemon status logging includes `height`, `tip`, `mempool_size`, `connected_peers`.

## 12) Test Coverage (`tests/`)

- Unit tests for core primitives and validation logic.
- Integration tests for:
  - peer discovery
  - tx gossip
  - block propagation
  - chain sync/catch-up
  - fork/reorg and convergence scenarios

MiniChain v0 currently provides a complete educational blockchain node with deterministic behavior, CLI-driven flows, and reproducible local/multi-node testing.
