import json
import time
import hashlib


class MiningExceededError(Exception):
    """Raised when max_nonce, timeout, or cancellation is exceeded during mining."""


def calculate_hash(block_dict):
    """Calculates SHA256 hash of a block header."""
    block_string = json.dumps(block_dict, sort_keys=True).encode("utf-8")
    return hashlib.sha256(block_string).hexdigest()


def mine_block(
    block,
    difficulty=4,
    max_nonce=10_000_000,
    timeout_seconds=None,
    logger=None,
    progress_callback=None
):
    """Mines a block using Proof-of-Work without mutating input block until success."""

    if not isinstance(difficulty, int) or difficulty <= 0:
        raise ValueError("Difficulty must be a positive integer.")

    target = "0" * difficulty
    local_nonce = 0
    header_dict = block.to_header_dict() # Construct header dict once outside loop
    start_time = time.monotonic()

    if logger:
        logger.info(
            "Mining block %s (Difficulty: %s)",
            block.index,
            difficulty,
        )

    while True:

        # Enforce max_nonce limit before hashing
        if local_nonce >= max_nonce:
            if logger:
                logger.warning("Max nonce exceeded during mining.")
            raise MiningExceededError("Mining failed: max_nonce exceeded")

        # Enforce timeout if specified
        if timeout_seconds is not None and (time.monotonic() - start_time) > timeout_seconds:
            if logger:
                logger.warning("Mining timeout exceeded.")
            raise MiningExceededError("Mining failed: timeout exceeded")

        header_dict["nonce"] = local_nonce
        block_hash = calculate_hash(header_dict)

        # Check difficulty target
        if block_hash.startswith(target):
            block.nonce = local_nonce  # Assign only on success
            block.hash = block_hash
            if logger:
                logger.info("Success! Hash: %s", block_hash)
            return block

        # Allow cancellation via progress callback (pass nonce explicitly)
        if progress_callback:
            should_continue = progress_callback(local_nonce, block_hash)
            if should_continue is False:
                if logger:
                    logger.info("Mining cancelled via progress_callback.")
                raise MiningExceededError("Mining cancelled")

        # Increment nonce after attempt
        local_nonce += 1
