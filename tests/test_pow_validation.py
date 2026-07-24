from minichain import Blockchain, Block
from minichain.validators import ValidationStatus


def _make_block_with_invalid_pow(chain):
    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=[],
        difficulty=chain.current_difficulty,
        state_root=chain.state.state_root(),
    )

    while True:
        block.hash = block.compute_hash()
        if not block.hash.startswith("0" * block.difficulty):
            return block
        block.nonce += 1


def test_add_block_rejects_invalid_pow():
    chain = Blockchain()
    block = _make_block_with_invalid_pow(chain)

    assert chain.add_block(block) == ValidationStatus.INVALID
    assert len(chain.chain) == 1


def test_resolve_conflicts_rejects_invalid_pow():
    chain = Blockchain()
    block = _make_block_with_invalid_pow(chain)

    success, _ = chain.resolve_conflicts([chain.chain[0], block])

    assert success is False
    assert len(chain.chain) == 1


def _make_block_with_lied_difficulty(chain, lied_difficulty):
    block = Block(
        index=chain.last_block.index + 1,
        previous_hash=chain.last_block.hash,
        transactions=[],
        difficulty=lied_difficulty,
        state_root=chain.state.state_root(),
    )
    target = "0" * lied_difficulty
    while True:
        block.hash = block.compute_hash()
        if block.hash.startswith(target):
            return block
        block.nonce += 1


def test_add_block_rejects_lied_difficulty():
    chain = Blockchain()
    assert chain.current_difficulty > 1  # sanity: test only meaningful if real difficulty is harder
    block = _make_block_with_lied_difficulty(chain, lied_difficulty=1)

    assert chain.add_block(block) != ValidationStatus.VALID
    assert len(chain.chain) == 1


def test_resolve_conflicts_rejects_lied_difficulty():
    chain = Blockchain()
    assert chain.current_difficulty > 1
    block = _make_block_with_lied_difficulty(chain, lied_difficulty=1)

    success, _ = chain.resolve_conflicts([chain.chain[0], block])

    assert success is False
    assert len(chain.chain) == 1