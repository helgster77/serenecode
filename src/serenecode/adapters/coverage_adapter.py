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
    MockDependency,
    CoverageSuggestion,
)

try:
    import coverage as coverage_lib
    _COVERAGE_AVAILABLE = True
except ImportError:
    _COVERAGE_AVAILABLE = False

_TRUST_REQUIRED_MESSAGE = (
    "Level 3 coverage analysis imports and executes project modules. "
    "Re-run with allow_code_execution=True only for trusted code."
)

# I/O modules that always require mocking in tests
_IO_MODULES = frozenset({
    "os", "pathlib", "subprocess", "requests", "socket", "shutil",
    "tempfile", "glob", "http", "urllib", "sqlite3", "smtplib",
    "ftplib", "aiohttp", "boto3", "redis", "sqlalchemy",
    "httpx", "grpc", "paramiko", "fabric",
})

# I/O function/method patterns that suggest external interaction.
# Intentionally excludes generic names like "get", "post", "put", "delete",
# "patch" which match dict methods and other benign internal code.
_IO_CALL_PATTERNS = frozenset({
    "open", "read_file", "write_file", "connect", "send", "recv",
    "execute", "fetchall", "fetchone", "commit", "rollback",
    "request", "urlopen", "makefile", "sendall", "sendto",
})


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
    is_method: bool
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
        test_timeout: int = 120,
    ) -> None:
        """Initialize the coverage analyzer.

        Args:
            allow_code_execution: Must be True to run tests.
            coverage_threshold: Default coverage threshold percentage.
            test_timeout: Maximum seconds for the pytest subprocess to run.
        """
        if not allow_code_execution:
            raise UnsafeCodeExecutionError(_TRUST_REQUIRED_MESSAGE)
        if not _COVERAGE_AVAILABLE:
            raise ToolNotInstalledError(
                "coverage is not installed. Install with: pip install coverage"
            )
        self._allow_code_execution = allow_code_execution
        self._coverage_threshold = coverage_threshold
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

        # Step 1: Resolve module to file
        module = load_python_module(module_path, search_paths)
        try:
            # Use getsourcefile to get the .py path, not .pyc bytecode path
            source_file_result = inspect.getsourcefile(module)
            if source_file_result is None:
                source_file = inspect.getfile(module)
            else:
                source_file = source_file_result
        except (TypeError, OSError):
            return []
        source_file = str(Path(source_file).resolve())

        try:
            source = Path(source_file).read_text(encoding="utf-8")
        except OSError:
            return []

        # Step 2: Discover functions via AST
        functions = _discover_functions(source)
        if not functions:
            return []

        # Step 3: Run tests with coverage (cached per project root)
        project_root = _find_project_root(source_file, search_paths)
        cache_key = project_root or source_file
        if cache_key in self._coverage_cache:
            coverage_data = self._coverage_cache[cache_key]
        else:
            coverage_data = _run_tests_with_coverage(
                source_file, search_paths, test_timeout=self._test_timeout,
            )
            self._coverage_cache[cache_key] = coverage_data

        # Check for timeout or other errors signalled by _run_tests_with_coverage.
        error_msg = coverage_data.get("_error")
        if isinstance(error_msg, str):
            return [CoverageFinding(
                function_name="<module>",
                module_path=module_path,
                line_start=1,
                line_end=1,
                line_coverage_percent=0.0,
                branch_coverage_percent=0.0,
                uncovered_lines=(),
                uncovered_branches=(),
                suggestions=(),
                meets_threshold=False,
                message=f"Coverage analysis failed for '{module_path}': {error_msg}",
            )]

        # If no coverage data at all (no tests found or pytest failed), report
        # as a failure — missing tests must be written. Check if any lines were
        # actually executed — if zero lines were covered across all files,
        # no tests exercised this module.
        total_covered = sum(
            len(f.get("executed_lines", []))
            for f in coverage_data.get("files", {}).values()
        ) if coverage_data else 0
        if not coverage_data or total_covered == 0:
            return [CoverageFinding(
                function_name="<module>",
                module_path=module_path,
                line_start=1,
                line_end=1,
                line_coverage_percent=0.0,
                branch_coverage_percent=0.0,
                uncovered_lines=(),
                uncovered_branches=(),
                suggestions=(),
                meets_threshold=False,
                message=(
                    f"No test coverage data for '{module_path}' — "
                    "no tests found. Write tests to enable coverage analysis."
                ),
            )]

        # Step 4: Map coverage to functions
        function_coverages = _map_coverage_to_functions(
            coverage_data, functions, source_file,
        )

        # Step 5 & 6: Analyze uncovered paths and generate suggestions
        findings: list[CoverageFinding] = []
        # Loop invariant: findings contains results for function_coverages[0..i]
        for fc in function_coverages:
            line_pct = (
                100.0 * len(fc.executed_lines) / fc.total_lines
                if fc.total_lines > 0
                else 100.0
            )
            branch_pct = (
                100.0 * len(fc.executed_branches) / fc.total_branches
                if fc.total_branches > 0
                else 100.0
            )
            meets = line_pct >= effective_threshold and branch_pct >= effective_threshold

            suggestions: tuple[CoverageSuggestion, ...] = ()
            if not meets and fc.missing_lines:
                suggestions = _generate_suggestions(
                    fc, source, module_path,
                )

            if meets:
                message = (
                    f"'{fc.function.qualified_name}' has {line_pct:.0f}% line coverage "
                    f"and {branch_pct:.0f}% branch coverage (threshold: {effective_threshold:.0f}%)"
                )
            else:
                uncov_count = len(fc.missing_lines)
                message = (
                    f"'{fc.function.qualified_name}' has {line_pct:.0f}% line coverage "
                    f"and {branch_pct:.0f}% branch coverage — "
                    f"{uncov_count} lines uncovered (threshold: {effective_threshold:.0f}%)"
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
    test_timeout: int = 120,
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
        # No coverage data — report 0% for all functions
        results: list[_FunctionCoverage] = []
        # Loop invariant: results contains zero-coverage entries for functions[0..i]
        for func in functions:
            total = max(1, func.line_end - func.line_start + 1)
            all_lines = frozenset(range(func.line_start, func.line_end + 1))
            results.append(_FunctionCoverage(
                function=func,
                total_lines=total,
                executed_lines=frozenset(),
                missing_lines=all_lines,
                total_branches=0,
                executed_branches=(),
                missing_branches=(),
            ))
        return results

    # Extract file data from coverage JSON.
    # Coverage.py may store relative or absolute paths. We use os.path.samefile
    # for robust matching that handles symlinks and case-insensitive filesystems,
    # falling back to resolved string comparison if the file doesn't exist.
    files_data = coverage_data.get("files", {})
    file_info = None
    # Loop invariant: file_info is set if any key in files_data matches source_file
    for file_key, file_data in files_data.items():
        try:
            if os.path.exists(file_key) and os.path.exists(source_file):
                if os.path.samefile(file_key, source_file):
                    file_info = file_data
                    break
            else:
                resolved_key = str(Path(file_key).resolve())
                source_resolved = str(Path(source_file).resolve())
                if resolved_key == source_resolved:
                    file_info = file_data
                    break
        except (OSError, ValueError):
            continue

    if file_info is None:
        # Source file not in coverage data — 0% coverage
        results = []
        # Loop invariant: results contains zero-coverage entries for functions[0..i]
        for func in functions:
            total = max(1, func.line_end - func.line_start + 1)
            all_lines = frozenset(range(func.line_start, func.line_end + 1))
            results.append(_FunctionCoverage(
                function=func,
                total_lines=total,
                executed_lines=frozenset(),
                missing_lines=all_lines,
                total_branches=0,
                executed_branches=(),
                missing_branches=(),
            ))
        return results

    executed_lines_set = frozenset(file_info.get("executed_lines", []))
    missing_lines_set = frozenset(file_info.get("missing_lines", []))
    executed_branches_raw: list[list[int]] = file_info.get("executed_branches", [])
    missing_branches_raw: list[list[int]] = file_info.get("missing_branches", [])

    executed_branches_all = tuple((b[0], b[1]) for b in executed_branches_raw if len(b) == 2)
    missing_branches_all = tuple((b[0], b[1]) for b in missing_branches_raw if len(b) == 2)

    results = []
    # Loop invariant: results contains coverage for functions[0..i]
    for func in functions:
        func_range = range(func.line_start, func.line_end + 1)
        func_executed = frozenset(l for l in executed_lines_set if l in func_range)
        func_missing = frozenset(l for l in missing_lines_set if l in func_range)
        # Use the union of executed + missing as the set of executable lines.
        # This avoids double-counting if the sets overlap (they shouldn't, but
        # be safe) and correctly excludes non-executable lines (blank, comments).
        total = len(func_executed | func_missing)

        func_exec_branches = tuple(b for b in executed_branches_all if b[0] in func_range)
        func_miss_branches = tuple(b for b in missing_branches_all if b[0] in func_range)
        total_branches = len(func_exec_branches) + len(func_miss_branches)

        # When total is 0 (no executable lines — e.g., a stub function with
        # only a docstring or pass), keep total_lines=0 so the caller can
        # report 100% coverage rather than a misleading 0%.
        results.append(_FunctionCoverage(
            function=func,
            total_lines=total,
            executed_lines=func_executed,
            missing_lines=func_missing,
            total_branches=total_branches,
            executed_branches=func_exec_branches,
            missing_branches=func_miss_branches,
        ))

    return results


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.ensure(lambda result: isinstance(result, tuple), "result must be a tuple")
def _generate_suggestions(
    fc: _FunctionCoverage,
    source: str,
    module_path: str,
) -> tuple[CoverageSuggestion, ...]:
    """Generate test suggestions for uncovered code paths.

    Args:
        fc: Function coverage data.
        source: Full module source code.
        module_path: Module path for import references.

    Returns:
        Tuple of test suggestions.
    """
    lines = source.splitlines()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ()

    # Group contiguous uncovered lines into blocks
    blocks = _group_contiguous_lines(sorted(fc.missing_lines))
    suggestions: list[CoverageSuggestion] = []

    # Loop invariant: suggestions contains entries for blocks[0..i]
    for block in blocks:
        # Find dependencies in uncovered lines
        deps = _find_dependencies_in_lines(tree, block, source, module_path)

        # Find the branch condition if this is inside an if/try/while
        context = _describe_uncovered_block(lines, block)

        # Generate test code
        func_name = fc.function.name
        class_name = fc.function.class_name
        test_code = _generate_test_code(func_name, class_name, module_path, block, context, deps)

        all_necessary = all(d.mock_necessary for d in deps) if deps else True

        suggestions.append(CoverageSuggestion(
            description=context,
            target_lines=tuple(block),
            suggested_test_code=test_code,
            required_mocks=tuple(deps),
            all_mocks_necessary=all_necessary,
        ))

    return tuple(suggestions)


@icontract.require(
    lambda lines: isinstance(lines, list),
    "lines must be a list",
)
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _group_contiguous_lines(lines: list[int]) -> list[list[int]]:
    """Group sorted line numbers into contiguous blocks.

    Args:
        lines: Sorted list of line numbers.

    Returns:
        List of contiguous line groups.
    """
    if not lines:
        return []
    blocks: list[list[int]] = [[lines[0]]]
    # Loop invariant: blocks[-1] is the current contiguous group
    for line in lines[1:]:
        if line == blocks[-1][-1] + 1:
            blocks[-1].append(line)
        else:
            blocks.append([line])
    return blocks


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.require(
    lambda uncovered_lines: isinstance(uncovered_lines, list),
    "uncovered_lines must be a list",
)
@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _find_dependencies_in_lines(
    tree: ast.Module,
    uncovered_lines: list[int],
    source: str,
    module_path: str,
) -> list[MockDependency]:
    """Find call dependencies in uncovered lines via AST analysis.

    Args:
        tree: Parsed AST module.
        uncovered_lines: Line numbers to analyze.
        source: Full source code.
        module_path: Module path for classifying internal vs external.

    Returns:
        List of mock dependencies found.
    """
    line_set = frozenset(uncovered_lines)
    deps: list[MockDependency] = []
    seen_names: set[str] = set()

    # Build import map for the module
    imports = _build_import_map(tree)

    # Walk AST looking for calls on uncovered lines
    # Loop invariant: deps contains unique dependencies from nodes visited so far
    for node in ast.walk(tree):
        if not hasattr(node, "lineno") or node.lineno not in line_set:
            continue
        if not isinstance(node, ast.Call):
            continue

        call_name = _get_call_name(node)
        if call_name is None or call_name in seen_names:
            continue
        seen_names.add(call_name)

        # Classify the dependency
        top_module = call_name.split(".")[0]
        import_source = imports.get(top_module, top_module)
        is_external = _is_external_dependency(import_source)
        is_io = _is_io_call(call_name, import_source)

        deps.append(MockDependency(
            name=call_name,
            import_module=import_source,
            is_external=is_external,
            mock_necessary=is_io,
            reason=_classify_reason(import_source, is_external, is_io),
        ))

    return deps


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dict")
def _build_import_map(tree: ast.Module) -> dict[str, str]:
    """Build a mapping from local names to their import sources.

    Args:
        tree: Parsed AST module.

    Returns:
        Dict mapping local name to source module.
    """
    imports: dict[str, str] = {}
    # Walk the full AST to capture imports inside functions, try/except,
    # and if TYPE_CHECKING blocks — not just top-level imports.
    # Loop invariant: imports contains bindings from all import nodes visited so far
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # Loop invariant: imports contains bindings for aliases[0..j]
            for alias in node.names:
                local = alias.asname if alias.asname else alias.name
                imports[local] = alias.name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            # Loop invariant: imports contains bindings for aliases[0..j]
            for alias in node.names:
                local = alias.asname if alias.asname else alias.name
                imports[local] = module
    return imports


@icontract.require(
    lambda node: isinstance(node, ast.Call),
    "node must be an ast.Call",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be a string or None",
)
def _get_call_name(node: ast.Call) -> str | None:
    """Extract the call target name from an AST Call node.

    Args:
        node: An AST Call node.

    Returns:
        Dotted name string, or None if too complex.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts: list[str] = [func.attr]
        current: ast.expr = func.value
        # Variant: nesting depth decreases
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
            return ".".join(reversed(parts))
    return None


@icontract.require(
    lambda import_source: isinstance(import_source, str),
    "import_source must be a string",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _is_external_dependency(import_source: str) -> bool:
    """Check if an import source is an external package.

    Args:
        import_source: The source module name.

    Returns:
        True if external (not stdlib, not project-internal).
    """
    top = import_source.split(".")[0]
    return top in _IO_MODULES or top in {
        "celery", "django", "flask", "fastapi", "starlette",
        "pydantic", "motor", "pymongo",
    }


@icontract.require(
    lambda call_name: isinstance(call_name, str),
    "call_name must be a string",
)
@icontract.require(
    lambda import_source: isinstance(import_source, str),
    "import_source must be a string",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _is_io_call(call_name: str, import_source: str) -> bool:
    """Check if a call involves I/O that should be mocked.

    Args:
        call_name: The full call name.
        import_source: The source module.

    Returns:
        True if this is an I/O call requiring a mock.
    """
    top = import_source.split(".")[0]
    if top in _IO_MODULES:
        return True
    # Check for known I/O patterns in the call name
    final_name = call_name.rsplit(".", 1)[-1]
    return final_name in _IO_CALL_PATTERNS


@icontract.require(
    lambda import_source: isinstance(import_source, str),
    "import_source must be a string",
)
@icontract.require(
    lambda is_external: isinstance(is_external, bool),
    "is_external must be a bool",
)
@icontract.require(
    lambda is_io: isinstance(is_io, bool),
    "is_io must be a bool",
)
@icontract.ensure(
    lambda result: is_non_empty_string(result),
    "result must be a non-empty string",
)
def _classify_reason(import_source: str, is_external: bool, is_io: bool) -> str:
    """Produce a human-readable reason for the mock classification.

    Args:
        import_source: The source module.
        is_external: Whether it's external.
        is_io: Whether it's I/O.

    Returns:
        Reason string.
    """
    if is_io:
        top = import_source.split(".")[0]
        if top in {"os", "pathlib", "shutil", "tempfile", "glob"}:
            return "file system I/O"
        if top in {"subprocess"}:
            return "subprocess execution"
        if top in {"requests", "http", "urllib", "aiohttp", "httpx"}:
            return "network I/O"
        if top in {"socket", "smtplib", "ftplib", "paramiko"}:
            return "network I/O"
        if top in {"sqlite3", "sqlalchemy", "redis", "pymongo", "motor"}:
            return "database I/O"
        if top in {"boto3"}:
            return "cloud API"
        return "external I/O"
    if is_external:
        return "external library"
    return "internal code — can use real implementation"


@icontract.require(
    lambda lines: isinstance(lines, list),
    "lines must be a list",
)
@icontract.require(
    lambda block: isinstance(block, list),
    "block must be a list",
)
@icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
def _describe_uncovered_block(lines: list[str], block: list[int]) -> str:
    """Describe what an uncovered block of code does.

    Args:
        lines: All source lines (0-indexed).
        block: 1-indexed line numbers of the uncovered block.

    Returns:
        Human-readable description.
    """
    if not block:
        return "uncovered code"

    first_line_idx = block[0] - 1
    if first_line_idx < 0 or first_line_idx >= len(lines):
        return f"lines {block[0]}-{block[-1]}"

    first_line = lines[first_line_idx].strip()

    # Check lines BEFORE the block for branch context, scanning upward
    # past comments and blank lines to find the controlling statement.
    # Variant: scan_idx decreases each iteration, bounded by 0
    scan_idx = first_line_idx - 1
    scan_limit = max(0, first_line_idx - 5)
    while scan_idx >= scan_limit:
        prev_line = lines[scan_idx].strip()
        # Skip blank lines and comments
        if not prev_line or prev_line.startswith("#"):
            scan_idx -= 1
            continue
        if prev_line.startswith("if ") or prev_line.startswith("elif "):
            condition = prev_line.rstrip(":").strip()
            return f"branch: {condition} (lines {block[0]}-{block[-1]})"
        if prev_line.startswith("except"):
            return f"exception handler: {prev_line.rstrip(':')} (lines {block[0]}-{block[-1]})"
        if prev_line.startswith("else"):
            return f"else branch (lines {block[0]}-{block[-1]})"
        break

    if first_line.startswith("if ") or first_line.startswith("elif "):
        condition = first_line.rstrip(":").strip()
        return f"branch: {condition} (lines {block[0]}-{block[-1]})"
    if first_line.startswith("except"):
        return f"exception handler: {first_line.rstrip(':')} (lines {block[0]}-{block[-1]})"
    if first_line.startswith("raise"):
        return f"error path: {first_line} (line {block[0]})"
    if first_line.startswith("return"):
        return f"return path (line {block[0]})"

    return f"lines {block[0]}-{block[-1]}"


@icontract.require(
    lambda func_name: is_non_empty_string(func_name),
    "func_name must be a non-empty string",
)
@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.require(
    lambda block: isinstance(block, list),
    "block must be a list",
)
@icontract.require(
    lambda context: isinstance(context, str),
    "context must be a string",
)
@icontract.require(
    lambda deps: isinstance(deps, list),
    "deps must be a list",
)
@icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
def _generate_test_code(
    func_name: str,
    class_name: str | None,
    module_path: str,
    block: list[int],
    context: str,
    deps: list[MockDependency],
) -> str:
    """Generate a pytest test function for an uncovered block.

    Args:
        func_name: Name of the function being tested.
        class_name: Class name if it's a method, None otherwise.
        module_path: Module path for imports.
        block: Uncovered line numbers.
        context: Description of what the block does.
        deps: Dependencies that need mocking.

    Returns:
        A complete test function as a string.
    """
    test_name = func_name.lstrip("_")
    line_range = f"{block[0]}-{block[-1]}" if len(block) > 1 else str(block[0])

    parts: list[str] = []

    # Import statement
    if class_name:
        parts.append(f"from {module_path} import {class_name}")
    else:
        parts.append(f"from {module_path} import {func_name}")

    # Mock imports
    if any(d.mock_necessary for d in deps):
        parts.append("from unittest.mock import patch, MagicMock")

    parts.append("")

    # Build decorator stack for mocks.
    # Patch at the usage site (module_path), not the definition site (dep.import_module).
    # See https://docs.python.org/3/library/unittest.mock.html#where-to-patch
    mock_decorators: list[str] = []
    mock_params: list[str] = []
    # Loop invariant: mock_decorators and mock_params account for deps[0..i] that need mocking
    for dep in deps:
        if dep.mock_necessary:
            mock_var = f"mock_{dep.name.replace('.', '_')}"
            mock_decorators.append(f"@patch('{module_path}.{dep.name.split('.')[-1]}')")
            mock_params.append(mock_var)

    # Function signature
    # Loop invariant: decorator lines added for mock_decorators[0..i]
    for dec in mock_decorators:
        parts.append(dec)

    params = ", ".join(mock_params) if mock_params else ""
    parts.append(f"def test_{test_name}_line_{block[0]}({params}):")
    parts.append(f'    """Cover {context}."""')

    # Setup mocks
    # Loop invariant: mock setup lines added for mock_params[0..i]
    for i, dep in enumerate(deps):
        if dep.mock_necessary and i < len(mock_params):
            parts.append(f"    {mock_params[i]}.return_value = None  # TODO: configure mock return value")

    # Call
    if class_name:
        parts.append(f"    instance = {class_name}()  # TODO: provide constructor args")
        parts.append(f"    result = instance.{func_name}()  # TODO: provide args to reach line {block[0]}")
    else:
        parts.append(f"    result = {func_name}()  # TODO: provide args to reach line {block[0]}")

    parts.append(f"    assert result is not None  # replace with specific assertion")

    return "\n".join(parts)
