"""Minimal tests for cli_helpers module."""

from __future__ import annotations

from serenecode.cli_helpers import _env_int_or


def test_env_int_or_returns_fallback_when_env_unset(monkeypatch: object) -> None:
    """_env_int_or returns the fallback when the env var is not set."""
    import os

    env_name = "SERENECODE_TEST_NONEXISTENT_VAR_12345"
    # Ensure the variable is not set
    os.environ.pop(env_name, None)
    assert _env_int_or(env_name, 4) == 4


def test_env_int_or_reads_env_variable(monkeypatch: object) -> None:
    """_env_int_or reads the environment variable when set."""
    import os

    env_name = "SERENECODE_TEST_CLI_HELPERS_VAR"
    os.environ[env_name] = "7"
    try:
        assert _env_int_or(env_name, 4) == 7
    finally:
        os.environ.pop(env_name, None)
