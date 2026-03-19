"""Serenecode — formal verification framework for AI-generated Python code.

This module provides the public API surface for Serenecode. It enables
programmatic access to project initialization and all verification levels.
This is a composition root — it wires adapters to ports and delegates
to core logic.
"""

from __future__ import annotations

import time

from serenecode.adapters.local_fs import LocalFileReader, LocalFileWriter
from serenecode.checker.structural import check_structural as _check_structural
from serenecode.config import parse_serenecode_md
from serenecode.init import InitResult, initialize_project
from serenecode.models import CheckResult, FunctionResult, make_check_result


def init(path: str = ".", template: str = "default") -> InitResult:
    """Initialize a Serenecode project.

    Args:
        path: Project root directory.
        template: Template name ('default', 'strict', or 'minimal').

    Returns:
        An InitResult describing what was created.
    """
    reader = LocalFileReader()
    writer = LocalFileWriter()
    return initialize_project(
        directory=path,
        template=template,
        file_reader=reader,
        file_writer=writer,
    )


def check(path: str = ".", level: int = 5, format: str = "human") -> CheckResult:
    """Run verification up to the specified level.

    Args:
        path: File or directory to check.
        level: Maximum verification level (1-5).
        format: Output format (unused in library API, results are structured).

    Returns:
        A CheckResult with all findings.
    """
    return _run_check(path, level)


def check_structural(path: str = ".") -> CheckResult:
    """Run only the structural checker (Level 1).

    Args:
        path: File or directory to check.

    Returns:
        A CheckResult with structural findings only.
    """
    return _run_check(path, level=1)


def check_types(path: str = ".") -> CheckResult:
    """Run type checking (Level 2). Not yet implemented.

    Args:
        path: File or directory to check.

    Returns:
        A CheckResult (currently empty — Level 2 not implemented).
    """
    return make_check_result((), level_requested=2, duration_seconds=0.0)


def check_properties(path: str = ".") -> CheckResult:
    """Run property-based testing (Level 3). Not yet implemented.

    Args:
        path: File or directory to check.

    Returns:
        A CheckResult (currently empty — Level 3 not implemented).
    """
    return make_check_result((), level_requested=3, duration_seconds=0.0)


def check_symbolic(path: str = ".") -> CheckResult:
    """Run symbolic verification (Level 4). Not yet implemented.

    Args:
        path: File or directory to check.

    Returns:
        A CheckResult (currently empty — Level 4 not implemented).
    """
    return make_check_result((), level_requested=4, duration_seconds=0.0)


def check_compositional(path: str = ".") -> CheckResult:
    """Run compositional verification (Level 5). Not yet implemented.

    Args:
        path: File or directory to check.

    Returns:
        A CheckResult (currently empty — Level 5 not implemented).
    """
    return make_check_result((), level_requested=5, duration_seconds=0.0)


def status(path: str = ".") -> CheckResult:
    """Show verification status of the codebase.

    Args:
        path: File or directory to check.

    Returns:
        A CheckResult showing current verification state.
    """
    return _run_check(path, level=1)


def _run_check(path: str, level: int) -> CheckResult:
    """Internal helper to run checks at a given level.

    Args:
        path: File or directory to check.
        level: Maximum verification level.

    Returns:
        Aggregated CheckResult.
    """
    reader = LocalFileReader()
    start = time.monotonic()

    # Load config
    config_content: str | None = None
    serenecode_path = _find_serenecode_md(path, reader)
    if serenecode_path:
        config_content = reader.read_file(serenecode_path)

    if config_content:
        config = parse_serenecode_md(config_content)
    else:
        from serenecode.config import default_config
        config = default_config()

    # List files
    files = reader.list_python_files(path)

    all_func_results: list[FunctionResult] = []

    # Loop invariant: all_func_results contains results for files[0..i]
    for file_path in files:
        try:
            source = reader.read_file(file_path)
        except Exception:
            continue

        module_path = file_path
        if "src/serenecode/" in file_path:
            module_path = file_path.split("src/serenecode/")[-1]

        result = _check_structural(source, config, module_path, file_path)
        all_func_results.extend(result.results)

    elapsed = time.monotonic() - start
    return make_check_result(
        tuple(all_func_results),
        level_requested=level,
        duration_seconds=elapsed,
    )


def _find_serenecode_md(path: str, reader: LocalFileReader) -> str | None:
    """Find SERENECODE.md by searching up from the given path."""
    import os

    candidates = [
        os.path.join(path, "SERENECODE.md"),
        "SERENECODE.md",
    ]

    current = os.path.abspath(path)
    # Loop invariant: checked directories from path up to current
    for _ in range(10):
        candidate = os.path.join(current, "SERENECODE.md")
        if candidate not in candidates:
            candidates.append(candidate)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    # Loop invariant: checked candidates[0..i]
    for candidate in candidates:
        if reader.file_exists(candidate):
            return candidate

    return None
