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
    # Create a transaction
    tx = Transaction(sender="Alice_PK", receiver="Bob_PK", amount=50, nonce=1)
    
    first_id = tx.tx_id
    # Re-triggering the ID calculation
    second_id = tx.tx_id

    print(f"TX ID: {first_id}")
    assert first_id == second_id
    print("Success: Transaction ID is stable and deterministic!\n")

def test_block_serialization_determinism():
    print("--- Testing Block Serialization & Cross-Instance Determinism ---")
    tx_params = {"sender": "A", "receiver": "B", "amount": 10, "nonce": 5}
    
    # Create two SEPARATE block instances with the exact same data
    tx1 = Transaction(**tx_params)
    block1 = Block(index=1, previous_hash="0"*64, transactions=[tx1], difficulty=2)
    
    tx2 = Transaction(**tx_params)
    block2 = Block(index=1, previous_hash="0"*64, transactions=[tx2], difficulty=2)

    # 1. Test stable bytes on one instance (same object, twice)
    assert block1.canonical_payload == block1.canonical_payload
    
    # 2. Test cross-instance determinism (different objects, same data)
    assert block1.canonical_payload == block2.canonical_payload, "Identical blocks must have identical payloads"
    
    # 3. Test hash consistency
    assert block1.compute_hash() == block2.compute_hash()
    
    print("✅ Success: Block serialization is cross-instance deterministic!\n")

if __name__ == "__main__":
    # Removed try/except so that AssertionErrors 'bubble up' to the test runner
    test_raw_data_determinism()
    test_transaction_id_stability()
    test_block_serialization_determinism()
    print("🚀 ALL CANONICAL TESTS PASSED!")