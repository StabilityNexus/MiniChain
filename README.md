# MiniChain

MiniChain is a minimal, research-oriented blockchain implementation in Python.
It includes a full v0 core-chain pipeline with account-based state, PoW mining,
P2P propagation/sync primitives, SQLite persistence, and a CLI.

## Implemented v0 Scope

- Ed25519 identities and signatures (`src/minichain/crypto.py`)
- Deterministic serialization (`src/minichain/serialization.py`)
- Transactions, Merkle trees, blocks (`src/minichain/transaction.py`, `src/minichain/merkle.py`, `src/minichain/block.py`)
- Account state transition and chain manager with fork/reorg handling (`src/minichain/state.py`, `src/minichain/chain.py`)
- PoW mining and block construction (`src/minichain/consensus.py`, `src/minichain/mining.py`)
- Mempool with per-sender nonce queueing (`src/minichain/mempool.py`)
- SQLite persistence and restart recovery (`src/minichain/storage.py`)
- Node orchestration and CLI (`src/minichain/node.py`, `src/minichain/__main__.py`)
- Networking module with:
  - peer discovery/bootstrap
  - transaction gossip
  - block propagation
  - range-based chain synchronization
  (`src/minichain/network.py`)

## Requirements

- Python 3.11+

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
make dev-install
```

Optional:

```bash
python -m pip install -e .[network]
```

## Development Commands

```bash
make test
make lint
make format
make start-node
```

## CLI End-to-End Verification

Run this full flow in one shell session.

```bash
source .venv/bin/activate
export PYTHONPATH=src
rm -rf .demo
```

Generate 3 keypairs (miner, sender, recipient):

```bash
MINER_OUT=$(python -m minichain generate-key)
SENDER_OUT=$(python -m minichain generate-key)
RECIP_OUT=$(python -m minichain generate-key)

MINER_ADDR=$(echo "$MINER_OUT" | awk -F= '/^address=/{print $2}')
SENDER_ADDR=$(echo "$SENDER_OUT" | awk -F= '/^address=/{print $2}')
SENDER_PK=$(echo "$SENDER_OUT" | awk -F= '/^private_key=/{print $2}')
RECIP_ADDR=$(echo "$RECIP_OUT" | awk -F= '/^address=/{print $2}')
```

Sanity-check parsed values (must not be empty):

```bash
echo "MINER_ADDR=$MINER_ADDR"
echo "SENDER_ADDR=$SENDER_ADDR"
echo "SENDER_PK_LEN=${#SENDER_PK}"
echo "RECIP_ADDR=$RECIP_ADDR"
```

Check startup/genesis status:

```bash
python -m minichain --data-dir .demo start
python -m minichain --data-dir .demo chain-info
```

Mine rewards to sender and check balance:

```bash
python -m minichain --data-dir .demo --miner-address "$SENDER_ADDR" mine --count 2
python -m minichain --data-dir .demo balance --address "$SENDER_ADDR"
```

Submit transfer (auto-mines one block by default):

```bash
python -m minichain --data-dir .demo submit-tx \
  --private-key "$SENDER_PK" \
  --recipient "$RECIP_ADDR" \
  --amount 7 \
  --fee 1
```

Verify balances and blocks:

```bash
python -m minichain --data-dir .demo balance --address "$SENDER_ADDR"
python -m minichain --data-dir .demo balance --address "$RECIP_ADDR"
python -m minichain --data-dir .demo chain-info
python -m minichain --data-dir .demo block --height 1
python -m minichain --data-dir .demo block --height 2
python -m minichain --data-dir .demo block --height 3
```

Restart check (persistence):

```bash
python -m minichain --data-dir .demo start
python -m minichain --data-dir .demo chain-info
```

Negative-path checks:

```bash
python -m minichain --data-dir .demo balance --address bad || true
python -m minichain --data-dir .demo submit-tx \
  --private-key "$SENDER_PK" \
  --recipient "$RECIP_ADDR" \
  --amount -1 \
  --fee 1 || true
python -m minichain --data-dir .demo submit-tx \
  --private-key "$SENDER_PK" \
  --recipient "$RECIP_ADDR" \
  --amount 1 \
  --fee 1 \
  --nonce 0 || true
```

## Architecture and Roadmap Docs

- `implementation.md`: implementation details for all components
- `docs/issues.md`: issue-by-issue roadmap
- `docs/architectureProposal.md`: full architecture proposal
