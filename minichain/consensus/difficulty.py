import time


class PIDDifficultyAdjuster:
    def __init__(self, target_block_time=5, kp=0.5, ki=0.05, kd=0.1):
        self.target_block_time = target_block_time
        self.kp = kp
        self.ki = ki
        self.kd = kd

        self.integral = 0
        self.previous_error = 0
        self.last_block_time = time.monotonic()

    def adjust(self, current_difficulty):
        now = time.monotonic()
        actual_block_time = now - self.last_block_time
        self.last_block_time = now

        error = self.target_block_time - actual_block_time
        self.integral += error
        derivative = error - self.previous_error

        adjustment = (
            self.kp * error +
            self.ki * self.integral +
            self.kd * derivative
        )

        self.previous_error = error

        new_difficulty = current_difficulty + round(adjustment)

        if new_difficulty < 1:
            new_difficulty = 1

        return new_difficulty
