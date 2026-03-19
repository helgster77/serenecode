"""Module using aliased icontract import."""

import icontract as ic


@ic.require(lambda x: x >= 0, "x must be non-negative")
@ic.ensure(lambda result: result >= 0, "result must be non-negative")
def square(x: float) -> float:
    """Square a number using aliased import."""
    return x * x
