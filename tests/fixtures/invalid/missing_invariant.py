"""Module with a class missing its invariant."""

import icontract


class Counter:
    """A counter without an invariant."""

    @icontract.require(lambda start: start >= 0, "start must be non-negative")
    @icontract.ensure(lambda self: True, "always succeeds")
    def __init__(self, start: int) -> None:
        """Initialize counter."""
        self.value = start
