"""Module using from-import style for icontract."""

from icontract import require, ensure


@require(lambda x: x >= 0, "x must be non-negative")
@ensure(lambda result: result >= 0, "result must be non-negative")
def square(x: float) -> float:
    """Square a number using from-import style."""
    return x * x
