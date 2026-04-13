"""Minimal tests for core.hypothesis_refinement module.

Note: hypothesis_refinement is re-exported via hypothesis_strategies.
This test verifies the re-exported symbols are accessible.
"""

from __future__ import annotations

from serenecode.adapters.hypothesis_strategies import (
    _refine_strategies_with_preconditions,
)


def test_refine_strategies_with_preconditions_is_callable() -> None:
    """The re-exported refinement function is callable."""
    assert callable(_refine_strategies_with_preconditions)
