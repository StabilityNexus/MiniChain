import unittest
from minichain import Blockchain, Block
from minichain.pow import mine_block
from minichain.validators import ValidationStatus
from minichain.network_config import MAX_TARGET

class TestEMATarget(unittest.TestCase):
    def test_target_adjustment(self):
        chain = Blockchain()
        chain.target_block_time = 1000
        chain.alpha = 0.5
        chain.avg_block_time = 1000
        
        # Start with a target comfortably in the middle
        start_target = MAX_TARGET // 2
        chain.current_target = start_target
        chain.chain[0].target = start_target
        chain.chain[0].hash = chain.chain[0].compute_hash()
        chain.genesis_target = start_target
        chain.max_target_for_floor = MAX_TARGET

        # Fast mining: timestamps only 1ms apart.
        # The raw EMA wants start_target // 2, but one block may change the
        # target by at most 25%.
        ts = chain.last_block.timestamp + 1
        block1 = Block(index=1, previous_hash=chain.last_block.hash, transactions=[], timestamp=ts, target=chain.current_target, state_root=chain.state.state_root())
        mined_block1 = mine_block(block1)
        self.assertEqual(chain.add_block(mined_block1), ValidationStatus.VALID)
        expected_target_fast = start_target - (start_target * 25 // 100)
        self.assertEqual(chain.current_target, expected_target_fast)

        # Slow mining: timestamp 5000ms apart. The raw EMA wants 2.75x the
        # target, so the +25% bound applies.
        ts = chain.last_block.timestamp + 5000
        block2 = Block(index=2, previous_hash=chain.last_block.hash, transactions=[], timestamp=ts, target=chain.current_target, state_root=chain.state.state_root())
        mined_block2 = mine_block(block2)
        self.assertEqual(chain.add_block(mined_block2), ValidationStatus.VALID)
        expected_target_slow = expected_target_fast + (expected_target_fast * 25 // 100)
        self.assertEqual(chain.current_target, expected_target_slow)

    def test_target_change_is_bounded_in_one_block(self):
        chain = Blockchain()
        start_target = chain.genesis_target
        max_delta = max(1, start_target * 25 // 100)

        fastest = chain._next_target(start_target, 1)
        slowest = chain._next_target(start_target, 10**12)

        self.assertGreaterEqual(fastest, start_target - max_delta)
        self.assertLessEqual(slowest, start_target + max_delta)

    def test_target_never_falls_below_genesis_difficulty_floor(self):
        chain = Blockchain()
        target = chain.genesis_target
        floor = min(MAX_TARGET, chain.genesis_target * 4)

        for _ in range(100):
            target = chain._next_target(target, 10**12)

        self.assertEqual(target, floor)

    def test_reorg_target_validation(self):
        chain1 = Blockchain()
        chain1.target_block_time = 1000
        chain1.alpha = 0.5
        chain1.avg_block_time = 1000
        start_target = MAX_TARGET // 2
        chain1.current_target = start_target
        chain1.chain[0].target = start_target
        chain1.chain[0].hash = chain1.chain[0].compute_hash()
        chain1.genesis_target = start_target
        chain1.max_target_for_floor = MAX_TARGET

        chain2 = Blockchain()
        chain2.target_block_time = 1000
        chain2.alpha = 0.5
        chain2.avg_block_time = 1000
        chain2.current_target = start_target
        chain2.chain[0].target = start_target
        chain2.chain[0].hash = chain2.chain[0].compute_hash()
        chain2.genesis_target = start_target
        chain2.max_target_for_floor = MAX_TARGET

        # Chain 2 mines a fast block
        block1 = Block(1, chain2.last_block.hash, chain2.current_target, [], timestamp=chain2.last_block.timestamp + 1, state_root=chain2.state.state_root())
        mine_block(block1)
        chain2.add_block(block1)

        expected_target_fast = start_target - (start_target * 25 // 100)
        self.assertEqual(chain2.current_target, expected_target_fast)
        
        # Reorg chain1 to chain2
        success, _ = chain1.resolve_conflicts(chain2.chain)
        self.assertTrue(success)
        self.assertEqual(chain1.current_target, expected_target_fast)

        # Forging a chain with wrong target should be rejected
        forged_chain = list(chain2.chain)
        # Should be expected_target_fast but we provide start_target instead!
        forged_block = Block(2, chain2.last_block.hash, start_target, [], timestamp=chain2.last_block.timestamp + 1000, state_root=chain2.state.state_root())
        mine_block(forged_block)
        forged_chain.append(forged_block)
        
        success, _ = chain1.resolve_conflicts(forged_chain)
        self.assertFalse(success) # Rejected because target is wrong!
