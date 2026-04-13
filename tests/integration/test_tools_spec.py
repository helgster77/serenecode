"""Minimal tests for mcp.tools_spec module."""

from __future__ import annotations

import serenecode.mcp.tools_spec as mod


def test_module_imports_successfully() -> None:
    """The tools_spec module is importable."""
    assert hasattr(mod, "__name__")
    assert "tools_spec" in mod.__name__
