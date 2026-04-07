"""Module with async functions and contracts."""

import icontract


@icontract.require(lambda url: len(url) > 0, "url must be non-empty")
@icontract.ensure(lambda url, result: url in result, "result must reference the requested url")
async def fetch_data(url: str) -> str:
    """Fetch data from a URL (async)."""
    return f"data from {url}"
