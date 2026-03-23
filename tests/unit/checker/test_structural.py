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
    check_class_invariants,
    check_contracts,
    check_docstrings,
    check_exception_types,
    check_imports,
    check_loop_invariants,
    check_naming_conventions,
    check_no_any_in_core,
    check_structural,
    check_type_annotations,
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
        results = check_loop_invariants(source, tree, default_config(), "test.py")
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
        results = check_loop_invariants(source, tree, default_config(), "test.py")
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
        results = check_loop_invariants(source, tree, default_config(), "test.py")
        assert len(results) >= 1
        variant_results = [r for r in results if "variant" in r.details[0].message.lower()]
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
            if r.function != "<loop>" and "variant" in r.details[0].message.lower()
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


class TestCheckExceptionTypes:
    """Tests for exception type checking."""

    def test_raising_domain_exception_passes(self) -> None:
        source = textwrap.dedent("""\
            class MyError(Exception):
                pass

            def func() -> None:
                raise MyError("something went wrong")
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_exception_types(tree, config, "core/engine.py", "test.py")
        assert len(results) == 0

    def test_raising_bare_exception_fails(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                raise Exception("bad")
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_exception_types(tree, config, "core/engine.py", "test.py")
        assert len(results) == 1

    def test_raising_value_error_fails(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                raise ValueError("bad")
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_exception_types(tree, config, "core/engine.py", "test.py")
        assert len(results) == 1

    def test_exception_in_adapter_passes(self) -> None:
        source = textwrap.dedent("""\
            def func() -> None:
                raise ValueError("bad")
        """)
        tree = ast.parse(source)
        config = default_config()
        results = check_exception_types(tree, config, "adapters/local_fs.py", "test.py")
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

    def test_exempt_module_passes(self) -> None:
        source = "x = 1"  # no docstring, no contracts, etc.
        result = check_structural(
            source,
            default_config(),
            module_path="adapters/local_fs.py",
            file_path="test.py",
        )
        assert result.passed is True
        assert len(result.results) == 0

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
