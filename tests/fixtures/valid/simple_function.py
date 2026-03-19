"""A simple module with a properly contracted function."""

import icontract


@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result: result >= 0, "result must be non-negative")
def square(x: float) -> float:
    """Square a non-negative number."""
    return x * x
