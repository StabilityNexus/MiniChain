# node_config.py
# This file contains parameters that individual node operators can tweak 
# without causing consensus forks.

# P2P Banning Thresholds
MALFORMED_THRESHOLD = 15     # N: accumulated malformed messages before ban
FAILED_THRESHOLD = 15        # M: accumulated failed messages before ban
INVALID_THRESHOLD = 1        # L: accumulated invalid messages before ban (1 = immediate)
DECAY_INTERVAL_MINUTES = 10  # T: counter half-life period in minutes

# Mempool Config
MEMPOOL_MAX_SIZE = 1000
MEMPOOL_TX_PER_BLOCK = 100

# Mining Config
MINING_MAX_NONCE = 10_000_000 # Number of hashes to attempt before yielding the mining thread
