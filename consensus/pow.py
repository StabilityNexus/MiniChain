import json
from nacl.hash import sha256
from nacl.encoding import HexEncoder


class MiningExceededError(Exception):
    """Raised when max_nonce is exceeded during mining."""
    pass


def calculate_hash(block_dict):
    """Calculates SHA256 hash of a block header."""
    block_string = json.dumps(block_dict, sort_keys=True).encode("utf-8")
    return sha256(block_string, encoder=HexEncoder).decode("utf-8")


def mine_block(
    block,
    difficulty=4,
    max_nonce=None,
    logger=None,
    progress_callback=None
):
    """
    Mines a block using Proof-of-Work.

    Parameters:
        block               - Block object
        difficulty          - Number of leading zeros required
        max_nonce           - Optional upper bound for nonce attempts
        logger              - Optional logger instance
        progress_callback   - Optional callback(block, hash)

    Returns:
        Mined block if successful

    Raises:
        MiningExceededError if max_nonce is reached
    """

    target = "0" * difficulty
    block.nonce = 0

    if logger:
        logger.info(f"Mining block {block.index} (Difficulty: {difficulty})")

    while True:

        # Check max_nonce limit
        if max_nonce is not None and block.nonce > max_nonce:
            if logger:
                logger.warning("Max nonce exceeded during mining.")
            raise MiningExceededError("Mining failed: max_nonce exceeded")

        # Hash header (unchanged logic)
        block_hash = calculate_hash(block.to_header_dict())

        # Optional progress reporting
        if progress_callback:
            progress_callback(block, block_hash)

        if block_hash.startswith(target):
            block.hash = block_hash
            if logger:
                logger.info(f"Success! Hash: {block_hash}")
            return block

        block.nonce += 1
