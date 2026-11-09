# node_config.py
# This file contains parameters that individual node operators can tweak
# without causing consensus forks.

# P2P Banning Thresholds
MALFORMED_THRESHOLD = 15  # N: accumulated malformed messages before ban
FAILED_THRESHOLD = 15  # M: accumulated failed messages before ban
INVALID_THRESHOLD = 1  # L: accumulated invalid messages before ban (1 = immediate)
DECAY_INTERVAL_MINUTES = 10  # T: counter half-life period in minutes

# Gossip dedup: max tx/block ids remembered per set before the oldest is evicted.
SEEN_CACHE_MAX = 10_000

# Mempool Config
MEMPOOL_MAX_SIZE = 1000
MEMPOOL_TX_PER_BLOCK = 100

# Mining Config
MINING_MAX_NONCE = (
    10_000_000  # Number of hashes to attempt before yielding the mining thread
)

# Initial nonce range parameters (A and B).
# Miners can configure these to create unique search ranges and avoid overlapping work.
MINING_INITIAL_NONCE_MIN = 0

# Recommended upper limit: 2**32 - 1 (4,294,967,295).
# Keeping the upper limit around 32-bits ensures the nonce string in the JSON block
# doesn't become unnecessarily large, and avoids cross-language serialization issues.
MINING_INITIAL_NONCE_MAX = 2**32 - 1

# State Config
MAX_STATE_SNAPSHOTS = (
    10  # Number of recent block states to keep in memory for reorg optimization
)

# Sync Config (node-local policy; not consensus)
# 1000 JSON headers are roughly 540 KB, comfortably below MAX_FRAME_BYTES.
MAX_HEADERS_PER_REQUEST = 1000
MAX_BLOCKS_PER_REQUEST = 64
MAX_LOCATOR_HASHES = 32
# Leave headroom below MAX_FRAME_BYTES (1 MB) for envelope overhead and
# multiple small blocks sharing one response frame.
MAX_SYNC_RESPONSE_BYTES = 768 * 1024
SYNC_REQUEST_TIMEOUT_SECONDS = 30.0
SYNC_REQUESTS_PER_SECOND = 5.0
SYNC_REQUEST_BURST = 10
# Hard cap on peers tracked by the per-peer sync token bucket. Fully refilled
# buckets are dropped first; if every retained bucket is still active, new
# peers are refused rather than resetting an active rate limit.
SYNC_RATE_LIMIT_MAX_PEERS = 1024
# A fork deeper than one header batch keeps accumulating headers until it
# proves more work than our suffix. Cap that retention so a peer cannot make
# us hold an unbounded branch in memory.
MAX_FORK_HEADERS = MAX_HEADERS_PER_REQUEST * 16
# Fork bodies are buffered until resolve_conflicts can validate the branch.
# Cap that buffer independently of the header count so large bodies cannot
# exhaust memory during a deep fork.
MAX_FORK_BODY_BYTES = 64 * 1024 * 1024
# Retry an unanswered sync request before dropping the session. This recovers
# from a receiver that rate-limited (and therefore ignored) one request.
SYNC_REQUEST_RETRY_SECONDS = 5.0
SYNC_REQUEST_MAX_RETRIES = 2
# Request IDs are opaque and echoed back; bound them so a response cannot be
# inflated by a large ID that was not part of the payload budget.
MAX_SYNC_REQUEST_ID_BYTES = 128
# How often the node checks for stalled requests and expires idle limiter state.
SYNC_MAINTENANCE_INTERVAL_SECONDS = 1.0
