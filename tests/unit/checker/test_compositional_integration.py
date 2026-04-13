"""Minimal tests for checker.compositional_integration module."""

from __future__ import annotations

import serenecode.checker.compositional_integration as mod


def test_module_imports_successfully() -> None:
    """The compositional_integration module is importable."""
    assert hasattr(mod, "__name__")
    assert "compositional_integration" in mod.__name__
