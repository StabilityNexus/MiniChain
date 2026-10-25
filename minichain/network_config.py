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
CONTRACT_EXEC_TIMEOUT_SECONDS = 5  # Max time a contract may run once its sandbox process is up (sub-calls excluded)
CONTRACT_STARTUP_TIMEOUT_SECONDS = 60  # Max time to wait for the sandbox process to start (interpreter + imports)
MAX_TARGET = int("F" * 64, 16)
MIN_TARGET = 1

