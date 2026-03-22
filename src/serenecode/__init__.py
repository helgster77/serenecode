"""Serenecode — formal verification framework for AI-generated Python code.

This module provides the public API surface for Serenecode. It enables
programmatic access to project initialization and all verification levels.
This is a composition root — it wires adapters to ports and delegates
to core logic via the pipeline.
"""

from __future__ import annotations

import os

from serenecode.adapters.local_fs import LocalFileReader, LocalFileWriter
from serenecode.config import default_config, parse_serenecode_md
from serenecode.core.pipeline import SourceFile, run_pipeline
from serenecode.init import InitResult, initialize_project
from serenecode.models import CheckResult


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


def check(path: str = ".", level: int = 5) -> CheckResult:
    """Run verification up to the specified level.

    Uses the full pipeline (L1 through the requested level),
    wiring adapters for each level that has a backend available.

    Args:
        path: File or directory to check.
        level: Maximum verification level (1-5).

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


def status(path: str = ".") -> CheckResult:
    """Show verification status of the codebase.

    Args:
        path: File or directory to check.

    Returns:
        A CheckResult showing current verification state.
    """
    return _run_check(path, level=1)


def _run_check(path: str, level: int) -> CheckResult:
    """Internal helper to run checks via the real pipeline.

    Args:
        path: File or directory to check.
        level: Maximum verification level (1-5).

    Returns:
        Aggregated CheckResult.
    """
    reader = LocalFileReader()

    # Load config
    serenecode_path = _find_serenecode_md(path, reader)
    if serenecode_path:
        config_content = reader.read_file(serenecode_path)
        config = parse_serenecode_md(config_content)
    else:
        config = default_config()

    # List and build source files
    files = reader.list_python_files(path)
    source_files = _build_source_files(files, reader)

    # Wire up adapters for higher levels
    type_checker = None
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
            from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
            property_tester = HypothesisPropertyTester()
        except ImportError:
            pass

    if level >= 4:
        try:
            from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
            symbolic_checker = CrossHairSymbolicChecker()
        except ImportError:
            pass

    return run_pipeline(
        source_files=source_files,
        level=level,
        config=config,
        type_checker=type_checker,
        property_tester=property_tester,
        symbolic_checker=symbolic_checker,
    )


def _build_source_files(
    file_paths: list[str],
    reader: LocalFileReader,
) -> tuple[SourceFile, ...]:
    """Build SourceFile objects from file paths.

    Args:
        file_paths: Paths to Python files.
        reader: File reader for reading contents.

    Returns:
        Tuple of SourceFile objects.
    """
    source_files: list[SourceFile] = []

    # Loop invariant: source_files contains SourceFile for file_paths[0..i]
    for fp in file_paths:
        try:
            source = reader.read_file(fp)
        except Exception:
            continue

        # Derive module path for architecture checks
        module_path = fp
        if "src/serenecode/" in fp:
            module_path = fp.split("src/serenecode/")[-1]

        # Derive importable module name
        importable = _derive_importable_module(fp)

        source_files.append(SourceFile(
            file_path=fp,
            module_path=module_path,
            source=source,
            importable_module=importable,
        ))

    return tuple(source_files)


def _derive_importable_module(file_path: str) -> str | None:
    """Derive an importable Python module path from a file path.

    Args:
        file_path: Path to a Python file.

    Returns:
        Importable module path, or None if it can't be determined.
    """
    fp = file_path.replace(os.sep, "/")

    if "/src/" in fp:
        module_part = fp.split("/src/")[-1]
    elif fp.startswith("src/"):
        module_part = fp[4:]
    else:
        module_part = fp

    if module_part.endswith(".py"):
        module_part = module_part[:-3]
    else:
        return None

    module_path = module_part.replace("/", ".")

    if module_path.endswith(".__init__"):
        module_path = module_path[:-9]

    return module_path if module_path else None


def _find_serenecode_md(path: str, reader: LocalFileReader) -> str | None:
    """Find SERENECODE.md by searching up from the given path."""
    candidates: list[str] = []

    current = os.path.abspath(path)
    # Loop invariant: candidates contains checked paths from path upward
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
