"""Minimal tests for adapters.hypothesis_strategies module."""

from __future__ import annotations

import serenecode.adapters.hypothesis_strategies as mod


def test_module_imports_successfully() -> None:
    """The hypothesis_strategies module is importable."""
    assert hasattr(mod, "__name__")
    assert "hypothesis_strategies" in mod.__name__
