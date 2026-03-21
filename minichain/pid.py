"""
PID-based Difficulty Adjuster for MiniChain

Uses fixed-point integer arithmetic for deterministic behavior across all nodes.

Key Fix: Uses integer division (difficulty // 10) instead of float (difficulty * 0.1)
This prevents chain forks from CPU rounding differences.
"""

import time
from typing import Optional


class PIDDifficultyAdjuster:
    """
    Adjusts blockchain difficulty using a PID controller to maintain target block time.
    
    Uses fixed-point integer scaling (SCALE=1000) for deterministic behavior.
    Ensures all nodes compute identical results regardless of CPU/platform.
    """
    
    SCALE = 1000  # Fixed-point scaling factor
    
    def __init__(
        self,
        target_block_time: float = 5.0,
        kp: int = 500,
        ki: int = 50,
        kd: int = 100
    ):
        """
        Initialize the PID difficulty adjuster.
        
        Args:
            target_block_time: Target time for block generation in seconds
            kp: Proportional coefficient (pre-scaled by SCALE). Default 500 = 0.5
            ki: Integral coefficient (pre-scaled by SCALE). Default 50 = 0.05
            kd: Derivative coefficient (pre-scaled by SCALE). Default 100 = 0.1
        """
        self.target_block_time = target_block_time
        self.kp = kp      # Proportional
        self.ki = ki      # Integral
        self.kd = kd      # Derivative
        self.integral = 0
        self.previous_error = 0
        self.last_block_time = time.monotonic()
        self.integral_limit = 100 * self.SCALE
    
    def adjust(
        self,
        current_difficulty: Optional[int] = None,
        actual_block_time: Optional[float] = None
    ) -> int:
        """
        Calculate new difficulty based on actual block time.
        
        Args:
            current_difficulty: Current difficulty (default: 1000)
            actual_block_time: Time to mine block in seconds
                             If None, calculated from time since last call
        
        Returns:
            New difficulty value (minimum 1)
        
        Example:
            adjuster = PIDDifficultyAdjuster(target_block_time=10)
            new_difficulty = adjuster.adjust(current_difficulty=10000, actual_block_time=12.5)
        """
        
        # Handle None difficulty
        if current_difficulty is None:
            current_difficulty = 1000
        
        # Calculate actual_block_time if not provided
        if actual_block_time is None:
            now = time.monotonic()
            actual_block_time = now - self.last_block_time
            self.last_block_time = now
        
        # ===== Fixed-Point Integer Arithmetic =====
        # Convert times to scaled integers for precise calculation
        actual_block_time_scaled = int(actual_block_time * self.SCALE)
        target_time_scaled = int(self.target_block_time * self.SCALE)
        
        # Calculate error: positive = too fast, negative = too slow
        error = target_time_scaled - actual_block_time_scaled
        
        # ===== Proportional Term =====
        p_term = self.kp * error
        
        # ===== Integral Term with Anti-Windup =====
        self.integral += error
        self.integral = max(
            min(self.integral, self.integral_limit),
            -self.integral_limit
        )
        i_term = self.ki * self.integral
        
        # ===== Derivative Term =====
        derivative = error - self.previous_error
        self.previous_error = error
        d_term = self.kd * derivative
        
        # ===== PID Calculation =====
        # Combine all terms and scale back to normal units
        adjustment = (p_term + i_term + d_term) // self.SCALE
        
        # ===== Safety Constraint: Limit Change to 10% =====
        # ✅ FIXED: Use integer division instead of float multiplier
        # Was: max_delta = max(1, int(current_difficulty * 0.1))
        # Now: max_delta = max(1, current_difficulty // 10)
        max_delta = max(1, current_difficulty // 10)
        
        # Clamp adjustment to safety bounds
        clamped_adjustment = max(
            min(adjustment, max_delta),
            -max_delta
        )
        
        # Ensure we move at least ±1 if adjustment is non-zero
        delta = int(clamped_adjustment)
        if delta == 0 and clamped_adjustment != 0:
            delta = 1 if clamped_adjustment > 0 else -1
        
        # Calculate and return new difficulty
        new_difficulty = current_difficulty + delta
        return max(1, new_difficulty)
    
    def reset(self) -> None:
        """Reset PID state (integral and derivative history)."""
        self.integral = 0
        self.previous_error = 0
        self.last_block_time = time.monotonic()
    
    def get_state(self) -> dict:
        """
        Get current PID state for debugging or persistence.
        
        Returns:
            Dictionary containing integral, previous_error, and last update time
        """
        return {
            "integral": self.integral,
            "previous_error": self.previous_error,
            "last_block_time": self.last_block_time
        }
    
    def set_state(self, state: dict) -> None:
        """
        Restore PID state from a dictionary (for recovery/persistence).
        
        Args:
            state: Dictionary with keys 'integral', 'previous_error', 'last_block_time'
        """
        self.integral = state.get("integral", 0)
        self.previous_error = state.get("previous_error", 0)
        self.last_block_time = state.get("last_block_time", time.monotonic())