"""Serenecode — formal verification framework for AI-generated Python code.

This module provides the public API surface for Serenecode. It enables
programmatic access to project initialization and all verification levels.
This is a composition root — it wires adapters to ports and delegates
to core logic via the pipeline.
"""

from __future__ import annotations

import os

import icontract

from serenecode.adapters.local_fs import LocalFileReader, LocalFileWriter
from serenecode.config import default_config, parse_serenecode_md
from serenecode.contracts.predicates import (
    is_non_empty_string,
    is_valid_file_path_string,
    is_valid_template_name,
    is_valid_verification_level,
)
from serenecode.core.exceptions import UnsafeCodeExecutionError
from serenecode.core.pipeline import run_pipeline
from serenecode.init import InitResult, initialize_project
from serenecode.models import CheckResult
from serenecode.ports.dead_code_analyzer import DeadCodeAnalyzer
from serenecode.source_discovery import (
    build_source_files,
    discover_test_file_stems,
    find_serenecode_md,
    find_spec_md,
)

__all__ = [
    "init",
    "check",
    "check_structural",
    "check_types",
    "check_coverage",
    "check_properties",
    "check_symbolic",
    "check_compositional",
    "status",
    "CheckResult",
    "InitResult",
]

_TRUST_REQUIRED_MESSAGE = (
    "Levels 3-6 import and execute project modules. "
    "Only run on trusted code with allow_code_execution=True / --allow-code-execution."
)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda path: is_valid_file_path_string(path), "path must be a valid path string")
@icontract.require(lambda template: is_valid_template_name(template), "template must be a valid template name")
@icontract.ensure(lambda result: result.template_used in ("default", "strict", "minimal"), "result must report a valid template")
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


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda path: is_valid_file_path_string(path), "path must be a valid path string")
@icontract.require(lambda level: is_valid_verification_level(level), "level must be between 1 and 6")
@icontract.ensure(lambda level, result: result.level_requested == level, "result must report the requested level")
def check(
    path: str = ".",
    level: int = 6,
    allow_code_execution: bool = False,
) -> CheckResult:
    """Run verification up to the specified level.

    Uses the full pipeline (L1 through the requested level),
    wiring adapters for each level that has a backend available.

    Args:
        path: File or directory to check.
        level: Maximum verification level (1-6).
        allow_code_execution: Explicit opt-in for Levels 3-6, which import
            and execute project modules.

    Returns:
        A CheckResult with all findings.
    """
    return _run_check(path, level, allow_code_execution=allow_code_execution)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda path: is_valid_file_path_string(path), "path must be a valid path string")
@icontract.ensure(lambda result: result.level_requested == 1, "structural check must request level 1")
def check_structural(path: str = ".") -> CheckResult:
    """Run only the structural checker (Level 1).

    Args:
        path: File or directory to check.

    Returns:
        A CheckResult with structural findings only.
    """
    return _run_check(path, level=1)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda path: is_valid_file_path_string(path), "path must be a valid path string")
@icontract.ensure(lambda result: result.level_requested == 2, "type check must request level 2")
def check_types(path: str = ".") -> CheckResult:
    """Run the Level 2 type checker."""
    return _run_check(path, level=2)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda path: is_valid_file_path_string(path), "path must be a valid path string")
@icontract.ensure(lambda result: result.level_requested == 3, "coverage check must request level 3")
def check_coverage(
    path: str = ".",
    allow_code_execution: bool = False,
) -> CheckResult:
    """Run verification through Level 3 (coverage analysis).

    Args:
        path: File or directory to check.
        allow_code_execution: Explicit opt-in because Levels 3-6 import and
            execute project modules.

    Returns:
        CheckResult through Level 3.
    """
    return _run_check(path, level=3, allow_code_execution=allow_code_execution)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda path: is_valid_file_path_string(path), "path must be a valid path string")
@icontract.ensure(lambda result: result.level_requested == 4, "properties check must request level 4")
def check_properties(
    path: str = ".",
    allow_code_execution: bool = False,
) -> CheckResult:
    """Run property-based verification through Level 4.

    Args:
        path: File or directory to check.
        allow_code_execution: Explicit opt-in because Level 4 imports and
            executes project modules.

    Returns:
        A CheckResult with findings through Level 4.
    """
    return _run_check(path, level=4, allow_code_execution=allow_code_execution)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda path: is_valid_file_path_string(path), "path must be a valid path string")
@icontract.ensure(lambda result: result.level_requested == 5, "symbolic check must request level 5")
def check_symbolic(
    path: str = ".",
    allow_code_execution: bool = False,
) -> CheckResult:
    """Run symbolic verification through Level 5.

    Args:
        path: File or directory to check.
        allow_code_execution: Explicit opt-in because Levels 3-6 import and
            execute project modules.

    Returns:
        A CheckResult with findings through Level 5.
    """
    return _run_check(path, level=5, allow_code_execution=allow_code_execution)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda path: is_valid_file_path_string(path), "path must be a valid path string")
@icontract.ensure(lambda result: result.level_requested == 6, "compositional check must request level 6")
def check_compositional(
    path: str = ".",
    allow_code_execution: bool = False,
) -> CheckResult:
    """Run compositional verification through Level 6.

    Args:
        path: File or directory to check.
        allow_code_execution: Explicit opt-in because Levels 3-6 import and
            execute project modules during the full pipeline.

    Returns:
        A CheckResult with findings through Level 6.
    """
    return _run_check(path, level=6, allow_code_execution=allow_code_execution)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda path: is_valid_file_path_string(path), "path must be a valid path string")
@icontract.ensure(lambda result: result.level_requested == 1, "status check must request level 1")
def status(path: str = ".") -> CheckResult:
    """Show verification status of the codebase.

    Args:
        path: File or directory to check.

    Returns:
        A CheckResult showing current verification state.
    """
    return _run_check(path, level=1)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda path: is_valid_file_path_string(path), "path must be a valid path string")
@icontract.require(lambda level: is_valid_verification_level(level), "level must be between 1 and 6")
@icontract.ensure(lambda level, result: result.level_requested == level, "result must report the requested level")
def _run_check(
    path: str,
    level: int,
    allow_code_execution: bool = False,
) -> CheckResult:
    """Internal helper to run checks via the real pipeline.

    Args:
        path: File or directory to check.
        level: Maximum verification level (1-6).
        allow_code_execution: Explicit opt-in for Levels 3-6, which import
            and execute project modules.

    Returns:
        Aggregated CheckResult.
    """
    if level >= 3 and not allow_code_execution:
        raise UnsafeCodeExecutionError(_TRUST_REQUIRED_MESSAGE)

    reader = LocalFileReader()

    # Load config
    serenecode_path = find_serenecode_md(path, reader)
    if serenecode_path:
        config_content = reader.read_file(serenecode_path)
        config = parse_serenecode_md(config_content)
    else:
        config = default_config()

    # List and build source files
    files = reader.list_python_files(path)
    source_files = build_source_files(files, reader, path)

    # Wire up adapters for higher levels
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

    test_stems = discover_test_file_stems(path, reader)
    spec_content, test_sources = _load_spec_inputs(path, reader)
    return run_pipeline(
        source_files=source_files,
        level=level,
        start_level=1,
        config=config,
        type_checker=type_checker,
        coverage_analyzer=coverage_analyzer,
        property_tester=property_tester,
        symbolic_checker=symbolic_checker,
        dead_code_analyzer=dead_code_analyzer,
        known_test_stems=test_stems,
        spec_content=spec_content,
        test_sources=test_sources,
    )


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda reader: reader is not None, "reader must be provided")
@icontract.ensure(
    lambda result: isinstance(result, tuple) and len(result) == 2,
    "result must be a (spec_content, test_sources) pair",
)
def _load_spec_inputs(
    path: str,
    reader: LocalFileReader,
) -> tuple[str | None, tuple[tuple[str, str], ...]]:
    """Load auto-discovered SPEC.md content and associated test sources."""
    spec_path = find_spec_md(path, reader)
    if spec_path is None:
        return None, ()

    spec_content = reader.read_file(spec_path)
    project_root = os.path.dirname(spec_path)
    tests_dir = os.path.join(project_root, "tests")
    if not os.path.isdir(tests_dir):
        return spec_content, ()

    try:
        test_files = reader.list_python_files(tests_dir)
    except Exception:
        return spec_content, ()

    test_sources: list[tuple[str, str]] = []
    # Loop invariant: test_sources contains collected (path, source) pairs from test_files[0..i]
    for test_file in test_files:
        try:
            test_sources.append((test_file, reader.read_file(test_file)))
        except Exception:
            continue
    return spec_content, tuple(test_sources)
