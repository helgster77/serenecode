"""Minimal tests for core.pipeline_helpers module."""

from __future__ import annotations

from serenecode.core.pipeline_helpers import _is_test_file_path


def test_is_test_file_path_detects_test_prefix() -> None:
    """Files starting with test_ are recognized as test files."""
    assert _is_test_file_path("test_foo.py") is True


def test_is_test_file_path_detects_tests_directory() -> None:
    """Files under a tests/ directory are recognized as test files."""
    assert _is_test_file_path("tests/unit/foo.py") is True


def test_is_test_file_path_rejects_regular_module() -> None:
    """Regular source files are not test files."""
    assert _is_test_file_path("src/serenecode/models.py") is False
