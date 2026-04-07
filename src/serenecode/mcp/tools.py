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
from serenecode.checker.spec_traceability import (
    check_spec_traceability,
    extract_implementations,
    extract_spec_requirements,
    extract_verifications,
    validate_spec,
)
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
from serenecode.models import CheckResult, CheckStatus
from serenecode.source_discovery import (
    build_source_files,
    determine_context_root,
    discover_test_file_stems,
    find_serenecode_md,
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
    lambda project_root: isinstance(project_root, str) and len(project_root) > 0,
    "project_root must be a non-empty string",
)
@icontract.require(
    lambda level: isinstance(level, int) and 1 <= level <= 6,
    "level must be 1-6",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple) and len(result) == 2,
    "result must be a (config, source_files) pair",
)
def _build_pipeline_for(
    project_root: str,
    level: int,
) -> tuple[SerenecodeConfig, tuple[SourceFile, ...]]:
    """Load config and build the SourceFile tuple for the entire project root."""
    del level  # accepted for symmetry with the file-scoped helper
    reader = LocalFileReader()
    config = _load_config(project_root)
    files = reader.list_python_files(project_root)
    source_files = build_source_files(files, reader, project_root)
    return config, source_files


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

    return {
        "type_checker": type_checker,
        "coverage_analyzer": coverage_analyzer,
        "property_tester": property_tester,
        "symbolic_checker": symbolic_checker,
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
    """Run the verification pipeline on a directory or file path.

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
    adapters = _wire_adapters(level)
    result = run_pipeline(
        source_files=source_files,
        level=level,
        start_level=1,
        config=config,
        known_test_stems=test_stems,
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
    adapters = _wire_adapters(level)
    result = run_pipeline(
        source_files=source_files,
        level=level,
        start_level=1,
        config=config,
        known_test_stems=test_stems,
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
    """Run the verification pipeline scoped to a single function in a single file.

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
    if level == 1:
        # Fast path: structural-only on a single file
        sf = source_files[0]
        result = check_structural(sf.source, config, sf.module_path, sf.file_path)
    else:
        reader = LocalFileReader()
        test_stems = discover_test_file_stems(project_root, reader)
        adapters = _wire_adapters(level)
        result = run_pipeline(
            source_files=source_files,
            level=level,
            start_level=1,
            config=config,
            known_test_stems=test_stems,
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
# Tool: serenecode_validate_spec
# ---------------------------------------------------------------------------


@icontract.require(
    lambda spec_file: isinstance(spec_file, str) and len(spec_file) > 0,
    "spec_file must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "passed" in result,
    "result must be a JSON-friendly CheckResponse dict",
)
def tool_validate_spec(spec_file: str) -> dict[str, object]:
    """Validate a SPEC.md for SereneCode readiness.

    Args:
        spec_file: Path to the SPEC.md file.

    Returns:
        A JSON-friendly dict shaped as a CheckResponse for the spec.
    """
    reader = LocalFileReader()
    content = reader.read_file(spec_file)
    result = validate_spec(content)
    response = to_check_response(result)
    return response_to_dict(response)


# ---------------------------------------------------------------------------
# Tool: serenecode_list_reqs
# ---------------------------------------------------------------------------


@icontract.require(
    lambda spec_file: isinstance(spec_file, str) and len(spec_file) > 0,
    "spec_file must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "req_ids" in result and "count" in result,
    "result must contain req_ids and count fields",
)
def tool_list_reqs(spec_file: str) -> dict[str, object]:
    """List all REQ-xxx identifiers found in a SPEC.md file.

    Args:
        spec_file: Path to the SPEC.md file.

    Returns:
        A dict with `req_ids` (sorted list of strings) and `count`.
    """
    reader = LocalFileReader()
    content = reader.read_file(spec_file)
    req_ids = sorted(extract_spec_requirements(content))
    return {
        "spec_file": os.path.abspath(spec_file),
        "req_ids": req_ids,
        "count": len(req_ids),
    }


# ---------------------------------------------------------------------------
# Tool: serenecode_req_status
# ---------------------------------------------------------------------------


@icontract.require(
    lambda spec_file: isinstance(spec_file, str) and len(spec_file) > 0,
    "spec_file must be a non-empty string",
)
@icontract.require(
    lambda req_id: (req_id is None) or (isinstance(req_id, str) and len(req_id) > 0),
    "req_id must be None or a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "reqs" in result,
    "result must contain a 'reqs' list",
)
def tool_req_status(
    spec_file: str,
    req_id: str | None = None,
) -> dict[str, object]:
    """Report implementation and verification status for spec requirements.

    Scans every Python file under the project root that contains `spec_file`
    for `Implements: REQ-xxx` and `Verifies: REQ-xxx` references and reports
    where each requirement is implemented and tested. Source files are
    auto-discovered — you do NOT pass `src_path` or `tests_path` separately.

    Args:
        spec_file: Path to SPEC.md.
        req_id: Optional requirement identifier (e.g. "REQ-042"). When
            omitted, the response includes the status of every REQ in the
            spec. When provided, the response is filtered to that one REQ
            (and the `reqs` list will have at most one entry).

    Returns:
        A dict with:
            - `spec_file`: absolute path to the spec
            - `project_root`: where source/test files were scanned
            - `reqs`: list of {req_id, exists_in_spec, status, implementations,
              verifications} entries. `status` is one of "complete",
              "implemented_only", "tested_only", or "orphan".
    """
    reader = LocalFileReader()
    spec_content = reader.read_file(spec_file)
    spec_reqs = extract_spec_requirements(spec_content)
    project_root = _resolve_root(os.path.dirname(spec_file))
    files = reader.list_python_files(project_root)

    # Collect all (req_id → list of refs) for implementations and verifications
    impls_by_req: dict[str, list[dict[str, object]]] = {}
    verifs_by_req: dict[str, list[dict[str, object]]] = {}
    for f in files:
        try:
            source = reader.read_file(f)
        except OSError:
            continue
        for func_name, found_req, line in extract_implementations(source):
            impls_by_req.setdefault(found_req, []).append(
                {"file": f, "function": func_name, "line": line},
            )
        for func_name, found_req, line in extract_verifications(source):
            verifs_by_req.setdefault(found_req, []).append(
                {"file": f, "function": func_name, "line": line},
            )

    # Determine which REQ ids to report on. The union of (spec ∪ found in code)
    # so we surface code-side orphans (REQs in code that aren't in the spec) too.
    candidate_ids: set[str]
    if req_id is not None:
        candidate_ids = {req_id}
    else:
        candidate_ids = set(spec_reqs) | set(impls_by_req.keys()) | set(verifs_by_req.keys())

    reqs: list[dict[str, object]] = []
    for rid in sorted(candidate_ids):
        impls = impls_by_req.get(rid, [])
        verifs = verifs_by_req.get(rid, [])
        has_impl = len(impls) > 0
        has_test = len(verifs) > 0
        if has_impl and has_test:
            status = "complete"
        elif has_impl:
            status = "implemented_only"
        elif has_test:
            status = "tested_only"
        else:
            status = "orphan"
        reqs.append({
            "req_id": rid,
            "exists_in_spec": rid in spec_reqs,
            "status": status,
            "implementations": impls,
            "verifications": verifs,
        })

    return {
        "spec_file": os.path.abspath(spec_file),
        "project_root": project_root,
        "reqs": reqs,
    }


# ---------------------------------------------------------------------------
# Tool: serenecode_orphans
# ---------------------------------------------------------------------------


@icontract.require(
    lambda spec_file: isinstance(spec_file, str) and len(spec_file) > 0,
    "spec_file must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "unimplemented" in result and "untested" in result,
    "result must contain unimplemented and untested lists",
)
def tool_orphans(spec_file: str) -> dict[str, object]:
    """List REQs in SPEC.md that have no implementation and/or no test.

    Args:
        spec_file: Path to SPEC.md.

    Returns:
        A dict with `unimplemented` (REQs with no `Implements:` reference)
        and `untested` (REQs with no `Verifies:` reference).
    """
    reader = LocalFileReader()
    spec_content = reader.read_file(spec_file)
    spec_reqs = extract_spec_requirements(spec_content)
    project_root = _resolve_root(os.path.dirname(spec_file))
    files = reader.list_python_files(project_root)

    implemented: set[str] = set()
    tested: set[str] = set()
    for f in files:
        try:
            source = reader.read_file(f)
        except OSError:
            continue
        for _func_name, req_id, _line in extract_implementations(source):
            implemented.add(req_id)
        for _func_name, req_id, _line in extract_verifications(source):
            tested.add(req_id)

    unimplemented = sorted(spec_reqs - implemented)
    untested = sorted(spec_reqs - tested)
    return {
        "unimplemented": unimplemented,
        "untested": untested,
    }


# ---------------------------------------------------------------------------
# Tool: serenecode_uncovered
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
