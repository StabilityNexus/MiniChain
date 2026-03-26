from minichain.transaction import Transaction
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder

def test_tx_caching():
    # 1. Setup a dummy transaction
    sk = SigningKey.generate()
    sender_hex = sk.verify_key.encode(encoder=HexEncoder).decode()
    
    tx = Transaction(
        sender=sender_hex,
        receiver="receiver_addr",
        amount=100,
        nonce=1
    )

    # 2. Assert Initial State (The "None" check you were worried about)
    assert tx._cached_tx_id is None

    # 3. First access (triggers calculation)
    first_id = tx.tx_id
    assert tx._cached_tx_id == first_id
    assert tx._cached_tx_id is not None

    # 4. Second access (should be an instant lookup)
    second_id = tx.tx_id
    assert second_id == first_id

    # 5. Signing (must invalidate/clear the cache)
    tx.sign(sk)
    assert tx._cached_tx_id is None

    # 6. Access after signing (must re-calculate)
    signed_id = tx.tx_id
    assert tx._cached_tx_id == signed_id
    assert signed_id != first_id