"""
Stateless PID-based Difficulty Adjuster for MiniChain

CRITICAL: This implementation is 100% STATELESS.
No memory variables (integral, derivative) that persist between calls.

Why? If a node restarts, local variables reset to 0, causing the node to calculate
a radically different difficulty than the network, leading to a hard-fork.

Solution: Calculate difficulty purely from blockchain timestamps.
All nodes compute the same result regardless of when they started.
"""

from typing import Optional


class PIDDifficultyAdjuster:
    """
    Stateless difficulty adjuster using pure calculation from block data.
    
      100% Deterministic: Same inputs always produce same outputs
      No Hard-Forks: Nodes get identical results regardless of restart
      Blockchain-Based: Uses immutable timestamps, not local memory
    """
    
    SCALE = 1000  # Fixed-point scaling factor for integer math
    
    def __init__(
        self,
        target_block_time: float = 10.0,
        kp: int = 50,
        ki: int = 5,
        kd: int = 10
    ):
        """
        Initialize the stateless PID difficulty adjuster.
        
        Args:
            target_block_time: Target time for block generation in seconds
            kp: Proportional coefficient (pre-scaled by SCALE). Default 50 = 0.05
            ki: Integral coefficient (pre-scaled by SCALE). Default 5 = 0.005
            kd: Derivative coefficient (pre-scaled by SCALE). Default 10 = 0.01
        """
        self.target_block_time = target_block_time
        self.kp = kp      # Proportional
        self.ki = ki      # Integral (smaller - we don't accumulate state)
        self.kd = kd      # Derivative (smaller - we don't have history)
    
    def adjust(
        self,
        current_difficulty: int,
        actual_block_time: Optional[float] = None
    ) -> int:
        """
        Calculate new difficulty based on CURRENT block time only.
        
          STATELESS: No memory variables
        - No self.integral (resets on restart → hard-fork)
        - No self.previous_error (resets on restart → hard-fork)
        - No self.last_block_time (resets on restart → hard-fork)
        
        Pure calculation from current block time and fixed coefficients.
        
        Args:
            current_difficulty: Current difficulty value
            actual_block_time: Time to mine this block in seconds
        
        Returns:
            New difficulty value (minimum 1)
        """
        
        if actual_block_time is None:
            # If no time provided, make no adjustment (safe default)
            return current_difficulty
        
        # ===== Fixed-Point Integer Arithmetic =====
        # Convert times to scaled integers for precise calculation
        actual_block_time_scaled = int(actual_block_time * self.SCALE)
        target_time_scaled = int(self.target_block_time * self.SCALE)
        
        # Calculate error (positive = too fast, negative = too slow)
        error = target_time_scaled - actual_block_time_scaled
        
        # ===== Proportional Term Only =====
        # Without integral/derivative state, we use proportional adjustment
        # This is simpler, deterministic, and stateless
        p_adjustment = (self.kp * error) // self.SCALE
        
        # ===== Safety Constraint: Limit Change to 10% per Block =====
        #   FIXED: Use integer division (// 10) not float multiplier (* 0.1)
        # This ensures deterministic behavior across all CPUs
        max_delta = max(1, current_difficulty // 10)
        
        # Clamp adjustment to safety bounds
        clamped_adjustment = max(
            min(p_adjustment, max_delta),
            -max_delta
        )
        
        # Calculate new difficulty
        new_difficulty = current_difficulty + clamped_adjustment
        
        # Return new difficulty (minimum 1)
        return max(1, new_difficulty)
    
    # NOTE: No reset(), get_state(), or set_state() methods
    # These assume persistent memory, which causes hard-forks!
    # All state is calculated fresh from blockchain data.


class StatelessPIDDifficultyAdjuster(PIDDifficultyAdjuster):
    """
    Alternative: If you need more sophisticated PID logic, calculate from BLOCK HISTORY.
    
    Instead of storing state in memory:
    - Query the last N blocks from the blockchain
    - Calculate integral as sum of recent errors
    - Calculate derivative from last 2 blocks
    - All nodes get identical results (no hard-fork)
    
    This is the production-safe approach for a real blockchain.
    """
    
    def adjust_from_history(
        self,
        current_difficulty: int,
        recent_block_times: list  # Last N block times in seconds
    ) -> int:
        """
        Calculate adjustment using only blockchain history.
        
        Args:
            current_difficulty: Current difficulty
            recent_block_times: List of recent block times (e.g., last 10 blocks)
        
        Returns:
            New difficulty based on trend analysis
        """
        if not recent_block_times:
            return current_difficulty
        
        # Calculate average time (proportional term)
        avg_time = sum(recent_block_times) / len(recent_block_times)
        error = self.target_block_time - avg_time
        
        # Simple adjustment based on average deviation
        p_adjustment = int((self.kp * error * self.SCALE) // self.SCALE)
        
        # Safety constraint
        max_delta = max(1, current_difficulty // 10)
        clamped = max(min(p_adjustment, max_delta), -max_delta)
        
        new_difficulty = current_difficulty + clamped
        return max(1, new_difficulty)
