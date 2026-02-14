import json
from nacl.hash import sha256
from nacl.encoding import HexEncoder

def calculate_hash(block_dict):
    """Calculates SHA256 hash of a block header."""
    block_string = json.dumps(block_dict, sort_keys=True).encode('utf-8')
    return sha256(block_string, encoder=HexEncoder).decode('utf-8')

def mine_block(block, difficulty=4):
    """
    Increments nonce until the hash starts with 'difficulty' number of zeros.
    Returns the valid block (with nonce and hash set).
    """
    target = '0' * difficulty
    block.nonce = 0
    
    print(f"    Mining block {block.index} (Difficulty: {difficulty})...")
    while True:
        # We hash the header (which includes the tx list signature/hash)
        block_hash = calculate_hash(block.to_header_dict())
        
        if block_hash.startswith(target):
            block.hash = block_hash
            print(f"    Success! Hash: {block_hash}")
            return block
            
        block.nonce += 1