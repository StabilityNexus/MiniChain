import pytest
from unittest.mock import patch
from minichain.transaction import Transaction
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

def test_tx_caching_efficiency():
    """
    Verifies that the expensive hashing math is only performed once 
    and skipped on subsequent accesses (Memoization proof).
    """
    sk = SigningKey.generate()
    sender_hex = sk.verify_key.encode(encoder=HexEncoder).decode()
    tx = Transaction(sender=sender_hex, receiver="addr", amount=100, nonce=1)

    # We 'patch' the hashing function to count how many times it's called
    with patch('minichain.transaction.canonical_json_hash') as mock_hash:
        mock_hash.return_value = "mocked_hash_value"

        # 1. First Access: Should trigger the hash calculation
        res1 = tx.tx_id
        assert res1 == "mocked_hash_value"
        assert mock_hash.call_count == 1

        # 2. Second Access: Should return the cached value (count remains 1)
        res2 = tx.tx_id
        assert res2 == "mocked_hash_value"
        assert mock_hash.call_count == 1  # <--- THIS proves the cache worked!

        # 3. Comprehensive Invalidation: Changing ANY field must clear the cache
        mutations = {
            "sender": "new_sender_hex",
            "receiver": "new_receiver",
            "amount": 200,
            "nonce": 2,
            "data": "new_data",
            "timestamp": 1234567890,
            "signature": "fake_signature_hex"
        }

        expected_calls = 1
        for field, new_value in mutations.items():
            # Mutate the field dynamically
            setattr(tx, field, new_value)
            
            # Prove the cache was instantly killed
            assert tx._cached_tx_id is None, f"Cache failed to clear when mutating {field}"
            
            # Access ID again, which forces a re-calculation
            _ = tx.tx_id
            
            # Prove the hashing math ran exactly one more time
            expected_calls += 1
            assert mock_hash.call_count == expected_calls, f"Hash did not recalculate for {field}"

def test_signed_tx_is_sealed():
    """Verifies that a signed transaction clears cache, changes ID, and cannot be modified."""
    sk = SigningKey.generate()
    sender_hex = sk.verify_key.encode(encoder=HexEncoder).decode()
    tx = Transaction(sender=sender_hex, receiver="bob", amount=100, nonce=1)
    
    # 1. Grab the ID before signing
    unsigned_id = tx.tx_id
    assert tx._cached_tx_id == unsigned_id
    
    # 2. Sign it
    tx.sign(sk)
    
    # 3. Prove signing killed the old cache
    assert tx._cached_tx_id is None
    
    # 4. Prove the new ID is totally different
    signed_id = tx.tx_id
    assert signed_id != unsigned_id
    
    # 5. Prove it's locked down (Sealed)
    with pytest.raises(AttributeError, match="Transaction is sealed"):
        tx.amount = 500