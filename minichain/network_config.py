TProtocol = str

# network_config.py
# This file contains parameters that must be identical across all nodes 
# to maintain consensus.

# P2P Network Rules
SUPPORTED_MESSAGE_TYPES = {"hello", "tx", "block", "chain_request", "chain_response", "getaddr", "addr"}
PROTOCOL_ID = TProtocol("/minichain/1.0.0")
MAX_FRAME_BYTES = 1 * 1024 * 1024  # 1 MB

# Consensus Parameters
DEFAULT_MINING_REWARD = 50
MAX_FUTURE_BLOCK_TIME_MS = 15000  # Max allowed ms in the future for a block timestamp
GAS_PER_BYTE = 10  # Cost per byte of state storage written
MAX_CALL_DEPTH = 10  # Maximum depth for cross-contract calls
MAX_TARGET = int("F" * 64, 16)
MIN_TARGET = 1

# Difficulty-adjustment bounds. These are consensus-critical: changing them
# changes which blocks are accepted by the network.
#
# The EMA target may move by at most this percentage in one block. Without a
# bound, a single unusually short timestamp interval can move the target by
# several orders of magnitude.
MAX_TARGET_CHANGE_PERCENT = 25
#
# Difficulty (1 / target) may not fall below this fraction of the genesis
# difficulty. In target terms the easiest allowed target is
# genesis_target * MIN_DIFFICULTY_FLOOR_DIVISOR, capped at MAX_TARGET.
MIN_DIFFICULTY_FLOOR_DIVISOR = 4
