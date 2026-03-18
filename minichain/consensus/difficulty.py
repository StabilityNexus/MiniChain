import time

class PIDDifficultyAdjuster:
    SCALE = 1000  # Fixed-point scaling factor

    def __init__(self, target_block_time=5, kp=500, ki=50, kd=100):
        self.target_block_time = target_block_time

        # PID Coefficients (scaled integers)
        self.kp = kp      # 0.5  -> 500
        self.ki = ki      # 0.05 -> 50
        self.kd = kd      # 0.1  -> 100

        self.integral = 0
        self.previous_error = 0
        self.last_block_time = time.monotonic()

        self.integral_limit = 100 * self.SCALE
        self.max_change_factor = 0.1  # safe to keep as float OR convert too

    def adjust(self, current_difficulty, actual_block_time=None):

        if current_difficulty is None:
            current_difficulty = 1000

        if actual_block_time is None:
            now = time.monotonic()
            actual_block_time = now - self.last_block_time
            self.last_block_time = now

        # Convert time to scaled integer
        actual_block_time = int(actual_block_time * self.SCALE)
        target_time = int(self.target_block_time * self.SCALE)

        error = target_time - actual_block_time

        # Integral (clamped)
        self.integral = max(
            min(self.integral + error, self.integral_limit),
            -self.integral_limit
        )

        derivative = error - self.previous_error
        self.previous_error = error

        # Integer PID calculation
        adjustment = (
            self.kp * error +
            self.ki * self.integral +
            self.kd * derivative
        ) // self.SCALE  # scale back

        max_delta = max(1, int(current_difficulty * self.max_change_factor))

        clamped_adjustment = max(min(adjustment, max_delta), -max_delta)

        delta = int(clamped_adjustment)

        if delta == 0 and clamped_adjustment != 0:
            delta = 1 if clamped_adjustment > 0 else -1

        new_difficulty = current_difficulty + delta

        return max(1, new_difficulty)
