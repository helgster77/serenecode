"""Coverage-gap tests for AST-based private helpers.

Covers the attribute-style decorator branch of _has_override_decorator and
the opt-out comment detection of _has_allow_many_params.
"""

from __future__ import annotations

import ast

from serenecode.checker.structural_helpers import _has_override_decorator
from serenecode.core.module_health import _has_allow_many_params


def _first_function(source: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Return the first function definition parsed from source."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node
    raise AssertionError("no function definition found in source")


class TestHasOverrideDecorator:
    """Tests for _has_override_decorator."""

    def test_bare_override_name_returns_true(self) -> None:
        node = _first_function(
            "@override\ndef method(self) -> None:\n    pass\n"
        )
        assert _has_override_decorator(node) is True

    def test_attribute_style_typing_override_returns_true(self) -> None:
        node = _first_function(
            "import typing\n\n@typing.override\ndef method(self) -> None:\n    pass\n"
        )
        assert _has_override_decorator(node) is True

    def test_attribute_style_typing_extensions_override_returns_true(self) -> None:
        node = _first_function(
            "import typing_extensions\n\n"
            "@typing_extensions.override\ndef method(self) -> None:\n    pass\n"
        )
        assert _has_override_decorator(node) is True

    def test_attribute_style_non_override_returns_false(self) -> None:
        node = _first_function(
            "import typing\n\n@typing.final\ndef method(self) -> None:\n    pass\n"
        )
        assert _has_override_decorator(node) is False

    def test_no_decorators_returns_false(self) -> None:
        node = _first_function("def method(self) -> None:\n    pass\n")
        assert _has_override_decorator(node) is False

    def test_other_name_decorator_returns_false(self) -> None:
        node = _first_function(
            "@staticmethod\ndef method() -> None:\n    pass\n"
        )
        assert _has_override_decorator(node) is False


class TestHasAllowManyParams:
    """Tests for _has_allow_many_params."""

    def test_comment_on_def_line_returns_true(self) -> None:
        source = (
            "def wide(a, b, c, d, e, f):  # allow-many-params: config bundle\n"
            "    pass\n"
        )
        node = _first_function(source)
        assert _has_allow_many_params(source, node) is True

    def test_comment_on_line_above_returns_true(self) -> None:
        source = (
            "# allow-many-params: legacy signature\n"
            "def wide(a, b, c, d, e, f):\n"
            "    pass\n"
        )
        node = _first_function(source)
        assert _has_allow_many_params(source, node) is True

    def test_no_comment_returns_false(self) -> None:
        source = "def wide(a, b, c, d, e, f):\n    pass\n"
        node = _first_function(source)
        assert _has_allow_many_params(source, node) is False

    def test_unrelated_comment_above_returns_false(self) -> None:
        source = (
            "# just a regular comment\n"
            "def wide(a, b, c):\n"
            "    pass\n"
        )
        node = _first_function(source)
        assert _has_allow_many_params(source, node) is False

    def test_def_on_first_line_handles_out_of_range_lookback(self) -> None:
        source = "def f(a):\n    pass\n"
        node = _first_function(source)
        assert node.lineno == 1
        assert _has_allow_many_params(source, node) is False
