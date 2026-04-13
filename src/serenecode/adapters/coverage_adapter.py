"""Coverage analysis adapter for Level 3.

This adapter implements the CoverageAnalyzer protocol by running
existing tests under coverage.py tracing, analyzing per-function
coverage, and generating test suggestions for uncovered paths
with mock necessity assessments.

This is an adapter module — it handles I/O (module importing, test
execution, subprocess calls) and is exempt from full contract requirements.
"""

from __future__ import annotations

import ast
import inspect
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import icontract

from serenecode.adapters.module_loader import load_python_module
from serenecode.contracts.predicates import is_non_empty_string
from serenecode.core.exceptions import ToolNotInstalledError, UnsafeCodeExecutionError
from serenecode.ports.coverage_analyzer import (
    CoverageFinding,
    CoverageSuggestion,
)

try:
    import coverage  # noqa: F401 — presence check only
    _COVERAGE_AVAILABLE = True
except ImportError:
    _COVERAGE_AVAILABLE = False

_TRUST_REQUIRED_MESSAGE = (
    "Level 3 coverage analysis imports and executes project modules. "
    "Re-run with allow_code_execution=True only for trusted code."
)


@icontract.invariant(
    lambda self: len(self.name) > 0 and self.line_start >= 1,
    "name must be non-empty and line_start must be >= 1",
)
@dataclass(frozen=True)
class _FunctionNode:
    """AST-extracted function information."""

    name: str
    qualified_name: str
    line_start: int
    line_end: int
    is_method: bool  # allow-unused: dataclass field used by consumers
    class_name: str | None


@icontract.invariant(
    lambda self: self.total_lines >= 0,
    "total_lines must be non-negative",
)
@dataclass(frozen=True)
class _FunctionCoverage:
    """Coverage metrics for a single function."""

    function: _FunctionNode
    total_lines: int
    executed_lines: frozenset[int]
    missing_lines: frozenset[int]
    total_branches: int
    executed_branches: tuple[tuple[int, int], ...]
    missing_branches: tuple[tuple[int, int], ...]


# Test suggestion helpers live in coverage_suggestions.py.
# Import _generate_suggestions so that analyze_module can use it unchanged.
# This import is placed after _FunctionCoverage to avoid a circular import:
# coverage_suggestions imports _FunctionCoverage from this module.
from serenecode.adapters.coverage_suggestions import (  # noqa: E402
    _generate_suggestions,
    _group_contiguous_lines,
    _find_dependencies_in_lines,
    _build_import_map,
    _get_call_name,
    _is_external_dependency,
    _is_io_call,
    _classify_reason,
    _describe_uncovered_block,
    _generate_test_code,
)


# no-invariant: adapter with mutable coverage cache for single-run optimization
class CoverageAnalyzerAdapter:
    """Coverage analysis implementation using coverage.py.

    Runs existing tests with coverage tracing, analyzes per-function
    coverage, and generates test suggestions for uncovered paths.

    Coverage data is cached per project root so that multiple modules
    in the same project share a single pytest run.
    """

    def __init__(
        self,
        allow_code_execution: bool = False,
        coverage_threshold: float = 80.0,
        test_timeout: int = 600,
    ) -> None:
        """Initialize the coverage analyzer.

        Args:
            allow_code_execution: Must be True to run tests.
            coverage_threshold: Default coverage threshold percentage.
            test_timeout: Maximum seconds for the pytest subprocess to run.
                Defaults to 600 seconds (10 minutes), enough headroom for
                a project test suite of a few minutes. Override via the
                CLI's --coverage-timeout flag for very large suites.
        """
        if not allow_code_execution:
            raise UnsafeCodeExecutionError(_TRUST_REQUIRED_MESSAGE)
        if not _COVERAGE_AVAILABLE:
            raise ToolNotInstalledError(
                "coverage is not installed. Install with: pip install coverage"
            )
        self._allow_code_execution = allow_code_execution
        self._coverage_threshold = coverage_threshold  # allow-unused: stored config, read by tests
        self._test_timeout = test_timeout
        self._coverage_cache: dict[str, dict[str, Any]] = {}

    @icontract.require(
        lambda self: self._allow_code_execution,
        "code execution must be explicitly allowed",
    )
    @icontract.require(
        lambda module_path: is_non_empty_string(module_path),
        "module_path must be a non-empty string",
    )
    @icontract.require(
        lambda search_paths: isinstance(search_paths, tuple),
        "search_paths must be a tuple",
    )
    @icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
    def analyze_module(
        self,
        module_path: str,
        search_paths: tuple[str, ...] = (),
        coverage_threshold: float = 80.0,
    ) -> list[CoverageFinding]:
        """Run coverage analysis on all functions in a module.

        Args:
            module_path: Importable Python module path to analyze.
            search_paths: sys.path roots needed to import the module.
            coverage_threshold: Minimum coverage percentage to pass.

        Returns:
            List of coverage findings per function.
        """
        effective_threshold = coverage_threshold

        source_file, source = _resolve_module_source(module_path, search_paths)
        if source_file is None or source is None:
            return []

        functions = _discover_functions(source)
        if not functions:
            return []

        coverage_data = self._get_coverage_data(source_file, search_paths)

        error_finding = _check_coverage_error(coverage_data, module_path)
        if error_finding is not None:
            return [error_finding]
        empty_finding = _check_empty_coverage(coverage_data, module_path)
        if empty_finding is not None:
            return [empty_finding]

        function_coverages = _map_coverage_to_functions(
            coverage_data, functions, source_file,
        )

        return _build_coverage_findings(
            function_coverages, source, module_path, effective_threshold,
        )

    def _get_coverage_data(
        self,
        source_file: str,
        search_paths: tuple[str, ...],
    ) -> dict[str, Any]:
        """Get coverage data, using cache when available."""
        project_root = _find_project_root(source_file, search_paths)
        cache_key = project_root or source_file
        if cache_key in self._coverage_cache:
            return self._coverage_cache[cache_key]
        coverage_data = _run_tests_with_coverage(
            source_file, search_paths, test_timeout=self._test_timeout,
        )
        self._coverage_cache[cache_key] = coverage_data
        return coverage_data


def _resolve_module_source(
    module_path: str,
    search_paths: tuple[str, ...],
) -> tuple[str | None, str | None]:
    """Resolve a module path to its source file and content."""
    module = load_python_module(module_path, search_paths)
    try:
        source_file_result = inspect.getsourcefile(module)
        if source_file_result is None:
            source_file = inspect.getfile(module)
        else:
            source_file = source_file_result
    except (TypeError, OSError):
        return None, None
    source_file = str(Path(source_file).resolve())
    try:
        source = Path(source_file).read_text(encoding="utf-8")
    except OSError:
        return None, None
    return source_file, source


def _check_coverage_error(
    coverage_data: dict[str, Any],
    module_path: str,
) -> CoverageFinding | None:
    """Return a finding if coverage data has an error, else None."""
    error_msg = coverage_data.get("_error")
    if isinstance(error_msg, str):
        return CoverageFinding(
            function_name="<module>", module_path=module_path,
            line_start=1, line_end=1,
            line_coverage_percent=0.0, branch_coverage_percent=0.0,
            uncovered_lines=(), uncovered_branches=(), suggestions=(),
            meets_threshold=False,
            message=f"Coverage analysis failed for '{module_path}': {error_msg}",
        )
    return None


def _check_empty_coverage(
    coverage_data: dict[str, Any],
    module_path: str,
) -> CoverageFinding | None:
    """Return a finding if no tests exercised the module, else None."""
    total_covered = sum(
        len(f.get("executed_lines", []))
        for f in coverage_data.get("files", {}).values()
    ) if coverage_data else 0
    if not coverage_data or total_covered == 0:
        return CoverageFinding(
            function_name="<module>", module_path=module_path,
            line_start=1, line_end=1,
            line_coverage_percent=0.0, branch_coverage_percent=0.0,
            uncovered_lines=(), uncovered_branches=(), suggestions=(),
            meets_threshold=False,
            message=(
                f"No test coverage data for '{module_path}' — "
                "no tests found. Write tests to enable coverage analysis."
            ),
        )
    return None


def _build_coverage_findings(
    function_coverages: list[_FunctionCoverage],
    source: str,
    module_path: str,
    threshold: float,
) -> list[CoverageFinding]:
    """Build coverage findings from per-function metrics."""
    findings: list[CoverageFinding] = []
    # Loop invariant: findings contains results for function_coverages[0..i]
    for fc in function_coverages:
        line_pct = (
            100.0 * len(fc.executed_lines) / fc.total_lines
            if fc.total_lines > 0 else 100.0
        )
        branch_pct = (
            100.0 * len(fc.executed_branches) / fc.total_branches
            if fc.total_branches > 0 else 100.0
        )
        meets = line_pct >= threshold and branch_pct >= threshold

        suggestions: tuple[CoverageSuggestion, ...] = ()
        if not meets and fc.missing_lines:
            suggestions = _generate_suggestions(fc, source, module_path)

        if meets:
            message = (
                f"'{fc.function.qualified_name}' has {line_pct:.0f}% line coverage "
                f"and {branch_pct:.0f}% branch coverage (threshold: {threshold:.0f}%)"
            )
        else:
            message = (
                f"'{fc.function.qualified_name}' has {line_pct:.0f}% line coverage "
                f"and {branch_pct:.0f}% branch coverage — "
                f"{len(fc.missing_lines)} lines uncovered (threshold: {threshold:.0f}%)"
            )

        findings.append(CoverageFinding(
            function_name=fc.function.qualified_name,
            module_path=module_path,
            line_start=fc.function.line_start,
            line_end=fc.function.line_end,
            line_coverage_percent=line_pct,
            branch_coverage_percent=branch_pct,
            uncovered_lines=tuple(sorted(fc.missing_lines)),
            uncovered_branches=fc.missing_branches,
            suggestions=suggestions,
            meets_threshold=meets,
            message=message,
        ))

    return findings


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _discover_functions(source: str) -> list[_FunctionNode]:
    """Parse source and extract all function definitions with line ranges.

    Recursively walks the AST to discover nested functions and methods
    in nested classes, not just top-level definitions.

    Args:
        source: Python source code.

    Returns:
        List of function nodes with name, line range, and class context.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    functions: list[_FunctionNode] = []
    _walk_for_functions(tree, functions, prefix="", class_name=None)
    return functions


@icontract.require(
    lambda node: isinstance(node, ast.AST),
    "node must be an AST node",
)
@icontract.require(
    lambda functions: isinstance(functions, list),
    "functions must be a list",
)
@icontract.require(
    lambda prefix: isinstance(prefix, str),
    "prefix must be a string",
)
@icontract.ensure(
    lambda result: result is None,
    "function mutates the accumulator list in place",
)
def _walk_for_functions(
    node: ast.AST,
    functions: list[_FunctionNode],
    prefix: str,
    class_name: str | None,
) -> None:
    """Recursively discover function definitions in an AST subtree.

    Args:
        node: Current AST node to examine.
        functions: Accumulator list for discovered functions.
        prefix: Dotted qualified name prefix from enclosing scopes.
        class_name: Enclosing class name if inside a class, None otherwise.
    """
    # Variant: AST depth decreases with each recursive call
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified = f"{prefix}.{child.name}" if prefix else child.name
            functions.append(_FunctionNode(
                name=child.name,
                qualified_name=qualified,
                line_start=child.lineno,
                line_end=child.end_lineno or child.lineno,
                is_method=class_name is not None,
                class_name=class_name,
            ))
            # Recurse into function body for nested functions
            _walk_for_functions(child, functions, prefix=qualified, class_name=None)
        elif isinstance(child, ast.ClassDef):
            qualified = f"{prefix}.{child.name}" if prefix else child.name
            # Recurse into class body for methods and nested classes
            _walk_for_functions(child, functions, prefix=qualified, class_name=child.name)


@icontract.require(
    lambda source_file: is_non_empty_string(source_file),
    "source_file must be a non-empty string",
)
@icontract.require(
    lambda search_paths: isinstance(search_paths, tuple),
    "search_paths must be a tuple",
)
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dict")
def _run_tests_with_coverage(
    source_file: str,
    search_paths: tuple[str, ...],
    test_timeout: int = 600,
) -> dict[str, Any]:
    """Run tests in subprocess with coverage and return JSON data.

    Args:
        source_file: Absolute path to the source file being analyzed.
        search_paths: Import roots for the project.
        test_timeout: Maximum seconds for the pytest subprocess.

    Returns:
        Parsed coverage JSON data, or empty dict on failure.
    """
    # Find project root by walking up from source file
    project_root = _find_project_root(source_file, search_paths)
    if project_root is None:
        return {}

    # Find test directory
    test_dir = _find_test_dir(project_root)

    # Determine coverage scope: use the source root (search paths) to
    # cover all project source in a single run, not just one file's dir.
    cov_source = _find_source_root(project_root, search_paths)

    with tempfile.TemporaryDirectory() as tmpdir:
        json_file = os.path.join(tmpdir, "coverage.json")

        cmd = [
            sys.executable, "-m", "pytest",
            "--no-header", "-q",
            f"--cov={cov_source}",
            "--cov-branch",
            f"--cov-report=json:{json_file}",
            "--cov-report=",
            "--tb=no",
        ]
        if test_dir is not None:
            cmd.append(test_dir)
        else:
            cmd.append(project_root)

        from serenecode.adapters import safe_subprocess_env

        extra: dict[str, str] = {}
        if search_paths:
            existing_pypath = os.environ.get("PYTHONPATH", "")
            existing_parts = set(existing_pypath.split(os.pathsep)) if existing_pypath else set()
            new_parts: list[str] = []
            # Loop invariant: new_parts contains search_paths[0..i] not already in existing
            for sp in search_paths:
                if sp not in existing_parts:
                    new_parts.append(sp)
            if new_parts or existing_pypath:
                all_parts = new_parts + ([existing_pypath] if existing_pypath else [])
                extra["PYTHONPATH"] = os.pathsep.join(all_parts)
        env = safe_subprocess_env(extra_paths=extra)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=test_timeout,
                cwd=project_root,
                env=env,
            )
        except subprocess.TimeoutExpired:
            return {"_error": f"test execution timed out after {test_timeout} seconds"}
        except FileNotFoundError:
            return {"_error": "pytest not found — install pytest and pytest-cov"}
        except OSError as os_err:
            return {"_error": f"cannot run pytest: {os_err}"}

        if not os.path.exists(json_file):
            stderr_hint = (proc.stderr or "").strip()[:200]
            return {"_error": f"pytest did not produce coverage data (exit {proc.returncode})"
                    + (f": {stderr_hint}" if stderr_hint else "")}

        try:
            with open(json_file, encoding="utf-8") as f:
                return json.load(f)  # type: ignore[no-any-return]
        except json.JSONDecodeError as json_err:
            return {"_error": f"coverage JSON is malformed: {json_err}"}
        except OSError as read_err:
            return {"_error": f"cannot read coverage JSON: {read_err}"}


@icontract.require(
    lambda source_file: isinstance(source_file, str),
    "source_file must be a string",
)
@icontract.require(
    lambda search_paths: isinstance(search_paths, tuple),
    "search_paths must be a tuple",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be a string or None",
)
def _find_project_root(
    source_file: str,
    search_paths: tuple[str, ...],
) -> str | None:
    """Find the project root directory.

    Walks up from source_file looking for pyproject.toml or setup.py.
    Falls back to the first search path.

    Args:
        source_file: Path to the source file.
        search_paths: Import roots.

    Returns:
        Project root path, or None.
    """
    current = Path(source_file).parent
    # Variant: depth decreases each iteration
    for _ in range(20):
        if (current / "pyproject.toml").exists() or (current / "setup.py").exists():
            return str(current)
        parent = current.parent
        if parent == current:
            break
        current = parent

    if search_paths:
        return search_paths[0]
    return None


@icontract.require(
    lambda project_root: is_non_empty_string(project_root),
    "project_root must be a non-empty string",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be a string or None",
)
def _find_test_dir(project_root: str) -> str | None:
    """Find the test directory in a project.

    Args:
        project_root: Project root directory.

    Returns:
        Path to test directory, or None.
    """
    # Loop invariant: checked candidates[0..i] for existence
    for candidate in ("tests", "test"):
        test_path = os.path.join(project_root, candidate)
        if os.path.isdir(test_path):
            return test_path
    return None


@icontract.require(
    lambda project_root: is_non_empty_string(project_root),
    "project_root must be a non-empty string",
)
@icontract.require(
    lambda search_paths: isinstance(search_paths, tuple),
    "search_paths must be a tuple",
)
@icontract.ensure(
    lambda result: isinstance(result, str),
    "result must be a non-empty string",
)
def _find_source_root(
    project_root: str,
    search_paths: tuple[str, ...],
) -> str:
    """Find the source root directory for coverage scoping.

    Uses the first search path that exists, then falls back to
    common source directory names, then the project root itself.

    Args:
        project_root: Project root directory.
        search_paths: Import roots for the project.

    Returns:
        Path to use as the --cov source root.
    """
    # Prefer an explicit search path that exists
    # Loop invariant: checked search_paths[0..i] for existence
    for sp in search_paths:
        if os.path.isdir(sp):
            return sp

    # Fall back to common source directory names
    # Loop invariant: checked candidates[0..i] for existence
    for candidate in ("src", "lib"):
        src_path = os.path.join(project_root, candidate)
        if os.path.isdir(src_path):
            return src_path

    return project_root


@icontract.require(
    lambda coverage_data: isinstance(coverage_data, dict),
    "coverage_data must be a dict",
)
@icontract.require(
    lambda functions: isinstance(functions, list),
    "functions must be a list",
)
@icontract.require(
    lambda source_file: isinstance(source_file, str),
    "source_file must be a string",
)
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _map_coverage_to_functions(
    coverage_data: dict[str, Any],
    functions: list[_FunctionNode],
    source_file: str,
) -> list[_FunctionCoverage]:
    """Map file-level coverage data to per-function metrics.

    Args:
        coverage_data: Parsed JSON from coverage.py report.
        functions: AST-discovered functions.
        source_file: Absolute path to the source file.

    Returns:
        List of per-function coverage metrics.
    """
    if not coverage_data:
        return _zero_coverage_for_all(functions)

    file_info = _find_file_coverage_data(coverage_data, source_file)
    if file_info is None:
        return _zero_coverage_for_all(functions)

    return _map_file_info_to_functions(file_info, functions)


def _zero_coverage_for_all(functions: list[_FunctionNode]) -> list[_FunctionCoverage]:
    """Return zero-coverage entries for all functions."""
    results: list[_FunctionCoverage] = []
    # Loop invariant: results contains zero-coverage entries for functions[0..i]
    for func in functions:
        total = max(1, func.line_end - func.line_start + 1)
        all_lines = frozenset(range(func.line_start, func.line_end + 1))
        results.append(_FunctionCoverage(
            function=func, total_lines=total,
            executed_lines=frozenset(), missing_lines=all_lines,
            total_branches=0, executed_branches=(), missing_branches=(),
        ))
    return results


def _find_file_coverage_data(
    coverage_data: dict[str, Any],
    source_file: str,
) -> dict[str, Any] | None:
    """Find the coverage data entry matching source_file."""
    files_data = coverage_data.get("files", {})
    # Loop invariant: file_info is set if any key in files_data matches source_file
    for file_key, file_data in files_data.items():
        try:
            if os.path.exists(file_key) and os.path.exists(source_file):
                if os.path.samefile(file_key, source_file):
                    return file_data
            else:
                resolved_key = str(Path(file_key).resolve())
                source_resolved = str(Path(source_file).resolve())
                if resolved_key == source_resolved:
                    return file_data
        except (OSError, ValueError):
            continue
    return None


def _map_file_info_to_functions(
    file_info: dict[str, Any],
    functions: list[_FunctionNode],
) -> list[_FunctionCoverage]:
    """Map file-level coverage data to per-function metrics."""
    executed_lines_set = frozenset(file_info.get("executed_lines", []))
    missing_lines_set = frozenset(file_info.get("missing_lines", []))
    executed_branches_raw: list[list[int]] = file_info.get("executed_branches", [])
    missing_branches_raw: list[list[int]] = file_info.get("missing_branches", [])
    executed_branches_all = tuple((b[0], b[1]) for b in executed_branches_raw if len(b) == 2)
    missing_branches_all = tuple((b[0], b[1]) for b in missing_branches_raw if len(b) == 2)

    results: list[_FunctionCoverage] = []
    # Loop invariant: results contains coverage for functions[0..i]
    for func in functions:
        func_range = range(func.line_start, func.line_end + 1)
        func_executed = frozenset(l for l in executed_lines_set if l in func_range)
        func_missing = frozenset(l for l in missing_lines_set if l in func_range)
        total = len(func_executed | func_missing)
        func_exec_branches = tuple(b for b in executed_branches_all if b[0] in func_range)
        func_miss_branches = tuple(b for b in missing_branches_all if b[0] in func_range)
        total_branches = len(func_exec_branches) + len(func_miss_branches)
        results.append(_FunctionCoverage(
            function=func, total_lines=total,
            executed_lines=func_executed, missing_lines=func_missing,
            total_branches=total_branches,
            executed_branches=func_exec_branches,
            missing_branches=func_miss_branches,
        ))
    return results
