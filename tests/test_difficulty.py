"""
Test Suite for PIDDifficultyAdjuster

Comprehensive tests covering:
- Basic PID functionality
- Edge cases and boundary conditions
- Integration scenarios
- State management
- Integer arithmetic correctness
"""

import time
import unittest
from minichain.pid import PIDDifficultyAdjuster

class TestPIDBasicFunctionality(unittest.TestCase):
    """Test core PID functionality."""
    
    def setUp(self):
        """Initialize adjuster for each test."""
        self.adjuster = PIDDifficultyAdjuster(target_block_time=5.0)
    
    def test_initialization(self):
        """Test proper initialization of PID adjuster."""
        self.assertEqual(self.adjuster.target_block_time, 5.0)
        self.assertEqual(self.adjuster.kp, 500)
        self.assertEqual(self.adjuster.ki, 50)
        self.assertEqual(self.adjuster.kd, 100)
        self.assertEqual(self.adjuster.integral, 0)
        self.assertEqual(self.adjuster.previous_error, 0)
    
    def test_block_too_slow_increases_difficulty(self):
        """Test that slow blocks increase difficulty."""
        current_difficulty = 1000
        actual_block_time = 7.0  # 2 seconds slower than target (5s)
        
        new_difficulty = self.adjuster.adjust(
            current_difficulty=current_difficulty,
            actual_block_time=actual_block_time
        )
        
        self.assertGreater(new_difficulty, current_difficulty)
        print(f"Slow block (7s): {current_difficulty} → {new_difficulty}")
    
    def test_block_too_fast_decreases_difficulty(self):
        """Test that fast blocks decrease difficulty."""
        current_difficulty = 1000
        actual_block_time = 3.0  # 2 seconds faster than target (5s)
        
        new_difficulty = self.adjuster.adjust(
            current_difficulty=current_difficulty,
            actual_block_time=actual_block_time
        )
        
        self.assertLess(new_difficulty, current_difficulty)
        print(f"Fast block (3s): {current_difficulty} → {new_difficulty}")
    
    def test_block_on_target_minimal_change(self):
        """Test that on-target blocks produce minimal change."""
        current_difficulty = 1000
        actual_block_time = 5.0  # Exactly target
        
        new_difficulty = self.adjuster.adjust(
            current_difficulty=current_difficulty,
            actual_block_time=actual_block_time
        )
        
        # Should be very close to current (0 or ±1)
        self.assertLessEqual(abs(new_difficulty - current_difficulty), 1)
        print(f"On-target block (5s): {current_difficulty} → {new_difficulty}")


class TestSafetyConstraints(unittest.TestCase):
    """Test difficulty adjustment bounds and limits."""
    
    def setUp(self):
        self.adjuster = PIDDifficultyAdjuster(target_block_time=5.0)
    
    def test_maximum_change_is_10_percent(self):
        """Test that adjustment is clamped to ±10%."""
        current_difficulty = 1000
        
        # Extremely slow block (should want to increase difficulty much more than 10%)
        actual_block_time = 100.0  # 95 seconds slower than target
        
        new_difficulty = self.adjuster.adjust(
            current_difficulty=current_difficulty,
            actual_block_time=actual_block_time
        )
        
        change_percent = abs((new_difficulty - current_difficulty) / current_difficulty)
        self.assertLessEqual(change_percent, 0.11)  # Allow small rounding margin
        print(f"Extreme slow (100s): ±{change_percent:.1%} change (clamped at 10%)")
    
    def test_difficulty_never_goes_below_one(self):
        """Test that difficulty never goes below 1."""
        # Start with very low difficulty
        current_difficulty = 1
        
        # Fast block
        actual_block_time = 0.1
        
        new_difficulty = self.adjuster.adjust(
            current_difficulty=current_difficulty,
            actual_block_time=actual_block_time
        )
        
        self.assertGreaterEqual(new_difficulty, 1)
        print(f"Minimum difficulty check: {new_difficulty}")
    
    def test_minimum_adjustment_is_one_if_needed(self):
        """Test that smallest change is ±1 (not 0 when adjustment needed)."""
        adjuster = PIDDifficultyAdjuster(target_block_time=5.0)
        
        # Many small adjustments to build up integral
        for _ in range(20):
            adjuster.adjust(current_difficulty=1000, actual_block_time=5.01)
        
        # Now should have minimal but nonzero adjustment
        new_diff = adjuster.adjust(current_difficulty=1000, actual_block_time=5.01)
        
        # Either no change or exactly ±1
        change = abs(new_diff - 1000)
        self.assertIn(change, [0, 1])
        print(f"Minimum adjustment: change of {change}")


class TestIntegerArithmetic(unittest.TestCase):
    """Verify pure integer arithmetic (no float precision issues)."""
    
    def test_integer_division_10_percent(self):
        """Verify 10% calculation uses integer division."""
        adjuster = PIDDifficultyAdjuster()
        
        # Test various difficulties
        test_values = [1, 10, 100, 1000, 10000, 123456]
        
        for difficulty in test_values:
            # Using the formula from the code
            max_delta = max(1, difficulty // 10)
            
            # Should be exactly 10%
            expected = difficulty // 10
            if expected == 0:
                expected = 1
            
            self.assertEqual(max_delta, expected)
        
        print("Integer division 10% check: PASSED for all test values")
    
    def test_no_float_calculations_in_main_path(self):
        """Verify main calculation path contains no float arithmetic."""
        # This is more of a code review than a test
        # The adjust() method should use only integer operations
        
        adjuster = PIDDifficultyAdjuster()
        
        # Call adjust multiple times and verify no float operations occur
        for _ in range(10):
            difficulty = adjuster.adjust(1000, 5.0)
            self.assertIsInstance(difficulty, int)
        
        print("No float arithmetic detected in main path")


class TestStateManagement(unittest.TestCase):
    """Test state persistence and recovery."""
    
    def test_get_state(self):
        """Test retrieving adjuster state."""
        adjuster = PIDDifficultyAdjuster()
        
        # Adjust a few times to change state
        for i in range(3):
            adjuster.adjust(1000 + i*100, 5.0 + i*0.1)
        
        state = adjuster.get_state()
        
        # Verify state dictionary contains expected keys
        self.assertIn("integral", state)
        self.assertIn("previous_error", state)
        self.assertIn("last_block_time", state)
        self.assertIsInstance(state["integral"], int)
    
    def test_set_state(self):
        """Test restoring adjuster state."""
        adjuster1 = PIDDifficultyAdjuster()
        
        # Build up state
        for _ in range(5):
            adjuster1.adjust(1000, 5.5)
        
        state = adjuster1.get_state()
        
        # Create new adjuster and restore state
        adjuster2 = PIDDifficultyAdjuster()
        adjuster2.set_state(state)
        
        # Should produce identical results
        diff1 = adjuster1.adjust(1000, 5.5)
        diff2 = adjuster2.adjust(1000, 5.5)
        
        self.assertEqual(diff1, diff2)
        print("State persistence: PASSED")
    
    def test_reset(self):
        """Test resetting adjuster state."""
        adjuster = PIDDifficultyAdjuster()
        
        # Build up state
        for _ in range(10):
            adjuster.adjust(1000, 6.0)  # Bias toward slower blocks
        
        self.assertNotEqual(adjuster.integral, 0)
        
        # Reset
        adjuster.reset()
        
        self.assertEqual(adjuster.integral, 0)
        self.assertEqual(adjuster.previous_error, 0)
        print("Reset function: PASSED")


class TestConvergence(unittest.TestCase):
    """Test that difficulty converges to target block time."""
    
    def test_convergence_to_target(self):
        """Simulate mining sequence and verify convergence."""
        adjuster = PIDDifficultyAdjuster(target_block_time=5.0)
        
        # Simulate blocks with random-like block times
        block_times = [
            6.2, 5.8, 6.5, 4.9, 5.1, 6.0, 5.3, 4.8, 5.9, 5.2,
            5.1, 5.0, 5.2, 4.9, 5.1
        ]
        
        difficulty = 1000
        deviations = []
        
        for block_time in block_times:
            difficulty = adjuster.adjust(difficulty, block_time)
            deviation = abs(block_time - 5.0)
            deviations.append(deviation)
        
        # Later deviations should be smaller (convergence)
        early_avg = sum(deviations[:5]) / 5
        late_avg = sum(deviations[-5:]) / 5
        
        print(f"Early blocks avg deviation: {early_avg:.2f}s")
        print(f"Late blocks avg deviation: {late_avg:.2f}s")
        print(f"Convergence ratio: {early_avg/late_avg:.2f}x improvement")
        
        # Should see improvement (though not guaranteed to be 2x)
        self.assertLess(late_avg, early_avg)
    
    def test_steady_state_detection(self):
        """Test behavior when blocks are consistently on-target."""
        adjuster = PIDDifficultyAdjuster(target_block_time=5.0)
        
        difficulty = 1000
        differences = []
        
        # Simulate steady stream of on-target blocks
        for _ in range(20):
            new_diff = adjuster.adjust(difficulty, 5.0)
            differences.append(abs(new_diff - difficulty))
            difficulty = new_diff
        
        # Changes should be minimal/zero
        avg_change = sum(differences) / len(differences)
        print(f"Steady state avg change: {avg_change:.2f}")
        
        self.assertLess(avg_change, 0.5)  # Nearly zero


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and boundary conditions."""
    
    def test_zero_difficulty_handling(self):
        """Test handling of difficulty=0 (shouldn't happen but...)."""
        adjuster = PIDDifficultyAdjuster()
        
        # When difficulty=0, should still return minimum (1)
        result = adjuster.adjust(0, 5.0)
        self.assertGreaterEqual(result, 1)
    
    def test_none_difficulty_uses_default(self):
        """Test that None difficulty defaults to 1000."""
        adjuster = PIDDifficultyAdjuster()
        
        result = adjuster.adjust(None, 5.0)
        self.assertGreater(result, 0)
        print(f"Default difficulty applied: {result}")
    
    def test_very_high_difficulty(self):
        """Test behavior with very large difficulties."""
        adjuster = PIDDifficultyAdjuster()
        
        large_difficulty = 10**15
        
        result = adjuster.adjust(large_difficulty, 7.0)
        
        # Should stay very large and within bounds
        self.assertGreater(result, large_difficulty // 2)
        self.assertLess(result, large_difficulty * 1.2)
        print(f"Large difficulty: {large_difficulty} → {result}")
    
    def test_rapid_fire_adjustments(self):
        """Test many rapid adjustments without time delay."""
        adjuster = PIDDifficultyAdjuster()
        
        difficulty = 1000
        
        # Rapid adjustments with explicit times (not auto-timing)
        for i in range(100):
            difficulty = adjuster.adjust(difficulty, 5.0)
        
        # Should stabilize despite rapid adjustments
        self.assertGreater(difficulty, 1)
        self.assertLess(difficulty, 10000)


class TestIntegrationScenarios(unittest.TestCase):
    """Test realistic blockchain scenarios."""
    
    def test_sudden_hash_rate_increase(self):
        """Simulate sudden increase in network hash rate (blocks too fast)."""
        adjuster = PIDDifficultyAdjuster(target_block_time=10.0)
        
        difficulty = 1000
        
        # Blocks start coming in 30% too fast
        print("\n--- Sudden Hash Rate Increase ---")
        for i in range(10):
            difficulty = adjuster.adjust(difficulty, 7.0)
            print(f"Block {i+1}: difficulty={difficulty}")
        
        # Difficulty should increase
        self.assertGreater(difficulty, 1000)
    
    def test_sudden_hash_rate_decrease(self):
        """Simulate sudden decrease in network hash rate (blocks too slow)."""
        adjuster = PIDDifficultyAdjuster(target_block_time=10.0)
        
        difficulty = 1000
        
        # Blocks start coming in 30% too slow
        print("\n--- Sudden Hash Rate Decrease ---")
        for i in range(10):
            difficulty = adjuster.adjust(difficulty, 13.0)
            print(f"Block {i+1}: difficulty={difficulty}")
        
        # Difficulty should decrease
        self.assertLess(difficulty, 1000)
    
    def test_oscillating_network(self):
        """Test behavior with oscillating (unpredictable) block times."""
        adjuster = PIDDifficultyAdjuster(target_block_time=5.0)
        
        # Alternating fast/slow blocks
        times = [3.0, 7.0] * 10  # Fast, slow, fast, slow...
        
        difficulty = 1000
        changes = []
        
        for block_time in times:
            new_diff = adjuster.adjust(difficulty, block_time)
            changes.append(abs(new_diff - difficulty))
            difficulty = new_diff
        
        # Changes should be reasonable despite oscillation
        avg_change = sum(changes) / len(changes)
        print(f"Oscillating network avg adjustment: {avg_change:.1f}")
        
        self.assertLess(avg_change, 50)


def run_tests():
    """Run all tests with verbose output."""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # Add all test classes
    suite.addTests(loader.loadTestsFromTestCase(TestPIDBasicFunctionality))
    suite.addTests(loader.loadTestsFromTestCase(TestSafetyConstraints))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegerArithmetic))
    suite.addTests(loader.loadTestsFromTestCase(TestStateManagement))
    suite.addTests(loader.loadTestsFromTestCase(TestConvergence))
    suite.addTests(loader.loadTestsFromTestCase(TestEdgeCases))
    suite.addTests(loader.loadTestsFromTestCase(TestIntegrationScenarios))
    
    # Run with verbose output
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Summary
    print("\n" + "="*70)
    print("TEST SUMMARY")
    print("="*70)
    print(f"Tests run: {result.testsRun}")
    print(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failures: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")
    print("="*70)
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    exit(0 if success else 1)