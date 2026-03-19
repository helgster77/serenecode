"""A complete module following all SERENECODE.md conventions.

This module demonstrates a fully compliant implementation with
contracts, type annotations, docstrings, and loop invariants.
"""

import icontract


@icontract.invariant(lambda self: self._count >= 0, "count must be non-negative")
class Counter:
    """A simple counter that can only hold non-negative values."""

    @icontract.require(lambda start: start >= 0, "start must be non-negative")
    @icontract.ensure(lambda self: self._count >= 0, "count is non-negative after init")
    def __init__(self, start: int = 0) -> None:
        """Initialize counter with a non-negative start value."""
        self._count = start

    @icontract.require(lambda self, n: n > 0, "n must be positive")
    @icontract.ensure(lambda self, OLD, n: self._count == OLD.self._count + n, "count increases by n")
    def increment(self, n: int = 1) -> None:
        """Increment the counter by n."""
        self._count += n

    @icontract.require(lambda self, n: 0 < n <= self._count, "n must be valid for decrement")
    @icontract.ensure(lambda self, OLD, n: self._count == OLD.self._count - n, "count decreases by n")
    def decrement(self, n: int = 1) -> None:
        """Decrement the counter by n."""
        self._count -= n


@icontract.require(lambda items: len(items) > 0, "items must be non-empty")
@icontract.require(lambda items: all(isinstance(x, (int, float)) for x in items), "all items must be numeric")
@icontract.ensure(lambda result: isinstance(result, float), "result must be a float")
def compute_average(items: list[float]) -> float:
    """Compute the average of a non-empty list of numbers."""
    # Loop invariant: total accumulates sum of items[0..i]
    total = sum(items)
    return total / len(items)


@icontract.require(lambda items: isinstance(items, list), "items must be a list")
@icontract.require(lambda target: isinstance(target, int), "target must be an int")
@icontract.ensure(lambda result: result >= -1, "result is -1 or valid index")
def binary_search(items: list[int], target: int) -> int:
    """Find target in a sorted list using binary search."""
    low, high = 0, len(items) - 1
    # Loop invariant: if target is in items, it is in items[low..high]
    # Variant: high - low decreases each iteration
    while low <= high:
        mid = (low + high) // 2
        if items[mid] == target:
            return mid
        elif items[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
