import time

class PIDDifficultyAdjuster:
    def __init__(self, target_block_time=5, kp=0.5, ki=0.05, kd=0.1):
        self.target_block_time = target_block_time
        # PID Coefficients
        self.kp = kp
        self.ki = ki
        self.kd = kd

        self.integral = 0
        self.previous_error = 0
        self.last_block_time = time.monotonic()
        
        # Limit the integral to prevent "Windup"
        # This stops the difficulty from tanking if the network goes offline
        self.integral_limit = 100 
        
        # Max percentage the difficulty can change in one block (e.g., 10%)
        self.max_change_factor = 0.1

    def adjust(self, current_difficulty, actual_block_time=None):
        """
        Calculates the new difficulty based on the time since the last block.
        """
        # --- FIX: Handle the case where current_difficulty is None ---
        if current_difficulty is None:
            current_difficulty = 1000  # Default starting difficulty
            
        if actual_block_time is None:
            now = time.monotonic()
            actual_block_time = now - self.last_block_time
            self.last_block_time = now

        # Error = Goal - Reality
        error = self.target_block_time - actual_block_time
        
        # Update Integral with clamping (Anti-Windup)
        self.integral = max(min(self.integral + error, self.integral_limit), -self.integral_limit)
        
        # Derivative: how fast is the error changing?
        derivative = error - self.previous_error
        self.previous_error = error

        # Calculate PID Adjustment
        adjustment = (
            self.kp * error +
            self.ki * self.integral +
            self.kd * derivative
        )

        # Apply adjustment with a cap to maintain stability
        # Now current_difficulty is guaranteed to be a number
        max_delta = max(1, int(round(current_difficulty * self.max_change_factor)))
        clamped_adjustment = max(min(adjustment, max_delta), -max_delta)

        delta = int(round(clamped_adjustment))
         if delta == 0 and clamped_adjustment != 0:
             delta = 1 if clamped_adjustment > 0 else -1
         new_difficulty = current_difficulty + delta

        # Safety: Difficulty must never drop below 1
        return max(1, new_difficulty)
