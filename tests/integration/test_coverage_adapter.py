"""Integration tests for the coverage analysis adapter."""

from __future__ import annotations

import pytest

from serenecode.adapters.coverage_adapter import (
    _build_import_map,
    _classify_reason,
    _describe_uncovered_block,
    _discover_functions,
    _find_dependencies_in_lines,
    _generate_suggestions,
    _generate_test_code,
    _get_call_name,
    _group_contiguous_lines,
    _is_external_dependency,
    _is_io_call,
    _map_coverage_to_functions,
    _FunctionCoverage,
    _FunctionNode,
)
from serenecode.ports.coverage_analyzer import MockDependency


class TestDiscoverFunctions:
    """Tests for AST function discovery."""

    def test_top_level_function(self) -> None:
        source = "def foo():\n    pass\n"
        funcs = _discover_functions(source)
        assert len(funcs) == 1
        assert funcs[0].name == "foo"
        assert funcs[0].is_method is False

    def test_class_method(self) -> None:
        source = "class MyClass:\n    def method(self):\n        pass\n"
        funcs = _discover_functions(source)
        assert len(funcs) == 1
        assert funcs[0].name == "method"
        assert funcs[0].is_method is True
        assert funcs[0].class_name == "MyClass"
        assert funcs[0].qualified_name == "MyClass.method"

    def test_nested_function(self) -> None:
        source = "def outer():\n    def inner():\n        pass\n    return inner\n"
        funcs = _discover_functions(source)
        assert len(funcs) == 2
        names = {f.qualified_name for f in funcs}
        assert "outer" in names
        assert "outer.inner" in names

    def test_async_function(self) -> None:
        source = "async def handler():\n    pass\n"
        funcs = _discover_functions(source)
        assert len(funcs) == 1
        assert funcs[0].name == "handler"

    def test_empty_source(self) -> None:
        funcs = _discover_functions("")
        assert funcs == []

    def test_no_functions(self) -> None:
        source = "x = 1\ny = 2\n"
        funcs = _discover_functions(source)
        assert funcs == []

    def test_syntax_error_returns_empty(self) -> None:
        funcs = _discover_functions("def broken(:\n")
        assert funcs == []

    def test_nested_class(self) -> None:
        source = "class Outer:\n    class Inner:\n        def method(self):\n            pass\n"
        funcs = _discover_functions(source)
        assert len(funcs) == 1
        assert funcs[0].qualified_name == "Outer.Inner.method"

    def test_multiple_functions(self) -> None:
        source = "def a():\n    pass\ndef b():\n    pass\ndef c():\n    pass\n"
        funcs = _discover_functions(source)
        assert len(funcs) == 3

    def test_line_ranges(self) -> None:
        source = "def foo():\n    x = 1\n    return x\n"
        funcs = _discover_functions(source)
        assert funcs[0].line_start == 1
        assert funcs[0].line_end == 3


class TestGroupContiguousLines:
    """Tests for line grouping."""

    def test_empty(self) -> None:
        assert _group_contiguous_lines([]) == []

    def test_single(self) -> None:
        assert _group_contiguous_lines([5]) == [[5]]

    def test_contiguous(self) -> None:
        assert _group_contiguous_lines([1, 2, 3]) == [[1, 2, 3]]

    def test_two_groups(self) -> None:
        assert _group_contiguous_lines([1, 2, 5, 6]) == [[1, 2], [5, 6]]

    def test_all_separate(self) -> None:
        assert _group_contiguous_lines([1, 5, 10]) == [[1], [5], [10]]


class TestBuildImportMap:
    """Tests for import mapping."""

    def test_simple_import(self) -> None:
        import ast
        tree = ast.parse("import os\n")
        result = _build_import_map(tree)
        assert result["os"] == "os"

    def test_from_import(self) -> None:
        import ast
        tree = ast.parse("from os.path import exists\n")
        result = _build_import_map(tree)
        assert result["exists"] == "os.path"

    def test_aliased_import(self) -> None:
        import ast
        tree = ast.parse("import numpy as np\n")
        result = _build_import_map(tree)
        assert result["np"] == "numpy"

    def test_nested_import_captured(self) -> None:
        import ast
        source = "def foo():\n    import json\n    return json.loads('{}')\n"
        tree = ast.parse(source)
        result = _build_import_map(tree)
        assert "json" in result

    def test_try_except_import_captured(self) -> None:
        import ast
        source = "try:\n    import rapidjson\nexcept ImportError:\n    import json as rapidjson\n"
        tree = ast.parse(source)
        result = _build_import_map(tree)
        assert "rapidjson" in result


class TestIsExternalDependency:
    """Tests for external dependency classification."""

    def test_os_is_external(self) -> None:
        assert _is_external_dependency("os") is True

    def test_requests_is_external(self) -> None:
        assert _is_external_dependency("requests") is True

    def test_internal_module_not_external(self) -> None:
        assert _is_external_dependency("myproject.utils") is False

    def test_stdlib_non_io_not_external(self) -> None:
        assert _is_external_dependency("collections") is False


class TestIsIoCall:
    """Tests for I/O call detection."""

    def test_open_is_io(self) -> None:
        assert _is_io_call("open", "builtins") is True

    def test_os_module_is_io(self) -> None:
        assert _is_io_call("os.listdir", "os") is True

    def test_dict_get_is_not_io(self) -> None:
        # After fix: "get" is no longer in _IO_CALL_PATTERNS
        assert _is_io_call("config.get", "myproject.config") is False

    def test_dict_delete_is_not_io(self) -> None:
        assert _is_io_call("items.delete", "myproject.items") is False

    def test_connect_is_io(self) -> None:
        assert _is_io_call("connect", "myproject.db") is True


class TestClassifyReason:
    """Tests for mock classification reason strings."""

    def test_filesystem_io(self) -> None:
        result = _classify_reason("os", is_external=True, is_io=True)
        assert "file system" in result

    def test_subprocess(self) -> None:
        result = _classify_reason("subprocess", is_external=True, is_io=True)
        assert "subprocess" in result

    def test_network_io(self) -> None:
        result = _classify_reason("requests", is_external=True, is_io=True)
        assert "network" in result

    def test_database_io(self) -> None:
        result = _classify_reason("sqlite3", is_external=True, is_io=True)
        assert "database" in result

    def test_internal_code(self) -> None:
        result = _classify_reason("myproject.utils", is_external=False, is_io=False)
        assert "internal" in result

    def test_socket_network_io(self) -> None:
        """Branch (line 1021): socket library family → network I/O."""
        result = _classify_reason("socket", is_external=True, is_io=True)
        assert "network" in result

    def test_smtplib_network_io(self) -> None:
        result = _classify_reason("smtplib", is_external=True, is_io=True)
        assert "network" in result

    def test_redis_database(self) -> None:
        result = _classify_reason("redis", is_external=True, is_io=True)
        assert "database" in result

    def test_pymongo_database(self) -> None:
        result = _classify_reason("pymongo", is_external=True, is_io=True)
        assert "database" in result

    def test_boto3_cloud(self) -> None:
        """Branch (lines 1024-1025): boto3 → cloud API."""
        result = _classify_reason("boto3", is_external=True, is_io=True)
        assert "cloud" in result

    def test_unknown_io_default(self) -> None:
        """Branch (line 1026): unknown I/O library → generic external I/O."""
        result = _classify_reason("some_obscure_library", is_external=True, is_io=True)
        assert "external I/O" in result

    def test_external_non_io(self) -> None:
        """Branch (line 1028): external but not I/O → external library."""
        result = _classify_reason("numpy", is_external=True, is_io=False)
        assert "external library" in result


class TestDescribeUncoveredBlock:
    """Tests for uncovered block description."""

    def test_if_branch(self) -> None:
        lines = ["if x > 0:", "    return x"]
        result = _describe_uncovered_block(lines, [1, 2])
        assert "branch" in result

    def test_except_handler(self) -> None:
        lines = ["try:", "    do_thing()", "except ValueError:", "    handle()"]
        result = _describe_uncovered_block(lines, [4])
        # Line 4 is "handle()", line before is "except ValueError:"
        assert "exception handler" in result

    def test_raise_path(self) -> None:
        lines = ["raise ValueError('bad')"]
        result = _describe_uncovered_block(lines, [1])
        assert "error path" in result

    def test_return_path(self) -> None:
        lines = ["return None"]
        result = _describe_uncovered_block(lines, [1])
        assert "return path" in result

    def test_empty_block(self) -> None:
        result = _describe_uncovered_block([], [])
        assert result == "uncovered code"

    def test_out_of_range_line(self) -> None:
        result = _describe_uncovered_block(["only line"], [100])
        assert "100" in result

    def test_skips_blank_lines_above(self) -> None:
        """Branch (lines 1069-1070): scan upward past blank/comment lines."""
        lines = ["if x > 0:", "", "    return x"]
        result = _describe_uncovered_block(lines, [3])
        assert "branch" in result

    def test_skips_comment_lines_above(self) -> None:
        lines = ["if x > 0:", "    # explain", "    return x"]
        result = _describe_uncovered_block(lines, [3])
        assert "branch" in result

    def test_else_branch(self) -> None:
        """Branch (lines 1076-1077): else: above the block."""
        lines = ["if x > 0:", "    return 1", "else:", "    return -1"]
        result = _describe_uncovered_block(lines, [4])
        assert "else" in result

    def test_first_line_is_if_directly(self) -> None:
        """Branch (line 1080-1082): first line of block IS the if."""
        lines = ["x = 1", "if x > 0:", "    pass"]
        result = _describe_uncovered_block(lines, [2])
        assert "branch" in result

    def test_first_line_is_except_directly(self) -> None:
        """Branch (line 1083-1084): first line is except handler."""
        lines = ["try:", "    pass", "except ValueError:", "    pass"]
        result = _describe_uncovered_block(lines, [3])
        assert "exception handler" in result

    def test_first_line_is_raise_directly(self) -> None:
        lines = ["x = 1", "raise ValueError('bad')"]
        result = _describe_uncovered_block(lines, [2])
        assert "error path" in result

    def test_first_line_is_return_directly(self) -> None:
        lines = ["x = 1", "return x"]
        result = _describe_uncovered_block(lines, [2])
        assert "return path" in result

    def test_falls_through_to_lines_label(self) -> None:
        """Branch (line 1090): no recognized pattern → 'lines X-Y'."""
        lines = ["x = 1", "y = 2", "z = x + y"]
        result = _describe_uncovered_block(lines, [2, 3])
        assert "lines 2-3" in result


class TestMapCoverageToFunctions:
    """Tests for mapping file-level coverage to per-function metrics."""

    def _make_func(
        self, name: str = "func", start: int = 1, end: int = 5,
    ) -> _FunctionNode:
        return _FunctionNode(
            name=name,
            qualified_name=name,
            line_start=start,
            line_end=end,
            is_method=False,
            class_name=None,
        )

    def test_empty_coverage_data(self) -> None:
        func = self._make_func()
        results = _map_coverage_to_functions({}, [func], "test.py")
        assert len(results) == 1
        assert results[0].total_lines >= 1
        assert len(results[0].executed_lines) == 0

    def test_no_functions(self) -> None:
        results = _map_coverage_to_functions({"files": {}}, [], "test.py")
        assert results == []

    def test_file_not_in_coverage_data(self) -> None:
        coverage_data = {"files": {"other.py": {"executed_lines": [1, 2]}}}
        func = self._make_func()
        results = _map_coverage_to_functions(coverage_data, [func], "test.py")
        assert len(results) == 1
        assert len(results[0].executed_lines) == 0

    def test_zero_executable_lines(self) -> None:
        """Functions with no executable lines (stubs) should report total_lines=0."""
        import tempfile
        import os
        # Create a real file so samefile matching works
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def stub():\n    pass\n")
            path = f.name
        try:
            coverage_data = {
                "files": {
                    path: {
                        "executed_lines": [],
                        "missing_lines": [],
                        "executed_branches": [],
                        "missing_branches": [],
                    }
                }
            }
            func = self._make_func(start=1, end=2)
            results = _map_coverage_to_functions(coverage_data, [func], path)
            assert len(results) == 1
            # total_lines should be 0 when no executable lines are reported
            assert results[0].total_lines == 0
        finally:
            os.unlink(path)

    def test_full_coverage(self) -> None:
        import tempfile
        import os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def func():\n    return 1\n")
            path = f.name
        try:
            coverage_data = {
                "files": {
                    path: {
                        "executed_lines": [1, 2],
                        "missing_lines": [],
                        "executed_branches": [],
                        "missing_branches": [],
                    }
                }
            }
            func = self._make_func(start=1, end=2)
            results = _map_coverage_to_functions(coverage_data, [func], path)
            assert len(results) == 1
            assert len(results[0].executed_lines) == 2
            assert len(results[0].missing_lines) == 0
        finally:
            os.unlink(path)


class TestGenerateTestCode:
    """Tests for test code generation."""

    def test_basic_function(self) -> None:
        code = _generate_test_code("compute", None, "mymodule", [5, 6], "lines 5-6", [])
        assert "def test_compute_line_5" in code
        assert "from mymodule import compute" in code
        assert "result = compute()" in code

    def test_method_with_class(self) -> None:
        code = _generate_test_code("process", "Handler", "pkg.mod", [10], "line 10", [])
        assert "from pkg.mod import Handler" in code
        assert "instance = Handler()" in code
        assert "instance.process()" in code

    def test_with_mock_dependency(self) -> None:
        dep = MockDependency(
            name="open",
            import_module="builtins",
            is_external=False,
            mock_necessary=True,
            reason="file system I/O",
        )
        code = _generate_test_code("load", None, "pkg.loader", [3], "line 3", [dep])
        assert "@patch(" in code
        # Patch target should use module_path (usage site), not import_module
        assert "pkg.loader.open" in code
        assert "mock_open" in code

    def test_private_function_strip_underscore(self) -> None:
        code = _generate_test_code("_helper", None, "mod", [1], "line 1", [])
        assert "def test_helper_line_1" in code


class TestGetCallName:
    """Tests for AST call name extraction."""

    def test_simple_name(self) -> None:
        import ast
        node = ast.parse("foo()").body[0].value  # type: ignore[attr-defined]
        assert _get_call_name(node) == "foo"

    def test_attribute_call(self) -> None:
        import ast
        node = ast.parse("os.path.exists()").body[0].value  # type: ignore[attr-defined]
        assert _get_call_name(node) == "os.path.exists"

    def test_complex_call_returns_none(self) -> None:
        import ast
        node = ast.parse("items[0]()").body[0].value  # type: ignore[attr-defined]
        assert _get_call_name(node) is None


class TestCoverageAnalyzerAdapterInit:
    """Tests for CoverageAnalyzerAdapter __init__ — covers branches at 126, 128."""

    def test_without_consent_raises(self) -> None:
        """Branch (line 126): allow_code_execution=False → UnsafeCodeExecutionError."""
        from serenecode.adapters.coverage_adapter import CoverageAnalyzerAdapter
        from serenecode.core.exceptions import UnsafeCodeExecutionError
        with pytest.raises(UnsafeCodeExecutionError):
            CoverageAnalyzerAdapter(allow_code_execution=False)

    def test_with_consent_constructs_ok(self) -> None:
        from serenecode.adapters.coverage_adapter import CoverageAnalyzerAdapter
        adapter = CoverageAnalyzerAdapter(allow_code_execution=True)
        assert adapter._allow_code_execution is True
        assert adapter._coverage_threshold == 80.0

    def test_custom_timeout(self) -> None:
        from serenecode.adapters.coverage_adapter import CoverageAnalyzerAdapter
        adapter = CoverageAnalyzerAdapter(allow_code_execution=True, test_timeout=900)
        assert adapter._test_timeout == 900

    def test_coverage_unavailable_raises_tool_not_installed(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Branch (line 128): _COVERAGE_AVAILABLE=False → ToolNotInstalledError."""
        from serenecode.adapters import coverage_adapter
        from serenecode.core.exceptions import ToolNotInstalledError
        monkeypatch.setattr(coverage_adapter, "_COVERAGE_AVAILABLE", False)
        with pytest.raises(ToolNotInstalledError, match="coverage is not installed"):
            coverage_adapter.CoverageAnalyzerAdapter(allow_code_execution=True)


class TestFindDependenciesInLines:
    """Tests for _find_dependencies_in_lines — covers branches at 834-868."""

    def test_finds_call_on_uncovered_line(self) -> None:
        import ast
        source = "import os\n\ndef f():\n    os.path.exists('foo')\n"
        tree = ast.parse(source)
        deps = _find_dependencies_in_lines(tree, [4], source, "mod.py")
        assert len(deps) >= 1
        assert any("os.path.exists" in dep.name for dep in deps)

    def test_no_calls_returns_empty(self) -> None:
        import ast
        source = "def f():\n    return 1\n"
        tree = ast.parse(source)
        deps = _find_dependencies_in_lines(tree, [2], source, "mod.py")
        assert deps == []

    def test_dedupes_by_call_name(self) -> None:
        import ast
        source = (
            "import os\n"
            "\n"
            "def f():\n"
            "    os.path.exists('a')\n"
            "    os.path.exists('b')\n"
        )
        tree = ast.parse(source)
        deps = _find_dependencies_in_lines(tree, [4, 5], source, "mod.py")
        # Both calls are the same name → deduped
        assert len(deps) == 1

    def test_skips_calls_outside_uncovered_lines(self) -> None:
        import ast
        source = (
            "import os\n"
            "def f():\n"
            "    os.path.exists('a')\n"  # line 3
            "    os.path.exists('b')\n"  # line 4
        )
        tree = ast.parse(source)
        # Only line 4 is uncovered — line 3 should be ignored
        deps = _find_dependencies_in_lines(tree, [4], source, "mod.py")
        assert len(deps) == 1

    def test_classifies_external_io(self) -> None:
        import ast
        source = "import os\ndef f():\n    os.system('ls')\n"
        tree = ast.parse(source)
        deps = _find_dependencies_in_lines(tree, [3], source, "mod.py")
        assert len(deps) == 1
        assert deps[0].mock_necessary is True
