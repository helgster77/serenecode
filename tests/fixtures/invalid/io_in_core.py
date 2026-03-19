"""Module that imports I/O in what would be a core module."""

import os

import icontract


@icontract.require(lambda path: len(path) > 0, "path must be non-empty")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be boolean")
def check_path(path: str) -> bool:
    """Check if a path exists (forbidden in core)."""
    return os.path.exists(path)
