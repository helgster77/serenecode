"""Minimal tests for checker.structural_quality module."""

from __future__ import annotations

import ast

from serenecode.checker.structural_quality import _is_stub_body


def test_is_stub_body_pass_only() -> None:
    """A function body containing only 'pass' is a stub."""
    tree = ast.parse("def f():\n    pass")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    assert _is_stub_body(func.body) is True


def test_is_stub_body_real_body() -> None:
    """A function body with a return statement is not a stub."""
    tree = ast.parse("def f():\n    return 42")
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    assert _is_stub_body(func.body) is False
