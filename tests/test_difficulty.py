import unittest
from minichain import Blockchain, Block
from minichain.pow import mine_block
from minichain.validators import ValidationStatus

class TestEMADifficulty(unittest.TestCase):
    def test_difficulty_adjustment(self):
        chain = Blockchain()
        chain.target_block_time = 1000
        chain.alpha = 0.5
        chain.avg_block_time = 1000
        
        start_target = (1 << 256) - 1
        chain.current_difficulty = start_target
        chain.chain[0].difficulty = start_target
        
        # Fast mining: timestamps only 1ms apart
        # avg = 0.5 * 1 + 0.5 * 1000 = 500.5 (which is < 1000)
        # ratio = 500.5 / 1000 = 0.5005
        # new_target = int(start_target * 0.5005)
        ts = chain.last_block.timestamp + 1
        block1 = Block(index=1, previous_hash=chain.last_block.hash, transactions=[], timestamp=ts, difficulty=chain.current_difficulty, state_root=chain.state.state_root())
        mined_block1 = mine_block(block1)
        self.assertEqual(chain.add_block(mined_block1), ValidationStatus.VALID)
        expected_target_1 = int(start_target * 0.5005)
        self.assertEqual(chain.current_difficulty, expected_target_1)
        
        # Slow mining: timestamp 5000ms apart
        # avg = 0.5 * 5000 + 0.5 * 500.5 = 2750.25
        # ratio = 2750.25 / 1000 = 2.75025
        # new_target = int(expected_target_1 * 2.75025)
        ts = chain.last_block.timestamp + 5000
        block2 = Block(index=2, previous_hash=chain.last_block.hash, transactions=[], timestamp=ts, difficulty=chain.current_difficulty, state_root=chain.state.state_root())
        mined_block2 = mine_block(block2)
        self.assertEqual(chain.add_block(mined_block2), ValidationStatus.VALID)
        expected_target_2 = min((1 << 256) - 1, int(expected_target_1 * 2.75025))
        self.assertEqual(chain.current_difficulty, expected_target_2)

    def test_reorg_difficulty_validation(self):
        chain1 = Blockchain()
        chain1.target_block_time = 1000
        chain1.alpha = 0.5
        chain1.avg_block_time = 1000
        
        start_target = (1 << 256) - 1
        chain1.current_difficulty = start_target
        chain1.chain[0].difficulty = start_target
        
        chain2 = Blockchain()
        chain2.target_block_time = 1000
        chain2.alpha = 0.5
        chain2.avg_block_time = 1000
        chain2.current_difficulty = start_target
        chain2.chain[0].difficulty = start_target

        # Chain 2 mines a fast block
        block1 = Block(1, chain2.last_block.hash, [], timestamp=chain2.last_block.timestamp + 1, difficulty=chain2.current_difficulty, state_root=chain2.state.state_root())
        mine_block(block1)
        chain2.add_block(block1)
        
        expected_target_1 = int(start_target * 0.5005)
        self.assertEqual(chain2.current_difficulty, expected_target_1)
        
        # Reorg chain1 to chain2
        success, orphans = chain1.resolve_conflicts(chain2.chain)
        self.assertTrue(success)
        self.assertEqual(chain1.current_difficulty, expected_target_1)

        # Forging a chain with wrong difficulty should be rejected
        forged_chain = list(chain2.chain)
        forged_block = Block(2, chain2.last_block.hash, [], timestamp=chain2.last_block.timestamp + 1000, difficulty=start_target, state_root=chain2.state.state_root())
        mine_block(forged_block)
        forged_chain.append(forged_block)
        
        success, _ = chain1.resolve_conflicts(forged_chain)
        self.assertFalse(success)
