"""Module with a function whose implementation violates its postcondition.

This fixture is used to test Level 3 (Hypothesis) and Level 4 (CrossHair)
verification — both should find counterexamples demonstrating the violation.
"""

import icontract


@icontract.require(lambda x: isinstance(x, int), "x must be an integer")
@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result: result >= 0, "result must be non-negative")
def absolute_value(x: int) -> int:
    """Return the absolute value of a non-negative integer.

    Bug: returns negative for x == 0 due to off-by-one.
    """
    return x - 1  # Bug: abs(0) returns -1


@icontract.require(lambda items: len(items) > 0, "items must be non-empty")
@icontract.ensure(lambda items, result: result <= max(items), "result must not exceed max")
@icontract.ensure(lambda items, result: result >= min(items), "result must not be below min")
def compute_mean(items: list[float]) -> float:
    """Compute the mean of a list of numbers.

    Correct implementation — should pass verification.
    """
    # Loop invariant: total accumulates sum of items[0..i]
    total = sum(items)
    return total / len(items)
