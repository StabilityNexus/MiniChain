# node_config.py
# This file contains parameters that individual node operators can tweak 
# without causing consensus forks.

# P2P Banning Thresholds
MALFORMED_THRESHOLD = 15     # N: accumulated malformed messages before ban
FAILED_THRESHOLD = 15        # M: accumulated failed messages before ban
INVALID_THRESHOLD = 1        # L: accumulated invalid messages before ban (1 = immediate)
DECAY_INTERVAL_MINUTES = 10  # T: counter half-life period in minutes

# Gossip dedup: max tx/block ids remembered per set before the oldest is evicted.
SEEN_CACHE_MAX = 10_000

# Mempool Config
MEMPOOL_MAX_SIZE = 1000
MEMPOOL_TX_PER_BLOCK = 100

# Mining Config
MINING_MAX_NONCE = 10_000_000 # Number of hashes to attempt before yielding the mining thread

# Initial nonce range parameters (A and B).
# Miners can configure these to create unique search ranges and avoid overlapping work.
MINING_INITIAL_NONCE_MIN = 0

# Recommended upper limit: 2**32 - 1 (4,294,967,295).
# Keeping the upper limit around 32-bits ensures the nonce string in the JSON block
# doesn't become unnecessarily large, and avoids cross-language serialization issues.
MINING_INITIAL_NONCE_MAX = 2**32 - 1

# State Config
MAX_STATE_SNAPSHOTS = 10     # Number of recent block states to keep in memory for reorg optimization
