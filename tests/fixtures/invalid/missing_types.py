"""Module with functions missing type annotations."""

import icontract


@icontract.require(lambda x: x > 0, "x must be positive")
@icontract.ensure(lambda result: result > 0, "result must be positive")
def double(x, y):
    """Double a number (missing annotations)."""
    return x * 2
