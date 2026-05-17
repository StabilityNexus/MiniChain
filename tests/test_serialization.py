from minichain.serialization import canonical_json_hash
from minichain.transaction import Transaction
from minichain.block import Block

def test_raw_data_determinism():
    print("--- Testing Raw Data Determinism ---")
    # Same data, different key order
    data_v1 = {"amount": 100, "nonce": 1, "receiver": "Alice", "sender": "Bob"}
    data_v2 = {"sender": "Bob", "receiver": "Alice", "nonce": 1, "amount": 100}

    hash_1 = canonical_json_hash(data_v1)
    hash_2 = canonical_json_hash(data_v2)

    print(f"Hash 1: {hash_1}")
    print(f"Hash 2: {hash_2}")
    assert hash_1 == hash_2
    print("Success: Raw hashes match regardless of key order!\n")

def test_transaction_id_stability():
    print("--- Testing Transaction ID Stability ---")
    # FIX: Add a fixed timestamp so tx1 and tx2 are identical
    tx_params = {"sender": "Alice", "receiver": "Bob", "amount": 50, "nonce": 1, "timestamp": 123456789}
    
    tx1 = Transaction(**tx_params)
    tx2 = Transaction(**tx_params)

    print(f"TX ID: {tx1.tx_id}")
    assert tx1.tx_id == tx2.tx_id, "Cross-instance TX IDs must match with same timestamp"
    print("✅ Success: Transaction ID is stable!\n")

def test_block_serialization_determinism():
    print("--- Testing Block Serialization & Cross-Instance Determinism ---")
    # FIX: Use fixed timestamps for both transaction and block
    tx_params = {"sender": "A", "receiver": "B", "amount": 10, "nonce": 5, "timestamp": 1000}
    
    # Create two separate but identical transaction instances
    tx1 = Transaction(**tx_params)
    tx2 = Transaction(**tx_params)
    
   # Add the miner field
    block1 = Block(index=1, previous_hash="0"*64, transactions=[tx1], difficulty=2, timestamp=999999, miner="a" * 40)
    block2 = Block(index=1, previous_hash="0"*64, transactions=[tx2], difficulty=2, timestamp=999999, miner="a" * 40)

    # Pre-compute the hashes before asserting
    block1.hash = block1.compute_hash()
    block2.hash = block2.compute_hash()

    assert block1.canonical_payload == block2.canonical_payload, "Identical blocks must have identical payloads"
    assert block1.compute_hash() == block2.compute_hash(), "Identical blocks must have identical hashes"
    
    print("✅ Success: Block serialization is cross-instance deterministic!\n")

def test_block_from_dict_rejects_tampered_payload():
    print("--- Testing Tamper Rejection ---")
    tx = Transaction(sender="A", receiver="B", amount=10, nonce=5, timestamp=1000)
    block = Block(
        index=1, previous_hash="0"*64, transactions=[tx], 
        difficulty=2, timestamp=999999, miner="a"*40
    )
    block.hash = block.compute_hash()

    # Test tampered Merkle Root (only one instance needed)
    bad_merkle = block.to_dict()
    bad_merkle["merkle_root"] = "f" * 64
    try:
        Block.from_dict(bad_merkle)
        raise AssertionError("Expected ValueError for tampered merkle_root") # Robust error
    except ValueError:
        pass

    # Test tampered Hash
    bad_hash = block.to_dict()
    bad_hash["hash"] = "0" * 64
    try:
        Block.from_dict(bad_hash)
        raise AssertionError("Expected ValueError for tampered hash")
    except ValueError:
        pass
    
    print("✅ Success: Tampered payloads are rejected!\n")

if __name__ == "__main__":
    # Removed try/except so that AssertionErrors 'bubble up' to the test runner
    test_raw_data_determinism()
    test_transaction_id_stability()
    test_block_serialization_determinism()
    test_block_from_dict_rejects_tampered_payload()  # <--- ADDED THIS LINE
    print("🚀 ALL CANONICAL TESTS PASSED!")