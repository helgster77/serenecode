"""MCP tool implementations.

Each function in this module is registered as an MCP tool by `server.py`.
The implementations are intentionally thin: they delegate to the existing
serenecode public API and project the result into a JSON-friendly shape.

This module is part of the MCP composition root and is exempt from
full structural verification.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import icontract

from serenecode.adapters.local_fs import LocalFileReader
from serenecode.checker.structural import check_structural
from serenecode.config import (
    SerenecodeConfig,
    default_config,
    parse_serenecode_md,
)
from serenecode.core.exceptions import UnsafeCodeExecutionError
from serenecode.core.pipeline import SourceFile, run_pipeline
from serenecode.mcp.schemas import (
    CheckResponse,
    response_to_dict,
    to_check_response,
)
from serenecode.models import CheckResult
from serenecode.ports.dead_code_analyzer import DeadCodeAnalyzer
from serenecode.source_discovery import (
    build_source_files,
    determine_context_root,
    discover_test_file_stems,
    find_serenecode_md,
    find_spec_md,
    is_test_file_path,
)


# ---------------------------------------------------------------------------
# Server-level state
# ---------------------------------------------------------------------------


# no-invariant: simple per-process state container; allow_code_execution / project_root mutate only at startup
@dataclass
class ServerState:
    """Per-process state for the MCP server.

    Holds the project root, the allow-code-execution flag granted at
    server startup, and a small cache of parsed configs keyed by
    SERENECODE.md path so repeated tool calls don't re-parse on every
    request. The cache invalidates whenever the file's mtime changes.
    """

    project_root: str | None = None
    allow_code_execution: bool = False
    last_check: CheckResponse | None = None
    config_cache: dict[str, tuple[float, SerenecodeConfig]] = field(default_factory=dict)


_STATE: ServerState = ServerState()


@icontract.ensure(
    lambda result: isinstance(result, ServerState),
    "result must be the singleton ServerState",
)
def get_state() -> ServerState:
    """Return the singleton server state (used by server.py)."""
    return _STATE


# allow-unused: used by test infrastructure
@icontract.ensure(
    lambda: _STATE.project_root is None and _STATE.allow_code_execution is False,
    "after reset, state is in its default read-only configuration",
)
def reset_state() -> None:
    """Reset server state. Used by tests."""
    global _STATE
    _STATE = ServerState()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@icontract.require(
    lambda path: path is None or isinstance(path, str),
    "path must be None or a string",
)
@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty path string",
)
def _resolve_root(path: str | None) -> str:
    """Resolve a project root from a tool argument or fall back to server state."""
    if path:
        return determine_context_root(os.path.abspath(path))
    if _STATE.project_root:
        return _STATE.project_root
    return determine_context_root(os.path.abspath("."))


@icontract.require(
    lambda project_root: isinstance(project_root, str) and len(project_root) > 0,
    "project_root must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, SerenecodeConfig),
    "result must be a SerenecodeConfig",
)
def _load_config(project_root: str) -> SerenecodeConfig:
    """Load (and cache) the SerenecodeConfig for a project root."""
    reader = LocalFileReader()
    serenecode_md = find_serenecode_md(project_root, reader)
    if serenecode_md is None:
        return default_config()
    try:
        mtime = os.stat(serenecode_md).st_mtime
    except OSError:
        return parse_serenecode_md(reader.read_file(serenecode_md))
    cached = _STATE.config_cache.get(serenecode_md)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    config = parse_serenecode_md(reader.read_file(serenecode_md))
    _STATE.config_cache[serenecode_md] = (mtime, config)
    return config


@icontract.require(
    lambda level: isinstance(level, int) and 1 <= level <= 6,
    "level must be 1-6",
)
@icontract.ensure(lambda result: result is None, "no return value")
def _gate_code_execution(level: int) -> None:
    """Raise UnsafeCodeExecutionError if level requires code execution but it's not allowed."""
    if level >= 3 and not _STATE.allow_code_execution:
        raise UnsafeCodeExecutionError(
            "Levels 3-6 import and execute project modules. "
            "Start the server with --allow-code-execution to enable them.",
        )


@icontract.require(
    lambda file_path: isinstance(file_path, str) and len(file_path) > 0,
    "file_path must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple) and len(result) == 3,
    "result must be a (config, source_files, project_root) triple",
)
def _build_pipeline_for_file(
    file_path: str,
) -> tuple[SerenecodeConfig, tuple[SourceFile, ...], str]:
    """Build a single-file SourceFile tuple plus the resolved project root."""
    project_root = _resolve_root(os.path.dirname(file_path))
    reader = LocalFileReader()
    config = _load_config(project_root)
    source_files = build_source_files([file_path], reader, project_root)
    return config, source_files, project_root


@icontract.require(
    lambda level: isinstance(level, int) and 1 <= level <= 6,
    "level must be 1-6",
)
@icontract.ensure(
    lambda result: isinstance(result, dict),
    "result is a dict of adapter handles (some may be None)",
)
def _wire_adapters(level: int) -> dict[str, object]:
    """Wire up the adapter set for the requested level (mirrors `_run_check`)."""
    type_checker = None
    coverage_analyzer = None
    property_tester = None
    symbolic_checker = None
    dead_code_analyzer: DeadCodeAnalyzer | None = None

    if level >= 2:
        try:
            from serenecode.adapters.mypy_adapter import MypyTypeChecker
            type_checker = MypyTypeChecker()
        except ImportError:
            pass

    if level >= 3:
        try:
            from serenecode.adapters.coverage_adapter import CoverageAnalyzerAdapter
            coverage_analyzer = CoverageAnalyzerAdapter(allow_code_execution=True)
        except ImportError:
            pass

    if level >= 4:
        try:
            from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
            property_tester = HypothesisPropertyTester(allow_code_execution=True)
        except ImportError:
            pass

    if level >= 5:
        try:
            from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
            symbolic_checker = CrossHairSymbolicChecker(allow_code_execution=True)
        except ImportError:
            pass

    try:
        from serenecode.adapters.vulture_adapter import VultureDeadCodeAnalyzer
        dead_code_analyzer = VultureDeadCodeAnalyzer()
    except ImportError:
        from serenecode.adapters.unavailable_dead_code_adapter import UnavailableDeadCodeAnalyzer
        dead_code_analyzer = UnavailableDeadCodeAnalyzer("vulture is not installed")

    return {
        "type_checker": type_checker,
        "coverage_analyzer": coverage_analyzer,
        "property_tester": property_tester,
        "symbolic_checker": symbolic_checker,
        "dead_code_analyzer": dead_code_analyzer,
    }


@icontract.require(
    lambda check_result: isinstance(check_result, CheckResult),
    "check_result must be a CheckResult",
)
@icontract.require(
    lambda function_name: isinstance(function_name, str) and len(function_name) > 0,
    "function_name must be a non-empty string",
)
@icontract.ensure(
    lambda check_result, result: result.level_requested == check_result.level_requested,
    "filtered result must preserve the requested level",
)
def _filter_to_function(check_result: CheckResult, function_name: str) -> CheckResult:
    """Return a new CheckResult containing only entries for one function name.

    Used by per-function tool calls — the pipeline runs at file granularity
    and we narrow afterward so the agent sees a focused response.
    """
    from serenecode.models import make_check_result

    filtered = tuple(r for r in check_result.results if r.function == function_name)
    return make_check_result(
        filtered,
        level_requested=check_result.level_requested,
        duration_seconds=check_result.summary.duration_seconds,
        level_achieved=check_result.level_achieved,
    )


@icontract.require(
    lambda path: isinstance(path, str) and len(path) > 0,
    "path must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple) and len(result) == 2,
    "result must be a (spec_content, test_sources) pair",
)
def _load_spec_inputs(path: str) -> tuple[str | None, tuple[tuple[str, str], ...]]:
    """Load auto-discovered SPEC.md content and test sources for a path."""
    reader = LocalFileReader()
    spec_file = find_spec_md(path, reader)
    if spec_file is None:
        return None, ()

    spec_content = reader.read_file(spec_file)
    project_root = os.path.dirname(spec_file)
    tests_dir = os.path.join(project_root, "tests")
    if not os.path.isdir(tests_dir):
        return spec_content, ()

    try:
        test_files = reader.list_python_files(tests_dir)
    except OSError:
        return spec_content, ()

    test_sources: list[tuple[str, str]] = []
    # Loop invariant: test_sources contains collected test file sources from test_files[0..i]
    for test_file in test_files:
        try:
            test_sources.append((test_file, reader.read_file(test_file)))
        except OSError:
            continue
    return spec_content, tuple(test_sources)


# ---------------------------------------------------------------------------
# Tool: serenecode_check
# ---------------------------------------------------------------------------


@icontract.require(
    lambda path: isinstance(path, str),
    "path must be a string",
)
@icontract.require(
    lambda level: isinstance(level, int),
    "level must be an int",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "passed" in result,
    "result must be a JSON-friendly CheckResponse dict",
)
def tool_check(path: str = ".", level: int = 1) -> dict[str, object]:
    """Run the verification pipeline on a directory or file path (CI / full tree).

    Prefer `tool_check_function` or `tool_check_file` during interactive editing.
    Mirrors the `serenecode check <path>` CLI behavior: the literal path
    is what gets scanned (not its enclosing project root). Configuration
    is loaded from the nearest SERENECODE.md walking up from `path`.

    Args:
        path: Directory or file to check. Defaults to the current directory.
        level: Verification level (1-6). Levels 3+ require the server to
            have been started with --allow-code-execution.

    Returns:
        A JSON-friendly dict shaped as a CheckResponse.
    """
    if not 1 <= level <= 6:
        raise ValueError(f"level must be between 1 and 6, got {level}")
    _gate_code_execution(level)
    abs_path = os.path.abspath(path) if path else os.path.abspath(".")
    reader = LocalFileReader()
    config = _load_config(abs_path)
    files = reader.list_python_files(abs_path)
    source_files = build_source_files(files, reader, abs_path)
    test_stems = discover_test_file_stems(abs_path, reader)
    spec_content, test_sources = _load_spec_inputs(abs_path)
    adapters = _wire_adapters(level)
    result = run_pipeline(
        source_files=source_files,
        level=level,
        start_level=1,
        config=config,
        known_test_stems=test_stems,
        spec_content=spec_content,
        test_sources=test_sources,
        **adapters,  # type: ignore[arg-type]
    )
    response = to_check_response(result)
    _STATE.last_check = response
    return response_to_dict(response)


# ---------------------------------------------------------------------------
# Tool: serenecode_check_file
# ---------------------------------------------------------------------------


@icontract.require(
    lambda path: isinstance(path, str) and len(path) > 0,
    "path must be a non-empty string",
)
@icontract.require(
    lambda level: isinstance(level, int),
    "level must be an int",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "passed" in result,
    "result must be a JSON-friendly CheckResponse dict",
)
def tool_check_file(path: str, level: int = 1) -> dict[str, object]:
    """Run the verification pipeline scoped to a single source file.

    Prefer this over `tool_check` during editing; faster than a full-project run.

    Args:
        path: Absolute or project-relative path to the source file.
            (Same parameter name as `serenecode_check` for consistency.)
        level: Verification level (1-6). Levels 3+ require the server to
            have been started with --allow-code-execution.

    Returns:
        A JSON-friendly dict shaped as a CheckResponse with findings only
        for the named file.
    """
    if not 1 <= level <= 6:
        raise ValueError(f"level must be between 1 and 6, got {level}")
    _gate_code_execution(level)
    abs_file = os.path.abspath(path)
    config, source_files, project_root = _build_pipeline_for_file(abs_file)
    reader = LocalFileReader()
    test_stems = discover_test_file_stems(project_root, reader)
    spec_content, test_sources = _load_spec_inputs(abs_file)
    adapters = _wire_adapters(level)
    result = run_pipeline(
        source_files=source_files,
        level=level,
        start_level=1,
        config=config,
        known_test_stems=test_stems,
        spec_content=spec_content,
        test_sources=test_sources,
        **adapters,  # type: ignore[arg-type]
    )
    response = to_check_response(result)
    _STATE.last_check = response
    return response_to_dict(response)


# ---------------------------------------------------------------------------
# Tool: serenecode_check_function
# ---------------------------------------------------------------------------


@icontract.require(
    lambda path: isinstance(path, str) and len(path) > 0,
    "path must be a non-empty string",
)
@icontract.require(
    lambda function: isinstance(function, str) and len(function) > 0,
    "function must be a non-empty string",
)
@icontract.require(
    lambda level: isinstance(level, int),
    "level must be an int",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "passed" in result,
    "result must be a JSON-friendly CheckResponse dict",
)
def tool_check_function(
    path: str,
    function: str,
    level: int = 1,
) -> dict[str, object]:
    """Primary MCP entry point while editing: one function in one file.

    Prefer this over `tool_check` (full tree) on each edit.

    For Level 1 this runs the structural checker on the file's source string
    and filters results to the named function. For Levels 2+ it runs the
    full pipeline on a single-file source set and filters afterward.

    Args:
        path: Path to the source file containing the function. (Same
            parameter name as `serenecode_check` for consistency.)
        function: Function name. The first def with this name in the file
            is what gets reported.
        level: Verification level (1-6). Levels 3+ require the server to
            have been started with --allow-code-execution.

    Returns:
        A JSON-friendly dict shaped as a CheckResponse with findings only
        for the named function.
    """
    if not 1 <= level <= 6:
        raise ValueError(f"level must be between 1 and 6, got {level}")
    _gate_code_execution(level)
    abs_file = os.path.abspath(path)
    config, source_files, project_root = _build_pipeline_for_file(abs_file)
    reader = LocalFileReader()
    test_stems = discover_test_file_stems(project_root, reader)
    spec_content, test_sources = _load_spec_inputs(abs_file)
    adapters = _wire_adapters(level)
    result = run_pipeline(
        source_files=source_files,
        level=level,
        start_level=1,
        config=config,
        known_test_stems=test_stems,
        spec_content=spec_content,
        test_sources=test_sources,
        **adapters,  # type: ignore[arg-type]
    )
    filtered = _filter_to_function(result, function)
    response = to_check_response(filtered)
    _STATE.last_check = response
    return response_to_dict(response)


# ---------------------------------------------------------------------------
# Tool: serenecode_verify_fixed
# ---------------------------------------------------------------------------


@icontract.require(
    lambda path: isinstance(path, str) and len(path) > 0,
    "path must be a non-empty string",
)
@icontract.require(
    lambda function: isinstance(function, str) and len(function) > 0,
    "function must be a non-empty string",
)
@icontract.require(
    lambda finding_substring: isinstance(finding_substring, str) and len(finding_substring) > 0,
    "finding_substring must be a non-empty string",
)
@icontract.require(
    lambda level: isinstance(level, int),
    "level must be an int",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "fixed" in result,
    "result must contain a 'fixed' bool flag",
)
def tool_verify_fixed(
    path: str,
    function: str,
    finding_substring: str,
    level: int = 1,
) -> dict[str, object]:
    """Re-run the verification on one function and report whether a finding is gone.

    Args:
        path: Path to the source file.
        function: Function name to re-check.
        finding_substring: A substring of the original finding's `message`
            that uniquely identifies it (e.g. "missing @icontract.ensure").
        level: Verification level (1-6).

    Returns:
        A dict with keys:
            - fixed: bool — True if no finding's message contains the substring
            - remaining_findings: list of finding dicts whose message still matches
            - all_findings: full CheckResponse for the function
    """
    response_dict = tool_check_function(path=path, function=function, level=level)
    findings = response_dict.get("findings", [])
    if not isinstance(findings, list):
        findings = []
    needle = finding_substring.lower()
    remaining = [
        f for f in findings
        if isinstance(f, dict) and isinstance(f.get("message"), str)
        and needle in f["message"].lower()
    ]
    return {
        "fixed": len(remaining) == 0,
        "remaining_findings": remaining,
        "all_findings": response_dict,
    }


# ---------------------------------------------------------------------------
# Tool: serenecode_suggest_contracts
# ---------------------------------------------------------------------------


@icontract.require(
    lambda path: isinstance(path, str) and len(path) > 0,
    "path must be a non-empty string",
)
@icontract.require(
    lambda function: isinstance(function, str) and len(function) > 0,
    "function must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "suggestions" in result,
    "result must contain a 'suggestions' list",
)
def tool_suggest_contracts(path: str, function: str) -> dict[str, object]:
    """Suggest icontract decorators for a function.

    Runs the structural checker on the file and surfaces the
    `missing @icontract.require` / `missing @icontract.ensure` finding
    suggestions for the named function. The suggestions are produced by
    the existing structural checker, so contract recommendations stay
    consistent between the CLI and the MCP server.

    Args:
        path: Path to the source file.
        function: Function name.

    Returns:
        A dict with `function`, `path`, and `suggestions` (list of strings).
    """
    abs_file = os.path.abspath(path)
    config, source_files, _ = _build_pipeline_for_file(abs_file)
    sf = source_files[0]
    result = check_structural(sf.source, config, sf.module_path, sf.file_path)
    suggestions: list[str] = []
    for r in result.results:
        if r.function != function:
            continue
        for d in r.details:
            if d.suggestion and "icontract" in d.message.lower():
                suggestions.append(d.suggestion)
    return {
        "path": abs_file,
        "function": function,
        "suggestions": suggestions,
    }


# ---------------------------------------------------------------------------
# Tool: serenecode_uncovered
# ---------------------------------------------------------------------------


@icontract.require(
    lambda path: isinstance(path, str) and len(path) > 0,
    "path must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "findings" in result,
    "result must contain a findings list",
)
def tool_dead_code(path: str = ".") -> dict[str, object]:
    """Return structured likely dead-code findings for a path.

    Args:
        path: Directory or file path to analyze. Defaults to the current directory.

    Returns:
        A dict with:
            - `path`: absolute analyzed path
            - `status`: "ok", "unavailable", or "no_python_files"
            - `findings`: list of structured dead-code findings
    """
    abs_path = os.path.abspath(path)
    reader = LocalFileReader()
    files = [
        file_path
        for file_path in reader.list_python_files(abs_path)
        if not is_test_file_path(file_path)
    ]
    if not files:
        return {
            "path": abs_path,
            "status": "no_python_files",
            "findings": [],
        }

    try:
        from serenecode.adapters.vulture_adapter import VultureDeadCodeAnalyzer
    except ImportError as exc:
        return {
            "path": abs_path,
            "status": "unavailable",
            "message": str(exc),
            "findings": [],
        }

    analyzer = VultureDeadCodeAnalyzer()
    findings = analyzer.analyze_paths(tuple(files))
    return {
        "path": abs_path,
        "status": "ok",
        "findings": [
            {
                "symbol_name": finding.symbol_name,
                "file": finding.file_path,
                "line": finding.line,
                "symbol_type": finding.symbol_type,
                "confidence": finding.confidence,
                "message": finding.message,
                "guidance": (
                    "Ask the user whether this code should be removed or "
                    "allowlisted before changing it."
                ),
            }
            for finding in findings
        ],
    }


@icontract.require(
    lambda path: isinstance(path, str) and len(path) > 0,
    "path must be a non-empty string",
)
@icontract.require(
    lambda function: isinstance(function, str) and len(function) > 0,
    "function must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "passed" in result,
    "result must be a JSON-friendly CheckResponse dict",
)
def tool_uncovered(path: str, function: str) -> dict[str, object]:
    """Report Level 3 coverage findings for a single function.

    Requires the server to have been started with --allow-code-execution.

    Args:
        path: Path to the source file.
        function: Function name.

    Returns:
        A JSON-friendly CheckResponse-shaped dict scoped to the function.
    """
    return tool_check_function(path=path, function=function, level=3)


# ---------------------------------------------------------------------------
# Tool: serenecode_suggest_test
# ---------------------------------------------------------------------------


@icontract.require(
    lambda path: isinstance(path, str) and len(path) > 0,
    "path must be a non-empty string",
)
@icontract.require(
    lambda function: isinstance(function, str) and len(function) > 0,
    "function must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "suggestions" in result,
    "result must contain a 'suggestions' list",
)
def tool_suggest_test(path: str, function: str) -> dict[str, object]:
    """Return any test scaffold suggestion the coverage adapter generated.

    Runs Level 3 on the function and surfaces the `Detail.suggestion`
    field, which the coverage adapter populates with a runnable test
    scaffold for uncovered paths.

    Args:
        path: Path to the source file.
        function: Function name.

    Returns:
        A dict with `function`, `path`, and `suggestions` (list of strings).
    """
    response_dict = tool_check_function(path=path, function=function, level=3)
    findings = response_dict.get("findings", [])
    suggestions: list[str] = []
    if isinstance(findings, list):
        for f in findings:
            if isinstance(f, dict):
                s = f.get("suggestion")
                if isinstance(s, str) and s:
                    suggestions.append(s)
    return {
        "path": os.path.abspath(path),
        "function": function,
        "suggestions": suggestions,
    }


# ---------------------------------------------------------------------------
# Tool: serenecode_module_health
# ---------------------------------------------------------------------------


@icontract.require(
    lambda path: isinstance(path, str) and len(path) > 0,
    "path must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "file" in result,
    "result must be a dict with a 'file' key",
)
def tool_module_health(path: str) -> dict[str, object]:
    """Return module health metrics for a single file without running verification.

    Use proactively before and after editing to monitor whether changes
    improve or degrade module structure. No --allow-code-execution needed.

    Implements: REQ-033, REQ-034, INT-004

    Args:
        path: Path to the Python source file.

    Returns:
        A dict with metrics, per-metric status, and split suggestions.
    """
    import ast as _ast

    from serenecode.core.module_health import count_non_receiver_params, suggest_split_points

    abs_path = os.path.abspath(path)
    reader = LocalFileReader()
    # silent-except: file read errors should return a structured error, not crash the tool
    try:
        source = reader.read_file(abs_path)
    except Exception as exc:
        return {"file": abs_path, "error": f"Cannot read file: {exc}"}

    config = _load_config(abs_path)
    mh = config.module_health
    line_count = len(source.splitlines())

    # silent-except: syntax errors should return a structured error with partial metrics
    try:
        tree = _ast.parse(source)
    except SyntaxError as exc:
        return {"file": abs_path, "error": f"Syntax error: {exc}", "metrics": {"line_count": line_count}}

    metrics = _collect_health_metrics(tree, count_non_receiver_params)
    split_suggestions = suggest_split_points(source) if line_count > mh.file_length_warn else []

    return _format_health_response(abs_path, line_count, metrics, mh, split_suggestions)


def _collect_health_metrics(tree: object, count_params: object) -> dict[str, object]:
    """Walk AST and collect function/class metrics."""
    import ast as _ast

    largest_func: dict[str, object] = {"name": "", "lines": 0, "line": 0}
    max_params: dict[str, object] = {"name": "", "count": 0, "line": 0}
    function_count = 0
    class_count = 0
    largest_class: dict[str, object] = {"name": "", "method_count": 0, "line": 0}

    # Loop invariant: metrics reflect nodes walked so far
    for node in _ast.iter_child_nodes(tree):
        func_nodes: list[_ast.FunctionDef | _ast.AsyncFunctionDef] = []
        if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
            func_nodes.append(node)
        elif isinstance(node, _ast.ClassDef):
            class_count += 1
            methods = [c for c in node.body if isinstance(c, (_ast.FunctionDef, _ast.AsyncFunctionDef))]
            if len(methods) > largest_class["method_count"]:  # type: ignore[operator]
                largest_class = {"name": node.name, "method_count": len(methods), "line": node.lineno}
            func_nodes.extend(methods)

        for func in func_nodes:
            function_count += 1
            if func.end_lineno is not None:
                length = func.end_lineno - func.lineno + 1
                if length > largest_func["lines"]:  # type: ignore[operator]
                    largest_func = {"name": func.name, "lines": length, "line": func.lineno}
            pc = count_params(func)
            if pc > max_params["count"]:  # type: ignore[operator]
                max_params = {"name": func.name, "count": pc, "line": func.lineno}

    return {
        "function_count": function_count, "class_count": class_count,
        "largest_function": largest_func, "max_parameters": max_params,
        "largest_class": largest_class,
    }


def _format_health_response(
    abs_path: str, line_count: int, metrics: dict[str, object],
    mh: object, split_suggestions: list[object],
) -> dict[str, object]:
    """Build the tool_module_health response dict."""
    def _status(value: int, warn: int, error: int) -> str:
        if value > error:
            return "error"
        if value > warn:
            return "warning"
        return "ok"

    return {
        "file": abs_path,
        "metrics": {"line_count": line_count, **metrics},
        "status": {
            "file_length": _status(line_count, mh.file_length_warn, mh.file_length_error),
            "function_length": _status(
                metrics["largest_function"]["lines"],  # type: ignore[index]
                mh.function_length_warn, mh.function_length_error,
            ),
            "parameter_count": _status(
                metrics["max_parameters"]["count"],  # type: ignore[index]
                mh.parameter_count_warn, mh.parameter_count_error,
            ),
            "class_method_count": _status(
                metrics["largest_class"]["method_count"],  # type: ignore[index]
                mh.class_method_count_warn, mh.class_method_count_error,
            ),
        },
        "split_suggestions": split_suggestions,
    }
