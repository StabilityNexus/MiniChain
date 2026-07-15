TProtocol = str

# network_config.py
# This file contains parameters that must be identical across all nodes 
# to maintain consensus.

# P2P Network Rules
SUPPORTED_MESSAGE_TYPES = {"hello", "tx", "block", "chain_request", "chain_response"}
PROTOCOL_ID = TProtocol("/minichain/1.0.0")
MAX_FRAME_BYTES = 1 * 1024 * 1024  # 1 MB

# Consensus Parameters
DEFAULT_MINING_REWARD = 50
MAX_FUTURE_BLOCK_TIME_MS = 15000  # Max allowed ms in the future for a block timestamp
GAS_PER_BYTE = 10  # Cost per byte of state storage written
