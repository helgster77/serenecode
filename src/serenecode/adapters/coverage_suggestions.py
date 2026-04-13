"""Test suggestion and generation helpers for coverage analysis.

This module contains the functions that analyze uncovered code paths and
generate test suggestions with mock necessity assessments.  They were
extracted from coverage_adapter.py to keep both modules under the 1000-line
limit.

This is an adapter module \u2014 it handles AST-based analysis for test
generation and is exempt from full contract requirements.
"""

from __future__ import annotations

import ast

import icontract

from serenecode.contracts.predicates import is_non_empty_string
from serenecode.ports.coverage_analyzer import (
    CoverageSuggestion,
    MockDependency,
)

# Import _FunctionCoverage from the adapter so _generate_suggestions can
# accept it.  This is a private type shared between sibling adapter modules.
from serenecode.adapters.coverage_adapter import _FunctionCoverage

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
    # and if TYPE_CHECKING blocks \u2014 not just top-level imports.
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
    return "internal code \u2014 can use real implementation"


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
