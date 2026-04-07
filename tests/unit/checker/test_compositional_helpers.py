"""Direct unit tests for the small AST helper functions in compositional.py.

Like the structural helpers, these are exercised transitively through
the higher-level checks but L3 coverage flags them as below threshold
because not every branch gets hit. This file adds focused, branch-level
tests for each helper.
"""

from __future__ import annotations

import ast
import textwrap
from typing import cast

from serenecode.checker.compositional import (
    MethodSignature,
    ModuleInfo,
    _find_file_for_module,
    _get_name,
    _is_public_function_name,
    _module_name_matches_reference,
    _module_package_name,
    _parse_method_signature,
    _resolve_from_import_module,
    parse_module_info,
)


def _first_function(src: str) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(src))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            return node
    raise AssertionError("no function in source")


def _first_import_from(src: str) -> ast.ImportFrom:
    tree = ast.parse(textwrap.dedent(src))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            return node
    raise AssertionError("no ImportFrom in source")


# ---------------------------------------------------------------------------
# _resolve_from_import_module
# ---------------------------------------------------------------------------


class TestResolveFromImportModule:
    def test_absolute_import(self) -> None:
        node = _first_import_from("from os import path")
        assert _resolve_from_import_module(node, "myproj/foo.py") == "os"

    def test_absolute_import_dotted(self) -> None:
        node = _first_import_from("from os.path import join")
        assert _resolve_from_import_module(node, "myproj/foo.py") == "os.path"

    def test_single_dot_relative(self) -> None:
        node = _first_import_from("from . import sibling")
        # current package is "myproj", level=1 ascends 0 → base is "myproj"
        assert _resolve_from_import_module(node, "myproj/foo.py") == "myproj"

    def test_double_dot_relative(self) -> None:
        node = _first_import_from("from .. import parent")
        # myproj/sub/foo.py: package = myproj.sub, level=2 → ascend 1 → myproj
        assert _resolve_from_import_module(node, "myproj/sub/foo.py") == "myproj"

    def test_relative_with_module(self) -> None:
        node = _first_import_from("from .sibling import thing")
        assert _resolve_from_import_module(node, "myproj/foo.py") == "myproj.sibling"

    def test_relative_ascends_past_package_root(self) -> None:
        """Branch: ascend > len(package_parts) — uses empty base_parts (line 287)."""
        node = _first_import_from("from ... import grandparent")
        # myproj/foo.py: package = "myproj" (1 part), level=3 → ascend 2, only 1 part
        # ascend > package_parts → base_parts = []
        # `from ...` has node.module = None, so resolved_parts stays empty → None
        result = _resolve_from_import_module(node, "myproj/foo.py")
        assert result is None

    def test_relative_with_module_ascending_past_package_root(self) -> None:
        """Branch: ascend > len(package_parts) AND node.module is set."""
        node = _first_import_from("from ...other import thing")
        result = _resolve_from_import_module(node, "myproj/foo.py")
        # base_parts is empty (ascended too far), but node.module='other' is appended
        assert result == "other"

    def test_relative_with_no_resolved_parts_returns_none(self) -> None:
        """Branch: resolved_parts is empty after ascent — returns None (line 296)."""
        node = _first_import_from("from . import sibling")
        # Module at root with package_path = "" makes the result empty
        result = _resolve_from_import_module(node, "")
        assert result is None


# ---------------------------------------------------------------------------
# _module_package_name
# ---------------------------------------------------------------------------


class TestModulePackageName:
    def test_dotted_module(self) -> None:
        assert _module_package_name("myproj/foo/bar.py") == "myproj.foo"

    def test_init_module(self) -> None:
        """Branch: name endswith .__init__ (line 314)."""
        assert _module_package_name("myproj/foo/__init__.py") == "myproj.foo"

    def test_top_level_module(self) -> None:
        """Branch: no dots in name (line 319)."""
        assert _module_package_name("foo.py") == ""

    def test_dotted_name_input(self) -> None:
        assert _module_package_name("myproj.foo.bar") == "myproj.foo"

    def test_empty_string(self) -> None:
        assert _module_package_name("") == ""


# ---------------------------------------------------------------------------
# _get_name
# ---------------------------------------------------------------------------


class TestGetName:
    def test_simple_name(self) -> None:
        node = ast.parse("foo", mode="eval").body
        assert _get_name(node) == "foo"

    def test_attribute(self) -> None:
        node = ast.parse("foo.bar", mode="eval").body
        assert _get_name(node) == "foo.bar"

    def test_dotted_attribute(self) -> None:
        node = ast.parse("a.b.c", mode="eval").body
        assert _get_name(node) == "a.b.c"

    def test_unknown_returns_placeholder(self) -> None:
        """Branch: not a Name and not an Attribute (line 652)."""
        node = ast.parse("42", mode="eval").body
        assert _get_name(node) == "<unknown>"

    def test_call_returns_unknown(self) -> None:
        node = ast.parse("foo()", mode="eval").body
        assert _get_name(node) == "<unknown>"


# ---------------------------------------------------------------------------
# _parse_method_signature
# ---------------------------------------------------------------------------


class TestParseMethodSignature:
    def test_basic_function(self) -> None:
        func = _first_function("def f(a, b): pass")
        sig = _parse_method_signature(func)
        assert sig.parameters == ("a", "b")
        assert sig.required_parameters == 2

    def test_with_defaults(self) -> None:
        func = _first_function("def f(a, b=1, c=2): pass")
        sig = _parse_method_signature(func)
        assert sig.parameters == ("a", "b", "c")
        assert sig.required_parameters == 1

    def test_self_skipped(self) -> None:
        cls_src = "class C:\n    def f(self, x): pass"
        cls = cast("ast.ClassDef", ast.parse(cls_src).body[0])
        method = cast("ast.FunctionDef", cls.body[0])
        sig = _parse_method_signature(method)
        assert "self" not in sig.parameters
        assert sig.parameters == ("x",)

    def test_kwonly_required(self) -> None:
        """Branch: kwonly arg with no default → required (lines 681-686)."""
        func = _first_function("def f(*, a, b=1): pass")
        sig = _parse_method_signature(func)
        assert "a" in sig.parameters
        assert "b" in sig.parameters
        # `a` has no default → required
        assert sig.required_parameters == 1

    def test_kwonly_self_skipped(self) -> None:
        """Branch: kwonly named self/cls is skipped (lines 682-683 continue)."""
        func = _first_function("def f(*, self, x=1): pass")
        sig = _parse_method_signature(func)
        # `self` is skipped, only x is in parameters
        assert "self" not in sig.parameters
        assert "x" in sig.parameters

    def test_vararg_included(self) -> None:
        """Branch: vararg present and not self/cls (line 689)."""
        func = _first_function("def f(*args): pass")
        sig = _parse_method_signature(func)
        assert "args" in sig.parameters

    def test_kwarg_included(self) -> None:
        """Branch: kwarg present and not self/cls (line 691)."""
        func = _first_function("def f(**kwargs): pass")
        sig = _parse_method_signature(func)
        assert "kwargs" in sig.parameters

    def test_with_return_annotation(self) -> None:
        func = _first_function("def f() -> int: pass")
        sig = _parse_method_signature(func)
        assert sig.has_return_annotation is True
        assert sig.return_annotation == "int"

    def test_without_return_annotation(self) -> None:
        func = _first_function("def f(): pass")
        sig = _parse_method_signature(func)
        assert sig.has_return_annotation is False
        assert sig.return_annotation is None


# ---------------------------------------------------------------------------
# _module_name_matches_reference
# ---------------------------------------------------------------------------


class TestModuleNameMatchesReference:
    def test_exact_match(self) -> None:
        assert _module_name_matches_reference("foo.bar", "foo.bar") is True

    def test_suffix_match(self) -> None:
        assert _module_name_matches_reference("a.b.c", "b.c") is True

    def test_no_match(self) -> None:
        assert _module_name_matches_reference("a.b.c", "x.y") is False

    def test_reference_longer_than_module(self) -> None:
        assert _module_name_matches_reference("foo", "a.b.c.foo") is False

    def test_empty_module(self) -> None:
        """Branch: normalized module is empty (line 1717)."""
        assert _module_name_matches_reference("", "foo") is False

    def test_empty_reference(self) -> None:
        assert _module_name_matches_reference("foo", "") is False

    def test_path_form(self) -> None:
        # The function normalizes path-form to dot-form
        assert _module_name_matches_reference("a/b/c.py", "b.c") is True


# ---------------------------------------------------------------------------
# _find_file_for_module
# ---------------------------------------------------------------------------


class TestIsPublicFunctionName:
    """Tests for _is_public_function_name — covers branch at line 335."""

    def test_public_name(self) -> None:
        assert _is_public_function_name("foo") is True

    def test_private_underscore(self) -> None:
        assert _is_public_function_name("_helper") is False

    def test_init_is_public(self) -> None:
        assert _is_public_function_name("__init__") is True

    def test_dunder_other_than_init_is_private(self) -> None:
        """Branch (line 335): __dunder__ that isn't __init__ → False."""
        assert _is_public_function_name("__repr__") is False
        assert _is_public_function_name("__str__") is False
        assert _is_public_function_name("__hash__") is False


class TestFindFileForModule:
    def _make_module(self, file_path: str, module_path: str) -> ModuleInfo:
        return parse_module_info('"""Doc."""\n', file_path, module_path)

    def test_finds_existing_module(self) -> None:
        m1 = self._make_module("a.py", "a.py")
        m2 = self._make_module("b.py", "b.py")
        assert _find_file_for_module("a.py", [m1, m2]) == "a.py"

    def test_returns_unknown_for_missing_module(self) -> None:
        """Branch: no module matches → return '<unknown>' (line 1958)."""
        m1 = self._make_module("a.py", "a.py")
        assert _find_file_for_module("nonexistent.py", [m1]) == "<unknown>"

    def test_empty_module_list(self) -> None:
        assert _find_file_for_module("foo.py", []) == "<unknown>"
