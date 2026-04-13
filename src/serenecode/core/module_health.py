"""Module health checks for the verification pipeline.

This module implements file-length, function-length, parameter-count,
and class-method-count checks that run as part of Level 1 structural
verification. Each check uses a two-threshold model: advisory warning
(EXEMPT status) and hard error (FAILED status).

This is a core module — no I/O imports are permitted.

Implements: REQ-008, REQ-009, REQ-010, REQ-011, REQ-012, REQ-013,
REQ-014, REQ-015, REQ-016, REQ-017, REQ-018, REQ-019, REQ-020,
REQ-021, REQ-022, REQ-023, REQ-024, REQ-025, REQ-026, REQ-027
"""

from __future__ import annotations

import ast
import re

import icontract

from serenecode.config import SerenecodeConfig
from serenecode.models import (
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
)
from serenecode.source_discovery import SourceFile

_BANNER_PATTERN = re.compile(r"^#\s*[-=]{3,}\s*(.+?)\s*[-=]*\s*$")


@icontract.require(
    lambda file_path: isinstance(file_path, str),
    "file_path must be a string",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def _is_test_file_path(file_path: str) -> bool:
    """Return True when a path points to a test-only Python file."""
    normalized = file_path.replace("\\", "/")
    basename = normalized.rsplit("/", maxsplit=1)[-1]
    path_parts = normalized.split("/")
    return (
        "tests" in path_parts
        or basename.startswith("test_")
        or basename == "conftest.py"
    )


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list of suggestion strings",
)
def suggest_split_points(source: str) -> list[str]:
    """Identify natural module split points from AST and source structure.

    Implements: REQ-025, REQ-026, REQ-027
    """
    # silent-except: if the file can't parse, no suggestions to give
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    suggestions: list[str] = []

    # 1. Top-level classes with line span and method count
    # Loop invariant: suggestions contains class-based split hints for classes seen so far
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef) and node.end_lineno is not None:
            method_count = sum(
                1 for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            if method_count >= 3:
                suggestions.append(
                    f"Class '{node.name}' (lines {node.lineno}-{node.end_lineno}, "
                    f"{method_count} methods) could be its own module"
                )

    # 2. Groups of top-level functions sharing a common prefix
    func_names: list[tuple[str, int]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_names.append((node.name, node.lineno))

    prefix_groups: dict[str, list[tuple[str, int]]] = {}
    # Loop invariant: prefix_groups maps each prefix to its accumulated function list
    for name, lineno in func_names:
        parts = name.split("_")
        if len(parts) >= 2 and not name.startswith("_"):
            prefix = parts[0]
            prefix_groups.setdefault(prefix, []).append((name, lineno))

    # Loop invariant: suggestions extended with prefix groups of 3+ functions
    for prefix, funcs in sorted(prefix_groups.items()):
        if len(funcs) >= 3:
            lines = sorted(ln for _, ln in funcs)
            suggestions.append(
                f"Functions '{prefix}_*' ({len(funcs)} functions, "
                f"lines {lines[0]}-{lines[-1]}) could form a '{prefix}' module"
            )

    # 3. Banner comments suggesting logical sections
    # Loop invariant: suggestions extended with banner comments found in lines[0..i]
    for i, line in enumerate(source.splitlines(), start=1):
        match = _BANNER_PATTERN.match(line.strip())
        if match:
            section_name = match.group(1).strip()
            if section_name:
                suggestions.append(
                    f"Banner '{section_name}' at line {i} suggests a logical boundary"
                )

    return suggestions


def _build_file_length_suggestion(
    line_count: int,
    source: str,
    warn_threshold: int,
) -> str:
    """Build the suggestion string for a file-length finding."""
    split_points = suggest_split_points(source)
    base = (
        f"Split this module into smaller, focused modules. Aim for modules "
        f"under {warn_threshold} lines with a single clear responsibility."
    )
    if split_points:
        return base + " Potential split points: " + "; ".join(split_points) + "."
    return (
        base + " Look for: classes that could be standalone modules, "
        "groups of related functions that share a prefix or domain concept, "
        "or sections separated by comment banners."
    )


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_file_length(
    source_files: tuple[SourceFile, ...],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check file lengths against module health thresholds.

    Implements: REQ-008, REQ-009, REQ-010, REQ-011, REQ-012
    """
    mh = config.module_health
    results: list[FunctionResult] = []

    # Loop invariant: results contains file-length findings for source_files[0..i]
    for sf in source_files:
        if _is_test_file_path(sf.file_path):
            continue
        line_count = len(sf.source.splitlines())
        if line_count <= mh.file_length_warn:
            continue

        suggestion = _build_file_length_suggestion(
            line_count, sf.source, mh.file_length_warn,
        )

        if line_count > mh.file_length_error:
            results.append(FunctionResult(
                function="<module>",
                file=sf.file_path,
                line=1,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="module_health",
                    finding_type="file_length",
                    message=(
                        f"File has {line_count} lines, exceeding the maximum of "
                        f"{mh.file_length_error} lines."
                    ),
                    suggestion=suggestion,
                ),),
            ))
        else:
            results.append(FunctionResult(
                function="<module>",
                file=sf.file_path,
                line=1,
                level_requested=1,
                level_achieved=1,
                status=CheckStatus.EXEMPT,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="module_health",
                    finding_type="file_length",
                    message=(
                        f"File has {line_count} lines (warning threshold: "
                        f"{mh.file_length_warn}, error threshold: "
                        f"{mh.file_length_error})."
                    ),
                    suggestion=suggestion,
                ),),
            ))

    return results


def _collect_func_nodes(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Collect top-level functions and class methods from an AST."""
    func_nodes: list[ast.FunctionDef | ast.AsyncFunctionDef] = []
    # Loop invariant: func_nodes contains checkable defs from nodes walked so far
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_nodes.append(node)
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    func_nodes.append(child)
    return func_nodes


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_function_length(
    source_files: tuple[SourceFile, ...],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check function lengths against module health thresholds.

    Implements: REQ-013, REQ-014, REQ-015, REQ-016
    """
    mh = config.module_health
    results: list[FunctionResult] = []

    # Loop invariant: results contains function-length findings for source_files[0..i]
    for sf in source_files:
        if _is_test_file_path(sf.file_path):
            continue
        # silent-except: unparseable files are handled by L1 structural; skip here
        try:
            tree = ast.parse(sf.source)
        except SyntaxError:
            continue

        for func in _collect_func_nodes(tree):
            if func.end_lineno is None:
                continue
            length = func.end_lineno - func.lineno + 1
            if length > mh.function_length_error:
                results.append(FunctionResult(
                    function=func.name,
                    file=sf.file_path,
                    line=func.lineno,
                    level_requested=1,
                    level_achieved=0,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="module_health",
                        finding_type="function_length",
                        message=(
                            f"Function '{func.name}' has {length} lines, "
                            f"exceeding the maximum of "
                            f"{mh.function_length_error} lines."
                        ),
                        suggestion=(
                            "Extract logical steps into helper functions. Look for: "
                            "comment-delimited sections, nested loops or conditionals "
                            "that perform a distinct sub-task, or setup/teardown code "
                            "that could be a context manager."
                        ),
                    ),),
                ))
            elif length > mh.function_length_warn:
                results.append(FunctionResult(
                    function=func.name,
                    file=sf.file_path,
                    line=func.lineno,
                    level_requested=1,
                    level_achieved=1,
                    status=CheckStatus.EXEMPT,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="module_health",
                        finding_type="function_length",
                        message=(
                            f"Function '{func.name}' has {length} lines "
                            f"(warning threshold: {mh.function_length_warn}, "
                            f"error threshold: {mh.function_length_error})."
                        ),
                        suggestion=(
                            f"Consider decomposing this function. Functions under "
                            f"{mh.function_length_warn} lines are easier to test, "
                            f"review, and reason about."
                        ),
                    ),),
                ))

    return results


def _has_allow_many_params(source: str, node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if `# allow-many-params:` appears on or above the def line."""
    lines = source.splitlines()
    # Loop invariant: checked target lines for opt-out comment
    for line_no in (node.lineno, node.lineno - 1):
        idx = line_no - 1
        if 0 <= idx < len(lines) and "allow-many-params:" in lines[idx]:
            return True
    return False


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: result >= 0,
    "parameter count must be non-negative",
)
def count_non_receiver_params(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> int:
    """Count non-self/cls parameters of a function."""
    args = node.args
    params = list(args.posonlyargs) + list(args.args)
    if params and params[0].arg in ("self", "cls"):
        params = params[1:]
    count = len(params) + len(args.kwonlyargs)
    if args.vararg is not None:
        count += 1
    if args.kwarg is not None:
        count += 1
    return count


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_parameter_count(
    source_files: tuple[SourceFile, ...],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check function parameter counts against module health thresholds.

    Implements: REQ-017, REQ-018, REQ-019, REQ-020
    """
    mh = config.module_health
    results: list[FunctionResult] = []

    # Loop invariant: results contains parameter-count findings for source_files[0..i]
    for sf in source_files:
        if _is_test_file_path(sf.file_path):
            continue
        # silent-except: unparseable files are handled by L1 structural; skip here
        try:
            tree = ast.parse(sf.source)
        except SyntaxError:
            continue

        for func in _collect_func_nodes(tree):
            count = count_non_receiver_params(func)
            if _has_allow_many_params(sf.source, func):
                continue
            if count > mh.parameter_count_error:
                results.append(FunctionResult(
                    function=func.name,
                    file=sf.file_path,
                    line=func.lineno,
                    level_requested=1,
                    level_achieved=0,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="module_health",
                        finding_type="parameter_count",
                        message=(
                            f"Function '{func.name}' has {count} parameters "
                            f"(excluding self/cls), exceeding the maximum of "
                            f"{mh.parameter_count_error}."
                        ),
                        suggestion=(
                            "Group related parameters into a dataclass or TypedDict. "
                            "Consider the Parameter Object pattern: identify parameters "
                            "that are always passed together and bundle them into a "
                            "single config or request object."
                        ),
                    ),),
                ))
            elif count > mh.parameter_count_warn:
                results.append(FunctionResult(
                    function=func.name,
                    file=sf.file_path,
                    line=func.lineno,
                    level_requested=1,
                    level_achieved=1,
                    status=CheckStatus.EXEMPT,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="module_health",
                        finding_type="parameter_count",
                        message=(
                            f"Function '{func.name}' has {count} parameters "
                            f"(warning threshold: {mh.parameter_count_warn}, "
                            f"error threshold: {mh.parameter_count_error})."
                        ),
                        suggestion=(
                            "Consider reducing parameters. Functions with fewer "
                            "arguments are easier to call correctly and test "
                            "exhaustively."
                        ),
                    ),),
                ))

    return results


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_class_method_count(
    source_files: tuple[SourceFile, ...],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check class method counts against module health thresholds.

    Implements: REQ-021, REQ-022, REQ-023, REQ-024
    """
    mh = config.module_health
    results: list[FunctionResult] = []

    # Loop invariant: results contains class-size findings for source_files[0..i]
    for sf in source_files:
        if _is_test_file_path(sf.file_path):
            continue
        # silent-except: unparseable files are handled by L1 structural; skip here
        try:
            tree = ast.parse(sf.source)
        except SyntaxError:
            continue

        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            method_count = sum(
                1 for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            )
            if method_count > mh.class_method_count_error:
                results.append(FunctionResult(
                    function=node.name,
                    file=sf.file_path,
                    line=node.lineno,
                    level_requested=1,
                    level_achieved=0,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="module_health",
                        finding_type="class_method_count",
                        message=(
                            f"Class '{node.name}' has {method_count} methods, "
                            f"exceeding the maximum of "
                            f"{mh.class_method_count_error}."
                        ),
                        suggestion=(
                            "This class likely has multiple responsibilities. Extract "
                            "cohesive groups of methods into separate classes. Look for: "
                            "methods that share a common prefix, methods that only access "
                            "a subset of instance attributes, or methods that could be "
                            "standalone functions."
                        ),
                    ),),
                ))
            elif method_count > mh.class_method_count_warn:
                results.append(FunctionResult(
                    function=node.name,
                    file=sf.file_path,
                    line=node.lineno,
                    level_requested=1,
                    level_achieved=1,
                    status=CheckStatus.EXEMPT,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="module_health",
                        finding_type="class_method_count",
                        message=(
                            f"Class '{node.name}' has {method_count} methods "
                            f"(warning threshold: {mh.class_method_count_warn}, "
                            f"error threshold: {mh.class_method_count_error})."
                        ),
                        suggestion=(
                            "Consider whether this class has a single clear "
                            "responsibility. Classes with fewer methods are easier "
                            "to understand and test."
                        ),
                    ),),
                ))

    return results
