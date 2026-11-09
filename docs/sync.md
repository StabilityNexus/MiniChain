# Headers-first sync

MiniChain syncs in two stages:

1. Ask a peer for the **headers** that follow a block we already know.
2. Validate those headers, then download their **bodies** in bounded batches.

Headers are small and cheap to verify. A peer must prove chain linkage,
proof-of-work, and plausible timestamps before we spend bandwidth on
transactions and receipts. Bodies are applied exactly as before, so consensus
rules have not changed.

## Messages

The sync protocol uses four message types. All payloads are JSON, and responses
echo the requester's `request_id`.

### `getheaders` → `headers`

```json
{
  "type": "getheaders",
  "data": {
    "request_id": "5f0c...",
    "locator": ["<newest known block>", "...", "<genesis>"],
    "max": 1000
  }
}
```

```json
{
  "type": "headers",
  "data": {
    "request_id": "5f0c...",
    "headers": [
      {
        "index": 42,
        "previous_hash": "...",
        "merkle_root": "...",
        "state_root": "...",
        "receipt_root": "...",
        "timestamp": 1716880000000,
        "target": "0x00ff...",
        "nonce": 12345,
        "miner": "...",
        "hash": "..."
      }
    ]
  }
}
```

The responder finds the first hash in `locator` that it knows and returns the
headers after it. A locator is newest-first with exponentially spaced entries,
anchored at genesis, so it stays small even on a long chain.

### `getblocks` → `blocks`

```json
{
  "type": "getblocks",
  "data": {
    "request_id": "9ab1...",
    "hashes": ["<header hash>", "..."]
  }
}
```

```json
{
  "type": "blocks",
  "data": {
    "request_id": "9ab1...",
    "blocks": [
      {
        "index": 42,
        "previous_hash": "...",
        "target": "0x00ff...",
        "transactions": [],
        "receipts": [],
        "hash": "..."
      }
    ]
  }
}
```

A response is always a contiguous prefix of the requested hashes. It may be
shorter than the request when a block is unknown or when the response would
exceed the frame budget. The requester continues from the last received block.

## Validation

Header validation happens before any body is requested:

- header hash matches the header fields;
- `index` and `previous_hash` link to the known parent;
- `target` is within `[MIN_TARGET, MAX_TARGET]`;
- `int(hash, 16) < target` proves the declared proof-of-work;
- timestamps strictly increase and are not too far in the future;
- the advertised headers represent more work than our current suffix.

Body application then enforces all remaining consensus rules: transactions,
receipts, state roots, and the exact EMA difficulty target for each height.

MiniChain does not persist the EMA target and average block time for every
historical block. A fork's exact target sequence therefore cannot be
reconstructed at the header stage, so it is checked when the fork bodies are
applied. This keeps the header stage cheap without weakening consensus.

## Limits and peer behaviour

Defaults live in [`minichain/node_config.py`](../minichain/node_config.py):

| Setting | Default | Purpose |
| --- | --- | --- |
| `MAX_HEADERS_PER_REQUEST` | 1000 | Header batch cap (about 540 KB of JSON) |
| `MAX_BLOCKS_PER_REQUEST` | 64 | Body batch cap |
| `MAX_LOCATOR_HASHES` | 32 | Locator cap |
| `MAX_SYNC_RESPONSE_BYTES` | 768 KiB | Response byte budget, below the 1 MiB frame limit |
| `SYNC_REQUEST_TIMEOUT_SECONDS` | 30 | Drop a stalled sync session |
| `SYNC_REQUESTS_PER_SECOND` / `SYNC_REQUEST_BURST` | 5 / 10 | Per-peer token bucket for serving requests and pacing outbound requests |
| `SYNC_REQUEST_RETRY_SECONDS` / `SYNC_REQUEST_MAX_RETRIES` | 5 / 2 | Retry an unanswered request before dropping the session |
| `MAX_FORK_HEADERS` | 16,000 | Cap on headers retained while following a fork |
| `MAX_FORK_BODY_BYTES` | 64 MiB | Cap on bodies buffered while following a fork |

A request's `max` field and a legacy `chain_request.limit` are clamped to their
configured ranges, so an out-of-range value alone is not a validation failure.
Oversized locator or hash lists and malformed sync payloads are returned to the
P2P layer as validation statuses, so existing peer scoring and banning apply. A
peer that advertises a chain with no more work than ours is ignored rather than
punished.

Requests are answered in bounded batches. A response may be shorter than the
requested count because of the byte budget, and the requester continues from the
last received item; an empty header batch ends the sync. Outbound requests are
paced against the same per-peer budget, and a request that would exceed it is
queued briefly instead of being dropped. Unanswered requests are retried before
a stalled session is dropped; earlier attempt IDs stay valid until the session
deadline so a late response can still complete the sync. A single valid header
or block may exceed the soft response budget when its complete frame still fits
within `MAX_FRAME_BYTES`.

A fork that exceeds `MAX_FORK_HEADERS` or `MAX_FORK_BODY_BYTES` is dropped
without penalizing the peer: the caps are local retention policy, not protocol
violations. If the local chain gains work while fork bodies are downloading, the
stale comparison restarts sync instead of treating the peer as invalid.

## Reorgs

When the common ancestor is not our tip, headers describe a competing branch.
If that branch spans more than one header batch, the headers are accumulated
across requests until the branch proves more work than our suffix. Bodies are
only downloaded after that point. They are buffered and handed to
`Blockchain.resolve_conflicts`. If the branch is heavier and valid, the chain
switches and transactions that are no longer confirmed are returned to the
mempool. Otherwise the session is dropped.

## Compatibility

`chain_request` and `chain_response` are still accepted for older peers:

- A new node does not initiate legacy sync.
- When a new node receives a legacy `chain_request`, it answers with a
  `chain_response` containing at most `MAX_BLOCKS_PER_REQUEST` blocks, so an old
  peer cannot ask for an unbounded response.
- A legacy `chain_response` is ignored because this node no longer initiates
  that request.

Old nodes cannot serve `getheaders` and therefore cannot be used as sync
sources by a new node. They can still download blocks from a new node through
the legacy path.

## Tests

`tests/test_sync.py` covers header serialization and validation, locator
spacing, request and response bounds, rate limiting, linear sync with header and
body pagination, early rejection of invalid headers, and fork/reorg sync.
