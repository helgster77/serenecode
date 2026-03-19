"""Module with async functions and contracts."""

import icontract


@icontract.require(lambda url: len(url) > 0, "url must be non-empty")
@icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
async def fetch_data(url: str) -> str:
    """Fetch data from a URL (async)."""
    return f"data from {url}"
