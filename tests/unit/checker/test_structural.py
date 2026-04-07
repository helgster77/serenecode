"""Tests for the Level 1 structural checker.

Each check function gets its own test class with passing, failing,
and edge case tests.
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from serenecode.checker.structural import (
    IcontractNames,
    _find_tautological_contracts,
    _is_test_module,
    _is_tautological_lambda,
    _decorator_descriptions_are_literals,
    check_bare_asserts_outside_tests,
    check_class_invariants,
    check_contracts,
    check_dangerous_calls,
    check_docstrings,
    check_exception_types,
    check_imports,
    check_loop_invariants,
    check_mutable_default_arguments,
    check_naming_conventions,
    check_no_any_in_core,
    check_no_assertions_in_tests,
    check_print_in_core,
    check_silent_exception_handling,
    check_structural,
    check_stub_residue,
    check_tautological_isinstance_postcondition,
    check_todo_comments,
    check_type_annotations,
    check_unused_parameters,
    resolve_icontract_aliases,
)
from serenecode.config import default_config, minimal_config, strict_config
from serenecode.models import CheckStatus


def _parse(source: str) -> ast.Module:
    """Helper to parse source code."""
    return ast.parse(textwrap.dedent(source))


def _aliases_standard() -> IcontractNames:
    """Standard icontract aliases (import icontract)."""
    return IcontractNames(
        module_alias="icontract",
        require_names=frozenset({"icontract.require"}),
        ensure_names=frozenset({"icontract.ensure"}),
        invariant_names=frozenset({"icontract.invariant"}),
    )


def _aliases_from_import() -> IcontractNames:
    """From-import style aliases."""
    return IcontractNames(
        module_alias=None,
        require_names=frozenset({"require"}),
        ensure_names=frozenset({"ensure"}),
        invariant_names=frozenset({"invariant"}),
    )


class TestResolveIcontractAliases:
    """Tests for import alias resolution."""

    def test_import_icontract(self) -> None:
        tree = _parse("import icontract")
        aliases = resolve_icontract_aliases(tree)
        assert aliases.module_alias == "icontract"
        assert "icontract.require" in aliases.require_names
        assert "icontract.ensure" in aliases.ensure_names
        assert "icontract.invariant" in aliases.invariant_names

    def test_import_icontract_as_ic(self) -> None:
        tree = _parse("import icontract as ic")
        aliases = resolve_icontract_aliases(tree)
        assert aliases.module_alias == "ic"
        assert "ic.require" in aliases.require_names

    def test_from_import(self) -> None:
        tree = _parse("from icontract import require, ensure, invariant")
        aliases = resolve_icontract_aliases(tree)
        assert "require" in aliases.require_names
        assert "ensure" in aliases.ensure_names
        assert "invariant" in aliases.invariant_names

    def test_from_import_with_alias(self) -> None:
        tree = _parse("from icontract import require as req")
        aliases = resolve_icontract_aliases(tree)
        assert "req" in aliases.require_names

    def test_no_icontract_import(self) -> None:
        tree = _parse("import os")
        aliases = resolve_icontract_aliases(tree)
        assert len(aliases.require_names) == 0
        assert len(aliases.ensure_names) == 0


class TestCheckContracts:
    """Tests for contract checking."""

    def test_function_with_contracts_passes(self) -> None:
        source = textwrap.dedent("""\
            import icontract

            @icontract.require(lambda x: x > 0, "x must be positive")
            @icontract.ensure(lambda result: result > 0, "result positive")
            def double(x: int) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = resolve_icontract_aliases(tree)
        config = default_config()
        results = check_contracts(tree, config, aliases, "test.py")
        passed = [r for r in results if r.status == CheckStatus.PASSED]
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(passed) == 1
        assert len(failed) == 0

    def test_function_missing_require(self) -> None:
        source = textwrap.dedent("""\
            import icontract

            @icontract.ensure(lambda result: result > 0, "result positive")
            def double(x: int) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = resolve_icontract_aliases(tree)
        results = check_contracts(tree, default_config(), aliases, "test.py")
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(failed) == 1
        assert "require" in failed[0].details[0].message.lower()

    def test_positional_only_parameter_requires_precondition(self) -> None:
        source = textwrap.dedent("""\
            import icontract

            @icontract.ensure(lambda result: result > 0, "result positive")
            def double(x: int, /) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = resolve_icontract_aliases(tree)
        results = check_contracts(tree, default_config(), aliases, "test.py")

        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED
        assert "require" in results[0].details[0].message.lower()

    def test_function_missing_ensure(self) -> None:
        source = textwrap.dedent("""\
            import icontract

            @icontract.require(lambda x: x > 0, "x must be positive")
            def double(x: int) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = resolve_icontract_aliases(tree)
        results = check_contracts(tree, default_config(), aliases, "test.py")
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(failed) == 1
        assert "ensure" in failed[0].details[0].message.lower()

    def test_function_missing_both(self) -> None:
        source = textwrap.dedent("""\
            def double(x: int) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_contracts(tree, default_config(), aliases, "test.py")
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(failed) == 1
        assert len(failed[0].details) == 2

    def test_private_function_skipped(self) -> None:
        source = textwrap.dedent("""\
            def _helper(x: int) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_contracts(tree, default_config(), aliases, "test.py")
        assert len(results) == 0

    def test_private_function_checked_in_strict_mode(self) -> None:
        source = textwrap.dedent("""\
            def _helper(x: int) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_contracts(tree, strict_config(), aliases, "test.py")
        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED

    def test_nested_local_function_not_checked(self) -> None:
        source = textwrap.dedent("""\
            def outer(x: int) -> int:
                def helper(y: int) -> int:
                    return y + 1
                return helper(x)
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_contracts(tree, strict_config(), aliases, "test.py")
        names = [result.function for result in results]
        assert "outer" in names
        assert "helper" not in names

    def test_dunder_method_skipped(self) -> None:
        source = textwrap.dedent("""\
            def __repr__(self) -> str:
                return "test"
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_contracts(tree, default_config(), aliases, "test.py")
        assert len(results) == 0

    def test_init_method_checked(self) -> None:
        source = textwrap.dedent("""\
            class Foo:
                def __init__(self, x: int) -> None:
                    self.x = x
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_contracts(tree, default_config(), aliases, "test.py")
        init_results = [r for r in results if r.function == "__init__"]
        assert len(init_results) == 1
        assert init_results[0].status == CheckStatus.FAILED

    def test_from_import_style_recognized(self) -> None:
        source = textwrap.dedent("""\
            from icontract import require, ensure

            @require(lambda x: x > 0, "x must be positive")
            @ensure(lambda result: result > 0, "result positive")
            def double(x: int) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = resolve_icontract_aliases(tree)
        results = check_contracts(tree, default_config(), aliases, "test.py")
        passed = [r for r in results if r.status == CheckStatus.PASSED]
        assert len(passed) == 1

    def test_aliased_import_recognized(self) -> None:
        source = textwrap.dedent("""\
            import icontract as ic

            @ic.require(lambda x: x > 0, "x must be positive")
            @ic.ensure(lambda result: result > 0, "result positive")
            def double(x: int) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = resolve_icontract_aliases(tree)
        results = check_contracts(tree, default_config(), aliases, "test.py")
        passed = [r for r in results if r.status == CheckStatus.PASSED]
        assert len(passed) == 1

    def test_missing_description_string(self) -> None:
        source = textwrap.dedent("""\
            import icontract

            @icontract.require(lambda x: x > 0)
            @icontract.ensure(lambda result: result > 0)
            def double(x: int) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = resolve_icontract_aliases(tree)
        config = default_config()
        results = check_contracts(tree, config, aliases, "test.py")
        failed = [r for r in results if r.status == CheckStatus.FAILED]
        assert len(failed) == 1
        assert "description" in failed[0].details[0].message.lower()

    def test_description_not_required_in_minimal(self) -> None:
        source = textwrap.dedent("""\
            import icontract

            @icontract.require(lambda x: x > 0)
            @icontract.ensure(lambda result: result > 0)
            def double(x: int) -> int:
                return x * 2
        """)
        tree = ast.parse(source)
        aliases = resolve_icontract_aliases(tree)
        config = minimal_config()
        results = check_contracts(tree, config, aliases, "test.py")
        passed = [r for r in results if r.status == CheckStatus.PASSED]
        assert len(passed) == 1


class TestCheckClassInvariants:
    """Tests for class invariant checking."""

    def test_class_with_invariant_passes(self) -> None:
        source = textwrap.dedent("""\
            import icontract

            @icontract.invariant(lambda self: self.x >= 0, "x non-negative")
            class Foo:
                pass
        """)
        tree = ast.parse(source)
        aliases = resolve_icontract_aliases(tree)
        results = check_class_invariants(tree, default_config(), aliases, "test.py")
        assert len(results) == 1
        assert results[0].status == CheckStatus.PASSED

    def test_class_missing_invariant_fails(self) -> None:
        source = textwrap.dedent("""\
            class Foo:
                pass
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_class_invariants(tree, default_config(), aliases, "test.py")
        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED

    def test_private_class_skipped(self) -> None:
        source = textwrap.dedent("""\
            class _Internal:
                pass
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_class_invariants(tree, default_config(), aliases, "test.py")
        assert len(results) == 0

    def test_private_class_checked_in_strict_mode(self) -> None:
        source = textwrap.dedent("""\
            class _Internal:
                pass
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_class_invariants(tree, strict_config(), aliases, "test.py")
        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED

    def test_nested_local_class_not_checked(self) -> None:
        source = textwrap.dedent("""\
            def outer() -> type[object]:
                class Helper:
                    pass
                return Helper
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_class_invariants(tree, strict_config(), aliases, "test.py")
        assert results == []

    def test_minimal_config_skips_invariants(self) -> None:
        source = textwrap.dedent("""\
            class Foo:
                pass
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_class_invariants(tree, minimal_config(), aliases, "test.py")
        assert len(results) == 0


class TestCheckTypeAnnotations:
    """Tests for type annotation checking."""

    def test_fully_annotated_passes(self) -> None:
        source = textwrap.dedent("""\
            def add(x: int, y: int) -> int:
                return x + y
        """)
        tree = ast.parse(source)
        results = check_type_annotations(tree, default_config(), "test.py")
        assert len(results) == 0  # no failures

    def test_missing_parameter_annotation(self) -> None:
        source = textwrap.dedent("""\
            def add(x, y: int) -> int:
                return x + y
        """)
        tree = ast.parse(source)
        results = check_type_annotations(tree, default_config(), "test.py")
        assert len(results) == 1
        assert "x" in results[0].details[0].message

    def test_missing_return_type(self) -> None:
        source = textwrap.dedent("""\
            def add(x: int, y: int):
                return x + y
        """)
        tree = ast.parse(source)
        results = check_type_annotations(tree, default_config(), "test.py")
        assert len(results) == 1
        assert "return" in results[0].details[0].message.lower()

    def test_self_skipped(self) -> None:
        source = textwrap.dedent("""\
            class Foo:
                def method(self, x: int) -> int:
                    return x
        """)
        tree = ast.parse(source)
        results = check_type_annotations(tree, default_config(), "test.py")
        assert len(results) == 0

    def test_cls_skipped(self) -> None:
        source = textwrap.dedent("""\
            class Foo:
                @classmethod
                def create(cls, x: int) -> 'Foo':
                    return cls(x)
        """)
        tree = ast.parse(source)
        results = check_type_annotations(tree, default_config(), "test.py")
        assert len(results) == 0

    def test_star_args_checked(self) -> None:
        source = textwrap.dedent("""\
            def func(*args, **kwargs) -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_type_annotations(tree, default_config(), "test.py")
        assert len(results) == 1
        assert len(results[0].details) == 2  # *args and **kwargs

    def test_keyword_only_arg_checked(self) -> None:
        source = textwrap.dedent("""\
            def func(*, item) -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_type_annotations(tree, default_config(), "test.py")

        assert len(results) == 1
        assert "item" in results[0].details[0].message

    def test_positional_only_arg_checked(self) -> None:
        source = textwrap.dedent("""\
            def func(item, /) -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_type_annotations(tree, default_config(), "test.py")

        assert len(results) == 1
        assert "item" in results[0].details[0].message

    def test_private_function_annotations_checked(self) -> None:
        source = textwrap.dedent("""\
            def _helper(item) -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_type_annotations(tree, default_config(), "test.py")

        assert len(results) == 1
        assert "_helper" == results[0].function


class TestCheckNoAnyInCore:
    """Tests for Any type usage checking."""

    def test_any_in_core_fails(self) -> None:
        source = textwrap.dedent("""\
            from typing import Any

            def process(data: Any) -> Any:
                return data
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_no_any_in_core(tree, config, "core/engine.py", "test.py")
        assert len(results) > 0

    def test_any_in_non_core_passes(self) -> None:
        source = textwrap.dedent("""\
            from typing import Any

            def process(data: Any) -> Any:
                return data
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_no_any_in_core(tree, config, "adapters/local_fs.py", "test.py")
        assert len(results) == 0

    def test_no_any_passes(self) -> None:
        source = textwrap.dedent("""\
            def process(data: str) -> str:
                return data
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_no_any_in_core(tree, config, "core/engine.py", "test.py")
        assert len(results) == 0


class TestCheckImports:
    """Tests for import checking in core modules."""

    def test_os_import_in_core_fails(self) -> None:
        source = textwrap.dedent("""\
            import os
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_imports(tree, config, "core/engine.py", "test.py")
        assert len(results) == 1
        assert "os" in results[0].details[0].message

    def test_from_pathlib_in_core_fails(self) -> None:
        source = textwrap.dedent("""\
            from pathlib import Path
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_imports(tree, config, "core/engine.py", "test.py")
        assert len(results) == 1

    def test_os_in_adapter_passes(self) -> None:
        source = textwrap.dedent("""\
            import os
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_imports(tree, config, "adapters/local_fs.py", "test.py")
        assert len(results) == 0

    def test_ast_allowed_in_checker(self) -> None:
        source = textwrap.dedent("""\
            import ast
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_imports(tree, config, "checker/structural.py", "test.py")
        assert len(results) == 0  # ast is not in forbidden list

    def test_stdlib_non_io_allowed(self) -> None:
        source = textwrap.dedent("""\
            import re
            import dataclasses
            import enum
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_imports(tree, config, "core/engine.py", "test.py")
        assert len(results) == 0


class TestCheckDocstrings:
    """Tests for docstring checking."""

    def test_module_with_docstring_passes(self) -> None:
        source = textwrap.dedent('''\
            """Module docstring."""

            def func() -> None:
                """Function docstring."""
                pass
        ''')
        tree = ast.parse(source)
        results = check_docstrings(tree, default_config(), "test.py")
        assert all(r.status == CheckStatus.PASSED or "module" not in r.function.lower()
                    for r in results)

    def test_missing_module_docstring_fails(self) -> None:
        source = textwrap.dedent("""\
            x = 1
        """)
        tree = ast.parse(source)
        results = check_docstrings(tree, default_config(), "test.py")
        module_results = [r for r in results if r.function == "<module>"]
        assert len(module_results) == 1
        assert module_results[0].status == CheckStatus.FAILED

    def test_missing_function_docstring_fails(self) -> None:
        source = textwrap.dedent('''\
            """Module doc."""

            def func() -> None:
                pass
        ''')
        tree = ast.parse(source)
        results = check_docstrings(tree, default_config(), "test.py")
        func_results = [r for r in results if r.function == "func"]
        assert len(func_results) == 1
        assert func_results[0].status == CheckStatus.FAILED

    def test_missing_class_docstring_fails(self) -> None:
        source = textwrap.dedent('''\
            """Module doc."""

            class Foo:
                pass
        ''')
        tree = ast.parse(source)
        results = check_docstrings(tree, default_config(), "test.py")
        class_results = [r for r in results if r.function == "Foo"]
        assert len(class_results) == 1
        assert class_results[0].status == CheckStatus.FAILED

    def test_private_function_docstring_not_checked(self) -> None:
        source = textwrap.dedent('''\
            """Module doc."""

            def _helper() -> None:
                pass
        ''')
        tree = ast.parse(source)
        results = check_docstrings(tree, default_config(), "test.py")
        helper_results = [r for r in results if r.function == "_helper"]
        assert len(helper_results) == 0


class TestCheckLoopInvariants:
    """Tests for loop invariant comment checking."""

    def test_invalid_unicode_source_is_treated_like_unreadable_tokens(self) -> None:
        tree = _parse(
            """\
            def demo() -> None:
                for _ in range(1):
                    pass
            """
        )

        results = check_loop_invariants("\udcff", tree, default_config(), "test.py")

        assert results == []

    def test_loop_with_invariant_passes(self) -> None:
        source = textwrap.dedent("""\
            \"\"\"Module doc.\"\"\"

            def func() -> None:
                # Loop invariant: i increases monotonically
                for i in range(10):
                    pass
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, default_config(), "test.py")
        assert len(results) == 0

    def test_loop_without_invariant_fails(self) -> None:
        source = textwrap.dedent("""\
            \"\"\"Module doc.\"\"\"

            def func() -> None:
                for i in range(10):
                    pass
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, strict_config(), "test.py")
        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED

    def test_while_loop_needs_invariant(self) -> None:
        source = textwrap.dedent("""\
            \"\"\"Module doc.\"\"\"

            def func() -> None:
                x = 10
                while x > 0:
                    x -= 1
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, strict_config(), "test.py")
        assert len(results) == 1

    def test_recursive_function_needs_variant(self) -> None:
        source = textwrap.dedent("""\
            \"\"\"Module doc.\"\"\"

            def factorial(n: int) -> int:
                if n <= 1:
                    return 1
                return n * factorial(n - 1)
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, strict_config(), "test.py")
        assert len(results) >= 1
        variant_results = [r for r in results if r.details and "variant" in r.details[0].message.lower()]
        assert len(variant_results) == 1

    def test_recursive_function_with_variant_passes(self) -> None:
        source = textwrap.dedent("""\
            \"\"\"Module doc.\"\"\"

            def factorial(n: int) -> int:
                # Variant: n decreases towards 0
                if n <= 1:
                    return 1
                return n * factorial(n - 1)
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, default_config(), "test.py")
        variant_results = [
            r for r in results
            if r.details and r.function != "<loop>" and "variant" in r.details[0].message.lower()
        ]
        assert len(variant_results) == 0

    def test_minimal_config_skips_loop_invariants(self) -> None:
        source = textwrap.dedent("""\
            \"\"\"Module doc.\"\"\"

            def func() -> None:
                for i in range(10):
                    pass
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, minimal_config(), "test.py")
        assert len(results) == 0

    def test_default_config_skips_loop_invariants(self) -> None:
        source = textwrap.dedent("""\
            \"\"\"Module doc.\"\"\"

            def func() -> None:
                for i in range(10):
                    pass
        """)
        tree = ast.parse(source)
        results = check_loop_invariants(source, tree, default_config(), "test.py")
        assert len(results) == 0


class TestCheckExceptionTypes:
    """Tests for exception type checking."""

    def test_default_config_skips_exception_checks(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                raise Exception("bad")
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_exception_types(tree, config, "core/engine.py", "test.py")
        assert len(results) == 0

    def test_raising_domain_exception_passes(self) -> None:
        source = textwrap.dedent("""\
            class MyError(Exception):
                pass

            def func() -> None:
                raise MyError("something went wrong")
        """)
        tree = ast.parse(source)
        config = strict_config()
        results = check_exception_types(tree, config, "core/engine.py", "test.py")
        assert len(results) == 0

    def test_raising_bare_exception_fails(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                raise Exception("bad")
        """)
        tree = ast.parse(source)
        config = strict_config()
        results = check_exception_types(tree, config, "core/engine.py", "test.py")
        assert len(results) == 1

    def test_raising_value_error_fails(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                raise ValueError("bad")
        """)
        tree = ast.parse(source)
        config = strict_config()
        results = check_exception_types(tree, config, "core/engine.py", "test.py")
        assert len(results) == 1

    def test_exception_in_adapter_passes(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                raise ValueError("bad")
        """)
        tree = ast.parse(source)
        config = strict_config()
        results = check_exception_types(tree, config, "adapters/local_fs.py", "test.py")
        assert len(results) == 0


class TestCheckSilentExceptionHandling:
    """Tests for silent exception handling detection."""

    def test_minimal_config_skips_silent_exception_check(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    risky()
                except Exception:
                    pass
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, minimal_config(), "test.py")
        assert len(results) == 0

    def test_except_pass_is_flagged(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    risky()
                except Exception:
                    pass
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 1
        assert "silent" in results[0].details[0].message.lower()

    def test_except_ellipsis_is_flagged(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    risky()
                except Exception:
                    ...
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 1

    def test_except_return_none_is_flagged(self) -> None:
        source = textwrap.dedent("""\
            def func() -> int | None:
                try:
                    return risky()
                except Exception:
                    return None
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 1

    def test_except_return_constant_is_flagged(self) -> None:
        source = textwrap.dedent("""\
            def func() -> str:
                try:
                    return risky()
                except Exception:
                    return ""
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 1

    def test_except_return_empty_list_is_flagged(self) -> None:
        source = textwrap.dedent("""\
            def func() -> list[int]:
                try:
                    return risky()
                except (TypeError, ValueError):
                    return []
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 1

    def test_except_continue_in_loop_is_flagged(self) -> None:
        source = textwrap.dedent("""\
            def func(items: list[int]) -> None:
                for item in items:
                    try:
                        process(item)
                    except Exception:
                        continue
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 1

    def test_bare_except_is_flagged(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    risky()
                except:
                    raise
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 1
        assert "bare" in results[0].details[0].message.lower()

    def test_except_with_reraise_passes(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    risky()
                except ValueError:
                    raise
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 0

    def test_except_translating_to_domain_exception_passes(self) -> None:
        source = textwrap.dedent("""\
            class DomainError(Exception):
                pass

            def func() -> None:
                try:
                    risky()
                except ValueError as exc:
                    raise DomainError("wrapped") from exc
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 0

    def test_except_using_exception_variable_passes(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    risky()
                except Exception as exc:
                    log(exc)
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 0

    def test_except_with_meaningful_body_passes(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    risky()
                except Exception:
                    cleanup()
                    notify_user()
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 0

    def test_opt_out_comment_above_try_exempts_handler(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                # silent-except: progress callback failures must not abort caller
                try:
                    progress("step")
                except Exception:
                    pass
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 0

    def test_opt_out_comment_above_except_exempts_handler(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    progress("step")
                # silent-except: progress callback failures must not abort caller
                except Exception:
                    pass
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 0

    def test_opt_out_without_reason_does_not_exempt(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                # silent-except:
                try:
                    risky()
                except Exception:
                    pass
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 1

    def test_unrelated_comment_does_not_exempt(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                # this is fine
                try:
                    risky()
                except Exception:
                    pass
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 1

    def test_multiple_handlers_each_evaluated_independently(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    risky()
                except ValueError:
                    raise
                except TypeError:
                    pass
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, default_config(), "test.py")
        assert len(results) == 1
        assert results[0].line >= 6

    def test_strict_config_also_enables_check(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                try:
                    risky()
                except Exception:
                    pass
        """)
        tree = ast.parse(source)
        results = check_silent_exception_handling(source, tree, strict_config(), "test.py")
        assert len(results) == 1


class TestIsTestModule:
    """Tests for the _is_test_module helper used by several checks."""

    def test_path_in_tests_dir(self) -> None:
        assert _is_test_module("tests/unit/foo.py") is True

    def test_test_prefix_basename(self) -> None:
        assert _is_test_module("test_foo.py") is True

    def test_src_module_is_not_test(self) -> None:
        assert _is_test_module("src/serenecode/core/foo.py") is False

    def test_empty_path(self) -> None:
        assert _is_test_module("") is False

    def test_windows_separators(self) -> None:
        assert _is_test_module("tests\\unit\\foo.py") is True


class TestCheckMutableDefaultArguments:
    """Tests for mutable-default-argument detection."""

    def test_minimal_config_skips(self) -> None:
        source = textwrap.dedent("""\
            def f(x=[]):
                return x
        """)
        tree = ast.parse(source)
        results = check_mutable_default_arguments(source, tree, minimal_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_list_literal_default_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f(x=[]):
                return x
        """)
        tree = ast.parse(source)
        results = check_mutable_default_arguments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1
        assert "mutable" in results[0].details[0].message.lower()

    def test_dict_literal_default_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f(x={}):
                return x
        """)
        tree = ast.parse(source)
        results = check_mutable_default_arguments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_set_literal_default_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f(x={1, 2}):
                return x
        """)
        tree = ast.parse(source)
        results = check_mutable_default_arguments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_list_constructor_call_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f(x=list()):
                return x
        """)
        tree = ast.parse(source)
        results = check_mutable_default_arguments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_none_default_passes(self) -> None:
        source = textwrap.dedent("""\
            def f(x=None):
                return x
        """)
        tree = ast.parse(source)
        results = check_mutable_default_arguments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_tuple_default_passes(self) -> None:
        source = textwrap.dedent("""\
            def f(x=(1, 2)):
                return x
        """)
        tree = ast.parse(source)
        results = check_mutable_default_arguments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_keyword_only_mutable_default_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f(*, x=[]):
                return x
        """)
        tree = ast.parse(source)
        results = check_mutable_default_arguments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_opt_out_comment_exempts(self) -> None:
        source = textwrap.dedent("""\
            # allow-mutable-default: sentinel pattern documented in design notes
            def f(x=[]):
                return x
        """)
        tree = ast.parse(source)
        results = check_mutable_default_arguments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_exempt_module_skipped(self) -> None:
        source = textwrap.dedent("""\
            def f(x=[]):
                return x
        """)
        tree = ast.parse(source)
        results = check_mutable_default_arguments(source, tree, default_config(), "cli.py", "cli.py")
        assert len(results) == 0


class TestCheckPrintInCore:
    """Tests for print()-in-core-module detection."""

    def test_minimal_config_skips(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                print('hi')
        """)
        tree = ast.parse(source)
        results = check_print_in_core(source, tree, minimal_config(), "core/foo.py", "core/foo.py")
        assert len(results) == 0

    def test_print_in_core_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                print('hi')
        """)
        tree = ast.parse(source)
        results = check_print_in_core(source, tree, default_config(), "core/foo.py", "core/foo.py")
        assert len(results) == 1
        assert "print" in results[0].details[0].message.lower()

    def test_print_in_adapter_skipped(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                print('hi')
        """)
        tree = ast.parse(source)
        results = check_print_in_core(source, tree, default_config(), "adapters/foo.py", "adapters/foo.py")
        assert len(results) == 0

    def test_print_in_non_core_module_skipped(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                print('hi')
        """)
        tree = ast.parse(source)
        results = check_print_in_core(source, tree, default_config(), "scripts/foo.py", "scripts/foo.py")
        assert len(results) == 0

    def test_opt_out_comment_exempts(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                # allow-print: required for stdout-only CLI helper inside core
                print('hi')
        """)
        tree = ast.parse(source)
        results = check_print_in_core(source, tree, default_config(), "core/foo.py", "core/foo.py")
        assert len(results) == 0

    def test_multiple_prints_each_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                print('one')
                print('two')
        """)
        tree = ast.parse(source)
        results = check_print_in_core(source, tree, default_config(), "core/foo.py", "core/foo.py")
        assert len(results) == 2


class TestCheckDangerousCalls:
    """Tests for dangerous-call detection."""

    def test_minimal_config_skips(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                eval('1+1')
        """)
        tree = ast.parse(source)
        results = check_dangerous_calls(source, tree, minimal_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_eval_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                eval('1+1')
        """)
        tree = ast.parse(source)
        results = check_dangerous_calls(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1
        assert "eval" in results[0].details[0].message.lower()

    def test_exec_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                exec('x = 1')
        """)
        tree = ast.parse(source)
        results = check_dangerous_calls(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_pickle_loads_flagged(self) -> None:
        source = textwrap.dedent("""\
            import pickle
            def f(data):
                return pickle.loads(data)
        """)
        tree = ast.parse(source)
        results = check_dangerous_calls(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1
        assert "pickle" in results[0].details[0].message.lower()

    def test_os_system_flagged(self) -> None:
        source = textwrap.dedent("""\
            import os
            def f() -> None:
                os.system('ls')
        """)
        tree = ast.parse(source)
        results = check_dangerous_calls(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_subprocess_run_with_shell_true_flagged(self) -> None:
        source = textwrap.dedent("""\
            import subprocess
            def f() -> None:
                subprocess.run('ls', shell=True)
        """)
        tree = ast.parse(source)
        results = check_dangerous_calls(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1
        assert "shell=True" in results[0].details[0].message

    def test_subprocess_run_without_shell_true_passes(self) -> None:
        source = textwrap.dedent("""\
            import subprocess
            def f() -> None:
                subprocess.run(['ls', '-la'])
        """)
        tree = ast.parse(source)
        results = check_dangerous_calls(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_opt_out_comment_exempts(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                # allow-dangerous: ast.literal_eval insufficient for legacy formula syntax
                eval('1+1')
        """)
        tree = ast.parse(source)
        results = check_dangerous_calls(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_exempt_module_skipped(self) -> None:
        source = textwrap.dedent("""\
            def f() -> None:
                eval('1+1')
        """)
        tree = ast.parse(source)
        results = check_dangerous_calls(source, tree, default_config(), "cli.py", "cli.py")
        assert len(results) == 0


class TestCheckBareAssertsOutsideTests:
    """Tests for bare assert detection in non-test source."""

    def test_minimal_config_skips(self) -> None:
        source = textwrap.dedent("""\
            def f(x: int) -> int:
                assert x > 0
                return x
        """)
        tree = ast.parse(source)
        results = check_bare_asserts_outside_tests(source, tree, minimal_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_assert_in_src_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f(x: int) -> int:
                assert x > 0
                return x
        """)
        tree = ast.parse(source)
        results = check_bare_asserts_outside_tests(source, tree, default_config(), "src/foo.py", "src/foo.py")
        assert len(results) == 1
        assert "assert" in results[0].details[0].message.lower()

    def test_assert_in_test_module_skipped(self) -> None:
        source = textwrap.dedent("""\
            def test_foo() -> None:
                assert 1 == 1
        """)
        tree = ast.parse(source)
        results = check_bare_asserts_outside_tests(source, tree, default_config(), "tests/test_foo.py", "tests/test_foo.py")
        assert len(results) == 0

    def test_assert_in_test_prefix_module_skipped(self) -> None:
        source = textwrap.dedent("""\
            def test_foo() -> None:
                assert 1 == 1
        """)
        tree = ast.parse(source)
        results = check_bare_asserts_outside_tests(source, tree, default_config(), "test_helpers.py", "test_helpers.py")
        assert len(results) == 0

    def test_opt_out_comment_exempts(self) -> None:
        source = textwrap.dedent("""\
            def f(x: int) -> int:
                # allow-assert: type narrowing for mypy; runtime guarded by precondition
                assert x > 0
                return x
        """)
        tree = ast.parse(source)
        results = check_bare_asserts_outside_tests(source, tree, default_config(), "src/foo.py", "src/foo.py")
        assert len(results) == 0

    def test_exempt_module_skipped(self) -> None:
        source = textwrap.dedent("""\
            def f(x: int) -> int:
                assert x > 0
                return x
        """)
        tree = ast.parse(source)
        results = check_bare_asserts_outside_tests(source, tree, default_config(), "cli.py", "cli.py")
        assert len(results) == 0


class TestCheckStubResidue:
    """Tests for stub-residue detection."""

    def test_minimal_config_skips(self) -> None:
        source = textwrap.dedent("""\
            def f() -> int:
                pass
        """)
        tree = ast.parse(source)
        results = check_stub_residue(source, tree, minimal_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_pass_only_body_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f() -> int:
                pass
        """)
        tree = ast.parse(source)
        results = check_stub_residue(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_ellipsis_only_body_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f() -> int:
                ...
        """)
        tree = ast.parse(source)
        results = check_stub_residue(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_raise_notimplementederror_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f() -> int:
                raise NotImplementedError()
        """)
        tree = ast.parse(source)
        results = check_stub_residue(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_docstring_plus_pass_still_flagged(self) -> None:
        source = textwrap.dedent("""\
            def f() -> int:
                \"\"\"Doc.\"\"\"
                pass
        """)
        tree = ast.parse(source)
        results = check_stub_residue(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_protocol_method_skipped(self) -> None:
        source = textwrap.dedent("""\
            from typing import Protocol
            class P(Protocol):
                def f(self) -> int: ...
        """)
        tree = ast.parse(source)
        results = check_stub_residue(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_abstractmethod_skipped(self) -> None:
        source = textwrap.dedent("""\
            from abc import abstractmethod
            class A:
                @abstractmethod
                def f(self) -> int:
                    pass
        """)
        tree = ast.parse(source)
        results = check_stub_residue(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_init_with_only_assignments_skipped(self) -> None:
        source = textwrap.dedent("""\
            class C:
                def __init__(self) -> None:
                    self.x = 1
                    self.y = 2
        """)
        tree = ast.parse(source)
        results = check_stub_residue(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_real_implementation_passes(self) -> None:
        source = textwrap.dedent("""\
            def f(x: int) -> int:
                return x + 1
        """)
        tree = ast.parse(source)
        results = check_stub_residue(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_opt_out_comment_exempts(self) -> None:
        source = textwrap.dedent("""\
            # allow-stub: placeholder for upcoming REQ-042 implementation
            def f() -> int:
                pass
        """)
        tree = ast.parse(source)
        results = check_stub_residue(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0


class TestCheckTodoComments:
    """Tests for TODO/FIXME/XXX/HACK comment detection."""

    def test_minimal_config_skips(self) -> None:
        source = textwrap.dedent("""\
            # TODO: implement this
            def f() -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_todo_comments(source, tree, minimal_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_todo_comment_flagged(self) -> None:
        source = textwrap.dedent("""\
            # TODO: implement this
            def f() -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_todo_comments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1
        assert "TODO" in results[0].details[0].message

    def test_fixme_flagged(self) -> None:
        source = textwrap.dedent("""\
            # FIXME: broken edge case
            def f() -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_todo_comments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_xxx_flagged(self) -> None:
        source = textwrap.dedent("""\
            # XXX: this is wrong
            def f() -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_todo_comments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_hack_flagged(self) -> None:
        source = textwrap.dedent("""\
            # HACK: temporary workaround
            def f() -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_todo_comments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 1

    def test_test_module_skipped(self) -> None:
        source = textwrap.dedent("""\
            # TODO: cover this branch
            def test_foo() -> None:
                assert True
        """)
        tree = ast.parse(source)
        results = check_todo_comments(source, tree, default_config(), "tests/test_foo.py", "tests/test_foo.py")
        assert len(results) == 0

    def test_opt_out_comment_exempts(self) -> None:
        source = textwrap.dedent("""\
            # allow-todo: tracked in INFRA-12
            # TODO: refactor when migration completes
            def f() -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_todo_comments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_clean_code_passes(self) -> None:
        source = textwrap.dedent("""\
            # this is a normal comment
            def f() -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_todo_comments(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_exempt_module_skipped(self) -> None:
        source = textwrap.dedent("""\
            # TODO: refactor
            def f() -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_todo_comments(source, tree, default_config(), "cli.py", "cli.py")
        assert len(results) == 0


class TestCheckNoAssertionsInTests:
    """Tests for tests-without-assertions detection."""

    def test_minimal_config_skips(self) -> None:
        source = textwrap.dedent("""\
            def test_nothing() -> None:
                x = 1
        """)
        tree = ast.parse(source)
        results = check_no_assertions_in_tests(source, tree, minimal_config(), "tests/test_foo.py", "tests/test_foo.py")
        assert len(results) == 0

    def test_test_with_no_assertion_flagged(self) -> None:
        source = textwrap.dedent("""\
            def test_nothing() -> None:
                x = 1
        """)
        tree = ast.parse(source)
        results = check_no_assertions_in_tests(source, tree, default_config(), "tests/test_foo.py", "tests/test_foo.py")
        assert len(results) == 1

    def test_test_with_assert_passes(self) -> None:
        source = textwrap.dedent("""\
            def test_foo() -> None:
                assert 1 == 1
        """)
        tree = ast.parse(source)
        results = check_no_assertions_in_tests(source, tree, default_config(), "tests/test_foo.py", "tests/test_foo.py")
        assert len(results) == 0

    def test_test_with_pytest_raises_passes(self) -> None:
        source = textwrap.dedent("""\
            import pytest
            def test_foo() -> None:
                with pytest.raises(ValueError):
                    raise ValueError("x")
        """)
        tree = ast.parse(source)
        results = check_no_assertions_in_tests(source, tree, default_config(), "tests/test_foo.py", "tests/test_foo.py")
        assert len(results) == 0

    def test_test_with_pytest_fail_passes(self) -> None:
        source = textwrap.dedent("""\
            import pytest
            def test_foo() -> None:
                if False:
                    pytest.fail("never")
        """)
        tree = ast.parse(source)
        results = check_no_assertions_in_tests(source, tree, default_config(), "tests/test_foo.py", "tests/test_foo.py")
        assert len(results) == 0

    def test_unittest_assertEqual_passes(self) -> None:
        source = textwrap.dedent("""\
            import unittest
            class T(unittest.TestCase):
                def test_foo(self) -> None:
                    self.assertEqual(1, 1)
        """)
        tree = ast.parse(source)
        results = check_no_assertions_in_tests(source, tree, default_config(), "tests/test_foo.py", "tests/test_foo.py")
        assert len(results) == 0

    def test_non_test_function_skipped(self) -> None:
        source = textwrap.dedent("""\
            def helper() -> None:
                x = 1
        """)
        tree = ast.parse(source)
        results = check_no_assertions_in_tests(source, tree, default_config(), "tests/test_foo.py", "tests/test_foo.py")
        assert len(results) == 0

    def test_non_test_module_skipped(self) -> None:
        source = textwrap.dedent("""\
            def test_nothing() -> None:
                x = 1
        """)
        tree = ast.parse(source)
        results = check_no_assertions_in_tests(source, tree, default_config(), "src/foo.py", "src/foo.py")
        assert len(results) == 0

    def test_opt_out_comment_exempts(self) -> None:
        source = textwrap.dedent("""\
            # allow-no-assert: smoke test only checks that import succeeds
            def test_imports_clean() -> None:
                import os
        """)
        tree = ast.parse(source)
        results = check_no_assertions_in_tests(source, tree, default_config(), "tests/test_foo.py", "tests/test_foo.py")
        assert len(results) == 0


class TestCheckTautologicalIsinstancePostcondition:
    """Tests for the extended isinstance-tautology check."""

    def test_minimal_config_skips(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            @icontract.ensure(lambda result: isinstance(result, int), "is int")
            def f() -> int:
                return 1
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_tautological_isinstance_postcondition(
            source, tree, minimal_config(), aliases, "foo.py", "foo.py",
        )
        assert len(results) == 0

    def test_isinstance_matching_return_type_flagged(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            @icontract.ensure(lambda result: isinstance(result, int), "is int")
            def f() -> int:
                return 1
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_tautological_isinstance_postcondition(
            source, tree, default_config(), aliases, "foo.py", "foo.py",
        )
        assert len(results) == 1
        assert "tautological" in results[0].details[0].message.lower()

    def test_meaningful_postcondition_passes(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            @icontract.ensure(lambda result: result > 0, "result must be positive")
            def f() -> int:
                return 1
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_tautological_isinstance_postcondition(
            source, tree, default_config(), aliases, "foo.py", "foo.py",
        )
        assert len(results) == 0

    def test_isinstance_against_different_type_passes(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            @icontract.ensure(lambda result: isinstance(result, int), "narrows union to int")
            def f() -> int | str:
                return 1
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_tautological_isinstance_postcondition(
            source, tree, default_config(), aliases, "foo.py", "foo.py",
        )
        # int does not equal "int | str", so not flagged
        assert len(results) == 0

    def test_no_return_annotation_skipped(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            @icontract.ensure(lambda result: isinstance(result, int), "is int")
            def f():
                return 1
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_tautological_isinstance_postcondition(
            source, tree, default_config(), aliases, "foo.py", "foo.py",
        )
        assert len(results) == 0

    def test_opt_out_comment_exempts(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            # allow-isinstance-tautology: defensive check kept for runtime safety
            @icontract.ensure(lambda result: isinstance(result, int), "is int")
            def f() -> int:
                return 1
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_tautological_isinstance_postcondition(
            source, tree, default_config(), aliases, "foo.py", "foo.py",
        )
        assert len(results) == 0

    def test_private_helper_skipped(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            @icontract.ensure(lambda result: isinstance(result, bool), "is bool")
            def _internal_helper() -> bool:
                return True
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_tautological_isinstance_postcondition(
            source, tree, default_config(), aliases, "foo.py", "foo.py",
        )
        assert len(results) == 0

    def test_is_predicate_skipped(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            @icontract.ensure(lambda result: isinstance(result, bool), "is bool")
            def is_valid(value: int) -> bool:
                return value > 0
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_tautological_isinstance_postcondition(
            source, tree, default_config(), aliases, "foo.py", "foo.py",
        )
        assert len(results) == 0

    def test_has_predicate_skipped(self) -> None:
        source = textwrap.dedent("""\
            import icontract
            @icontract.ensure(lambda result: isinstance(result, bool), "is bool")
            def has_property(obj: object) -> bool:
                return True
        """)
        tree = ast.parse(source)
        aliases = _aliases_standard()
        results = check_tautological_isinstance_postcondition(
            source, tree, default_config(), aliases, "foo.py", "foo.py",
        )
        assert len(results) == 0


class TestCheckUnusedParameters:
    """Tests for unused-parameter detection (strict-only by default)."""

    def test_default_config_skips(self) -> None:
        source = textwrap.dedent("""\
            def f(used: int, unused: int) -> int:
                return used
        """)
        tree = ast.parse(source)
        results = check_unused_parameters(source, tree, default_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_strict_config_flags_unused(self) -> None:
        source = textwrap.dedent("""\
            def f(used: int, unused: int) -> int:
                return used
        """)
        tree = ast.parse(source)
        results = check_unused_parameters(source, tree, strict_config(), "foo.py", "foo.py")
        assert len(results) == 1
        assert "unused" in results[0].details[0].message.lower()

    def test_underscore_prefix_skipped(self) -> None:
        source = textwrap.dedent("""\
            def f(used: int, _ignored: int) -> int:
                return used
        """)
        tree = ast.parse(source)
        results = check_unused_parameters(source, tree, strict_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_self_skipped(self) -> None:
        source = textwrap.dedent("""\
            class C:
                def f(self) -> int:
                    return 1
        """)
        tree = ast.parse(source)
        # C has no non-object base, so the conservative skip does not apply
        results = check_unused_parameters(source, tree, strict_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_subclass_method_skipped(self) -> None:
        source = textwrap.dedent("""\
            class Base:
                pass
            class Derived(Base):
                def f(self, x: int) -> int:
                    return 1
        """)
        tree = ast.parse(source)
        results = check_unused_parameters(source, tree, strict_config(), "foo.py", "foo.py")
        # Conservative: Derived has a non-object base, methods skipped
        assert len(results) == 0

    def test_override_decorator_skipped(self) -> None:
        source = textwrap.dedent("""\
            from typing import override
            class C:
                @override
                def f(self, x: int) -> int:
                    return 1
        """)
        tree = ast.parse(source)
        results = check_unused_parameters(source, tree, strict_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_args_kwargs_skipped(self) -> None:
        source = textwrap.dedent("""\
            def f(*args, **kwargs) -> int:
                return 1
        """)
        tree = ast.parse(source)
        results = check_unused_parameters(source, tree, strict_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_all_used_passes(self) -> None:
        source = textwrap.dedent("""\
            def f(a: int, b: int) -> int:
                return a + b
        """)
        tree = ast.parse(source)
        results = check_unused_parameters(source, tree, strict_config(), "foo.py", "foo.py")
        assert len(results) == 0

    def test_opt_out_comment_exempts(self) -> None:
        source = textwrap.dedent("""\
            # allow-unused-param: shape required by the FileReader port
            def f(used: int, unused: int) -> int:
                return used
        """)
        tree = ast.parse(source)
        results = check_unused_parameters(source, tree, strict_config(), "foo.py", "foo.py")
        assert len(results) == 0


class TestCheckNamingConventions:
    """Tests for naming convention checking."""

    def test_pascal_case_class_passes(self) -> None:
        source = textwrap.dedent("""\
            class MyClass:
                pass
        """)
        tree = ast.parse(source)
        results = check_naming_conventions(tree, default_config(), "test.py")
        assert len(results) == 0

    def test_non_pascal_case_class_fails(self) -> None:
        source = textwrap.dedent("""\
            class my_class:
                pass
        """)
        tree = ast.parse(source)
        results = check_naming_conventions(tree, default_config(), "test.py")
        assert len(results) == 1
        assert "PascalCase" in results[0].details[0].message

    def test_snake_case_function_passes(self) -> None:
        source = textwrap.dedent("""\
            def my_function() -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_naming_conventions(tree, default_config(), "test.py")
        assert len(results) == 0

    def test_camel_case_function_fails(self) -> None:
        source = textwrap.dedent("""\
            def myFunction() -> None:
                pass
        """)
        tree = ast.parse(source)
        results = check_naming_conventions(tree, default_config(), "test.py")
        assert len(results) == 1
        assert "snake_case" in results[0].details[0].message


class TestCheckStructuralOrchestrator:
    """Tests for the main check_structural orchestrator."""

    def test_valid_file_passes(self) -> None:
        source = textwrap.dedent('''\
            """Module docstring."""

            import icontract

            @icontract.require(lambda x: x >= 0, "x must be non-negative")
            @icontract.ensure(lambda result: result >= 0, "result non-negative")
            def square(x: float) -> float:
                """Square a number."""
                return x * x
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        assert result.passed is True

    def test_missing_contracts_fails(self) -> None:
        source = textwrap.dedent('''\
            """Module docstring."""

            def add(x: int, y: int) -> int:
                """Add two numbers."""
                return x + y
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        assert result.passed is False

    def test_syntax_error_handled(self) -> None:
        source = "def broken(:\n  pass"
        result = check_structural(source, default_config(), file_path="test.py")
        assert result.passed is False
        assert len(result.results) == 1
        assert "syntax" in result.results[0].details[0].message.lower()

    def test_exempt_module_is_reported_as_exempt(self) -> None:
        """Exempt modules produce EXEMPT results but do not count as passed."""
        source = "x = 1"  # no docstring, no contracts, etc.
        result = check_structural(
            source,
            default_config(),
            module_path="adapters/local_fs.py",
            file_path="test.py",
        )
        # All-exempt results cannot claim the level was achieved.
        assert result.passed is False
        assert result.level_achieved == 0
        # Exempt modules produce a visible EXEMPT result instead of being invisible.
        assert len(result.results) == 1
        assert result.results[0].status == CheckStatus.EXEMPT
        assert result.summary.exempt_count == 1

    def test_exempt_module_still_reports_syntax_errors(self) -> None:
        source = "def broken(:\n  pass"
        result = check_structural(
            source,
            default_config(),
            module_path="adapters/local_fs.py",
            file_path="test.py",
        )

        assert result.passed is False
        assert len(result.results) == 1
        assert "syntax" in result.results[0].details[0].message.lower()

    def test_empty_module_with_docstring_passes(self) -> None:
        source = '"""Empty module."""\n'
        result = check_structural(source, default_config(), file_path="test.py")
        assert result.passed is True

    def test_io_in_core_detected(self) -> None:
        source = textwrap.dedent('''\
            """Module doc."""

            import os
        ''')
        result = check_structural(
            source,
            default_config(),
            module_path="core/engine.py",
            file_path="test.py",
        )
        assert result.passed is False
        import_failures = [
            r for r in result.results
            if any("forbidden import" in d.message.lower() for d in r.details)
        ]
        assert len(import_failures) > 0

    def test_check_result_has_summary(self) -> None:
        source = textwrap.dedent('''\
            """Module docstring."""

            import icontract

            @icontract.require(lambda x: x >= 0, "x non-neg")
            @icontract.ensure(lambda result: result >= 0, "result non-neg")
            def square(x: float) -> float:
                """Square a number."""
                return x * x

            def broken(x, y):
                return x + y
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        assert result.summary.total_functions > 0
        assert result.summary.duration_seconds >= 0

    def test_async_function_checked(self) -> None:
        source = textwrap.dedent('''\
            """Module doc."""

            async def fetch(url: str) -> str:
                """Fetch data."""
                return f"data from {url}"
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        contract_failures = [
            r for r in result.results
            if r.function == "fetch" and any("require" in d.message.lower() for d in r.details)
        ]
        assert len(contract_failures) == 1


class TestTautologicalContracts:
    """Tests for detecting tautological contracts (always-True conditions)."""

    def test_tautological_ensure_detected(self) -> None:
        source = textwrap.dedent('''\
            import icontract

            @icontract.require(lambda x: x > 0, "x must be positive")
            @icontract.ensure(lambda result: True, "always passes")
            def compute(x: int) -> int:
                """Compute."""
                return x
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        taut_failures = [
            r for r in result.results
            if r.function == "compute"
            and any("tautological" in d.message.lower() for d in r.details)
        ]
        assert len(taut_failures) == 1

    def test_tautological_require_detected(self) -> None:
        source = textwrap.dedent('''\
            import icontract

            @icontract.require(lambda x: True, "accepts everything")
            @icontract.ensure(lambda result: result > 0, "positive result")
            def compute(x: int) -> int:
                """Compute."""
                return x
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        taut_failures = [
            r for r in result.results
            if r.function == "compute"
            and any("tautological" in d.message.lower() for d in r.details)
        ]
        assert len(taut_failures) == 1

    def test_meaningful_contract_not_flagged(self) -> None:
        source = textwrap.dedent('''\
            import icontract

            @icontract.require(lambda x: x > 0, "x must be positive")
            @icontract.ensure(lambda result: result >= 0, "non-negative result")
            def compute(x: int) -> int:
                """Compute."""
                return x
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        taut_failures = [
            r for r in result.results
            if any("tautological" in d.message.lower() for d in r.details)
        ]
        assert len(taut_failures) == 0

    def test_tautological_invariant_detected(self) -> None:
        source = textwrap.dedent('''\
            import icontract

            @icontract.invariant(lambda self: True, "always true")
            class Holder:
                """Holds data."""
                def __init__(self) -> None:
                    self.value: int = 0
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        taut_failures = [
            r for r in result.results
            if r.function == "Holder"
            and any("tautological" in d.message.lower() for d in r.details)
        ]
        assert len(taut_failures) == 1

    def test_is_tautological_lambda_true(self) -> None:
        node = ast.parse("lambda x: True", mode="eval").body
        assert _is_tautological_lambda(node) is True

    def test_is_tautological_lambda_false(self) -> None:
        node = ast.parse("lambda x: x > 0", mode="eval").body
        assert _is_tautological_lambda(node) is False

    def test_is_tautological_lambda_non_lambda(self) -> None:
        node = ast.parse("42", mode="eval").body
        assert _is_tautological_lambda(node) is False


class TestDescriptionLiteralValidation:
    """Tests that contract descriptions must be string literals."""

    def test_variable_description_detected(self) -> None:
        source = textwrap.dedent('''\
            import icontract

            DESC = "some description"

            @icontract.require(lambda x: x > 0, DESC)
            @icontract.ensure(lambda result: result > 0, "positive result")
            def compute(x: int) -> int:
                """Compute."""
                return x
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        literal_failures = [
            r for r in result.results
            if r.function == "compute"
            and any("not a string literal" in d.message.lower() for d in r.details)
        ]
        assert len(literal_failures) == 1

    def test_string_literal_description_passes(self) -> None:
        source = textwrap.dedent('''\
            import icontract

            @icontract.require(lambda x: x > 0, "x must be positive")
            @icontract.ensure(lambda result: result > 0, "positive result")
            def compute(x: int) -> int:
                """Compute."""
                return x
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        literal_failures = [
            r for r in result.results
            if any("not a string literal" in d.message.lower() for d in r.details)
        ]
        assert len(literal_failures) == 0
