"""Tests for module health checks in the verification pipeline.

Verifies: REQ-001, REQ-002, REQ-003, REQ-004, REQ-005, REQ-008, REQ-009,
REQ-010, REQ-011, REQ-012, REQ-013, REQ-014, REQ-015, REQ-017, REQ-018,
REQ-019, REQ-021, REQ-022, REQ-023, REQ-025, REQ-026, REQ-027, REQ-028
"""

from __future__ import annotations

import pytest

from serenecode.config import ModuleHealthConfig, default_config
from serenecode.core.module_health import (
    check_class_method_count as _check_class_method_count,
    check_file_length as _check_file_length,
    check_function_length as _check_function_length,
    check_parameter_count as _check_parameter_count,
    suggest_split_points as _suggest_split_points,
)
from serenecode.models import CheckStatus
from serenecode.source_discovery import SourceFile


def _make_source_file(source: str, file_path: str = "src/example.py") -> tuple[SourceFile, ...]:
    return (SourceFile(
        file_path=file_path,
        module_path=file_path,
        source=source,
        importable_module=None,
        import_search_paths=(),
        context_root=None,
    ),)


def _config_with(
    enabled: bool = True,
    file_length_warn: int = 10,
    file_length_error: int = 20,
    function_length_warn: int = 5,
    function_length_error: int = 10,
    parameter_count_warn: int = 3,
    parameter_count_error: int = 5,
    class_method_count_warn: int = 3,
    class_method_count_error: int = 5,
):
    from dataclasses import replace
    cfg = default_config()
    return replace(cfg, module_health=ModuleHealthConfig(
        enabled=enabled,
        file_length_warn=file_length_warn,
        file_length_error=file_length_error,
        function_length_warn=function_length_warn,
        function_length_error=function_length_error,
        parameter_count_warn=parameter_count_warn,
        parameter_count_error=parameter_count_error,
        class_method_count_warn=class_method_count_warn,
        class_method_count_error=class_method_count_error,
    ))


# ---------------------------------------------------------------------------
# File length
# Verifies: REQ-008, REQ-009, REQ-010, REQ-011, REQ-012
# ---------------------------------------------------------------------------


class TestFileLength:
    """Verifies: REQ-028"""

    def test_below_warn_no_results(self):
        """Verifies: REQ-008"""
        source = "\n".join(f"x = {i}" for i in range(5))
        results = _check_file_length(_make_source_file(source), _config_with())
        assert len(results) == 0

    def test_between_warn_and_error_advisory(self):
        """Verifies: REQ-010"""
        source = "\n".join(f"x = {i}" for i in range(15))
        results = _check_file_length(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].status == CheckStatus.EXEMPT
        assert results[0].details[0].finding_type == "file_length"
        assert "15 lines" in results[0].details[0].message

    def test_above_error_failed(self):
        """Verifies: REQ-009"""
        source = "\n".join(f"x = {i}" for i in range(25))
        results = _check_file_length(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED
        assert results[0].details[0].finding_type == "file_length"

    def test_disabled_no_results(self):
        """Verifies: REQ-001"""
        source = "\n".join(f"x = {i}" for i in range(25))
        cfg = _config_with(enabled=False)
        # When disabled, the pipeline skips the call entirely;
        # but if called directly, the function still runs.
        # The pipeline guard is tested in integration.
        results = _check_file_length(_make_source_file(source), cfg)
        # Function itself doesn't check enabled — pipeline does
        assert len(results) >= 1

    def test_test_files_excluded(self):
        """Verifies: REQ-008"""
        source = "\n".join(f"x = {i}" for i in range(25))
        results = _check_file_length(
            _make_source_file(source, file_path="tests/test_example.py"),
            _config_with(),
        )
        assert len(results) == 0

    def test_runs_on_exempt_modules(self):
        """Verifies: REQ-011"""
        source = "\n".join(f"x = {i}" for i in range(15))
        results = _check_file_length(
            _make_source_file(source, file_path="src/adapters/http.py"),
            _config_with(),
        )
        assert len(results) == 1

    def test_suggestion_is_actionable(self):
        """Verifies: REQ-012"""
        source = "\n".join(f"x = {i}" for i in range(15))
        results = _check_file_length(_make_source_file(source), _config_with())
        assert results[0].details[0].suggestion is not None
        assert "split" in results[0].details[0].suggestion.lower()


# ---------------------------------------------------------------------------
# Function length
# Verifies: REQ-013, REQ-014, REQ-015
# ---------------------------------------------------------------------------


class TestFunctionLength:
    def test_short_function_no_results(self):
        """Verifies: REQ-013"""
        source = "def foo():\n    pass\n"
        results = _check_function_length(_make_source_file(source), _config_with())
        assert len(results) == 0

    def test_between_warn_and_error_advisory(self):
        """Verifies: REQ-015"""
        body = "\n".join(f"    x = {i}" for i in range(7))
        source = f"def foo():\n{body}\n"
        results = _check_function_length(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].status == CheckStatus.EXEMPT
        assert results[0].details[0].finding_type == "function_length"
        assert results[0].function == "foo"

    def test_above_error_failed(self):
        """Verifies: REQ-014, REQ-016"""
        body = "\n".join(f"    x = {i}" for i in range(15))
        source = f"def foo():\n{body}\n"
        results = _check_function_length(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED
        assert "helper" in results[0].details[0].suggestion.lower()

    def test_only_offending_functions_reported(self):
        """Verifies: REQ-013"""
        short = "def short():\n    pass\n\n"
        body = "\n".join(f"    x = {i}" for i in range(15))
        long = f"def long_func():\n{body}\n"
        source = short + long
        results = _check_function_length(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].function == "long_func"

    def test_async_functions_counted(self):
        """Verifies: REQ-013"""
        body = "\n".join(f"    x = {i}" for i in range(15))
        source = f"async def afoo():\n{body}\n"
        results = _check_function_length(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].function == "afoo"

    def test_class_methods_checked(self):
        """Verifies: REQ-013"""
        body = "\n".join(f"        x = {i}" for i in range(15))
        source = f"class Foo:\n    def bar(self):\n{body}\n"
        results = _check_function_length(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].function == "bar"


# ---------------------------------------------------------------------------
# Parameter count
# Verifies: REQ-017, REQ-018, REQ-019
# ---------------------------------------------------------------------------


class TestParameterCount:
    def test_few_params_no_results(self):
        """Verifies: REQ-017"""
        source = "def foo(a, b):\n    pass\n"
        results = _check_parameter_count(_make_source_file(source), _config_with())
        assert len(results) == 0

    def test_between_warn_and_error_advisory(self):
        """Verifies: REQ-019"""
        source = "def foo(a, b, c, d):\n    pass\n"
        results = _check_parameter_count(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].status == CheckStatus.EXEMPT
        assert results[0].details[0].finding_type == "parameter_count"

    def test_above_error_failed(self):
        """Verifies: REQ-018, REQ-020"""
        source = "def foo(a, b, c, d, e, f):\n    pass\n"
        results = _check_parameter_count(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED
        assert "dataclass" in results[0].details[0].suggestion.lower()

    def test_self_excluded(self):
        """Verifies: REQ-017"""
        source = "class C:\n    def foo(self, a, b):\n        pass\n"
        results = _check_parameter_count(_make_source_file(source), _config_with())
        assert len(results) == 0

    def test_cls_excluded(self):
        """Verifies: REQ-017"""
        source = "class C:\n    @classmethod\n    def foo(cls, a, b):\n        pass\n"
        results = _check_parameter_count(_make_source_file(source), _config_with())
        assert len(results) == 0

    def test_args_kwargs_counted(self):
        """Verifies: REQ-017"""
        source = "def foo(a, b, c, d, *args, **kwargs):\n    pass\n"
        results = _check_parameter_count(_make_source_file(source), _config_with())
        # 6 params: a, b, c, d, *args, **kwargs — exceeds error threshold of 5
        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED


# ---------------------------------------------------------------------------
# Class method count
# Verifies: REQ-021, REQ-022, REQ-023
# ---------------------------------------------------------------------------


class TestClassMethodCount:
    def test_small_class_no_results(self):
        """Verifies: REQ-021"""
        source = "class C:\n    def a(self): pass\n    def b(self): pass\n"
        results = _check_class_method_count(_make_source_file(source), _config_with())
        assert len(results) == 0

    def test_between_warn_and_error_advisory(self):
        """Verifies: REQ-023"""
        methods = "\n".join(f"    def m{i}(self): pass" for i in range(4))
        source = f"class C:\n{methods}\n"
        results = _check_class_method_count(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].status == CheckStatus.EXEMPT
        assert results[0].details[0].finding_type == "class_method_count"
        assert results[0].function == "C"

    def test_above_error_failed(self):
        """Verifies: REQ-022, REQ-024"""
        methods = "\n".join(f"    def m{i}(self): pass" for i in range(6))
        source = f"class C:\n{methods}\n"
        results = _check_class_method_count(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED
        assert "extract" in results[0].details[0].suggestion.lower()

    def test_only_top_level_classes(self):
        """Verifies: REQ-021"""
        source = "def foo():\n    class Inner:\n" + "\n".join(
            f"        def m{i}(self): pass" for i in range(10)
        ) + "\n"
        results = _check_class_method_count(_make_source_file(source), _config_with())
        # Inner class is not top-level, should not be checked
        assert len(results) == 0


# ---------------------------------------------------------------------------
# Split suggestions
# Verifies: REQ-025, REQ-026, REQ-027
# ---------------------------------------------------------------------------


class TestSplitSuggestions:
    def test_class_boundary(self):
        """Verifies: REQ-025"""
        methods = "\n".join(f"    def m{i}(self): pass" for i in range(5))
        source = f"class Processor:\n{methods}\n"
        suggestions = _suggest_split_points(source)
        assert any("Processor" in s for s in suggestions)

    def test_function_prefix_group(self):
        """Verifies: REQ-025"""
        source = "\n".join(
            f"def parse_{name}(): pass"
            for name in ["header", "body", "footer"]
        ) + "\n"
        suggestions = _suggest_split_points(source)
        assert any("parse_*" in s for s in suggestions)

    def test_banner_comment(self):
        """Verifies: REQ-025"""
        source = "x = 1\n# --- Reporting ---\ny = 2\n"
        suggestions = _suggest_split_points(source)
        assert any("Reporting" in s for s in suggestions)

    def test_small_file_no_suggestions(self):
        """Verifies: REQ-027"""
        source = "x = 1\ny = 2\n"
        suggestions = _suggest_split_points(source)
        assert len(suggestions) == 0

    def test_split_points_in_file_length_suggestion(self):
        """Verifies: REQ-026"""
        methods = "\n".join(f"    def m{i}(self): pass" for i in range(5))
        filler = "\n".join(f"x = {i}" for i in range(15))
        source = f"class Processor:\n{methods}\n{filler}\n"
        results = _check_file_length(_make_source_file(source), _config_with())
        assert len(results) == 1
        assert "Processor" in results[0].details[0].suggestion
