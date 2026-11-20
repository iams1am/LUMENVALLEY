"""Fixed-step simulation clock shared by every front-end.

The renderer may run at any rate; gameplay always advances in whole ``step``
increments.  Keeping the accumulator here means the terminal loop and the
pygame loop cannot drift apart in how they feed time to the simulation.
"""


class FixedStepClock:
    """Accumulate wall-clock time and release whole simulation steps."""

    def __init__(self, step):
        self.step = float(step)
        if self.step <= 0:
            raise ValueError("step must be positive")
        self.accumulator = 0.0

    def advance(self, elapsed):
        """Add *elapsed* seconds (clamped to 100 ms) and return step count.

        The clamp prevents a long pause (debugger, window drag, breakpoint)
        from turning into a burst of catch-up frames.
        """
        self.accumulator += max(0.0, min(0.1, float(elapsed)))
        steps = int(self.accumulator // self.step)
        self.accumulator -= steps * self.step
        # Guard against binary-float dust leaving the accumulator a hair above
        # zero after an exact number of steps.
        if self.accumulator < 1e-9:
            self.accumulator = 0.0
        return steps

    def sleep_time(self):
        """Seconds to sleep before the next step is due."""
        return max(0.0, self.step - self.accumulator)
