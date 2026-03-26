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
        # Give it a dummy return value so the code doesn't crash
        mock_hash.return_value = "mocked_hash_value"

        # 1. First Access: Should trigger the hash calculation
        res1 = tx.tx_id
        assert res1 == "mocked_hash_value"
        assert mock_hash.call_count == 1

        # 2. Second Access: Should return the cached value (count remains 1)
        res2 = tx.tx_id
        assert res2 == "mocked_hash_value"
        assert mock_hash.call_count == 1  # <--- THIS proves the cache worked!

        # 3. Invalidate via Mutation: Changing a field clears the cache
        tx.amount = 200
        assert tx._cached_tx_id is None

        # 4. Third Access: Should trigger calculation again (count becomes 2)
        _ = tx.tx_id
        assert mock_hash.call_count == 2

def test_signed_tx_is_sealed():
    """Verifies that a signed transaction cannot be modified (Sealing check)."""
    sk = SigningKey.generate()
    sender_hex = sk.verify_key.encode(encoder=HexEncoder).decode()
    tx = Transaction(sender=sender_hex, receiver="bob", amount=100, nonce=1)
    
    tx.sign(sk)
    
    with pytest.raises(AttributeError, match="Transaction is sealed"):
        tx.amount = 500