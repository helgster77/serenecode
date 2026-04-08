"""Adapter implementations for Serenecode.

This package contains I/O implementations of the Protocol interfaces
defined in the ports package. Adapters handle file system access,
subprocess execution, and external tool integration.
"""

from __future__ import annotations

import os

import icontract

# Only pass these environment variables to subprocess calls.
# This prevents leaking credentials, API keys, and other
# sensitive values from the parent process environment.
_SAFE_ENV_KEYS = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "SHELL",
    "TMPDIR",
    "SYSTEMROOT",       # Windows
    "COMSPEC",          # Windows
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    # Corporate proxies and TLS (subprocess tools often need these)
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "SSL_CERT_FILE",
    "CURL_CA_BUNDLE",
    "REQUESTS_CA_BUNDLE",
    "PYTHONWARNINGS",
    "TZ",
})


@icontract.require(
    lambda extra_paths: extra_paths is None or isinstance(extra_paths, dict),
    "extra_paths must be None or a dictionary",
)
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dictionary")
def safe_subprocess_env(
    *,
    extra_paths: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a subprocess environment with only safe variables.

    Filters os.environ to an allowlist of known-safe keys, then
    merges any extra path variables (PYTHONPATH, MYPYPATH, etc.).

    Args:
        extra_paths: Additional key-value pairs to set in the env.

    Returns:
        A filtered environment dictionary safe for subprocess calls.
    """
    env: dict[str, str] = {}
    # Loop invariant: env contains safe entries from os.environ[0..i]
    for key in _SAFE_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    if extra_paths is not None:
        env.update(extra_paths)
    if os.environ.get("SERENECODE_DEBUG"):
        import sys
        keys = sorted(env.keys())
        print(
            f"[serenecode] subprocess environment keys ({len(keys)}): {keys}",
            file=sys.stderr,
        )
    return env
