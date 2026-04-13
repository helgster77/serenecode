"""Minimal tests for adapters.coverage_suggestions module.

Imports go through coverage_adapter to avoid the circular import between
coverage_suggestions and coverage_adapter (they share private types).
"""

from __future__ import annotations

from serenecode.adapters.coverage_adapter import _group_contiguous_lines


def test_group_contiguous_lines_empty() -> None:
    """An empty list produces no groups."""
    assert _group_contiguous_lines([]) == []


def test_group_contiguous_lines_single_block() -> None:
    """Consecutive lines form one group."""
    assert _group_contiguous_lines([1, 2, 3]) == [[1, 2, 3]]


def test_group_contiguous_lines_multiple_blocks() -> None:
    """Non-consecutive lines form separate groups."""
    assert _group_contiguous_lines([1, 2, 5, 6, 7]) == [[1, 2], [5, 6, 7]]
