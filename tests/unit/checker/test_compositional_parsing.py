"""Minimal tests for checker.compositional_parsing module."""

from __future__ import annotations

from serenecode.checker.compositional_parsing import parse_module_info


def test_parse_module_info_is_callable() -> None:
    """parse_module_info exists and is callable."""
    assert callable(parse_module_info)
