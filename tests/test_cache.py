from minichain.transaction import Transaction
from nacl.signing import SigningKey
from nacl.encoding import HexEncoder


def test_tx_caching():
    sk = SigningKey.generate()
    sender_hex = sk.verify_key.encode(encoder=HexEncoder).decode()
    tx = Transaction(sender=sender_hex, receiver="addr", amount=100, nonce=1)

    assert tx._cached_tx_id is None
    first_id = tx.tx_id
    assert tx._cached_tx_id == first_id
    assert tx.tx_id == first_id  # second access, same result

    tx.sign(sk)
    assert tx._cached_tx_id is None

    signed_id = tx.tx_id
    assert signed_id != first_id
    assert tx._cached_tx_id == signed_id


def test_tx_mutation_clears_cache():
    tx = Transaction(sender="alice", receiver="bob", amount=100, nonce=1)
    original_id = tx.tx_id
    assert tx._cached_tx_id is not None

    tx.amount = 500
    assert tx._cached_tx_id is None
    assert tx.tx_id != original_id