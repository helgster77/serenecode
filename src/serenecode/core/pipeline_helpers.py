"""Helper functions for the verification pipeline.

Dead-code analysis and test-existence checking utilities used by
the main pipeline orchestrator.

This is a core module — no I/O imports are permitted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import icontract

from serenecode.contracts.predicates import is_non_empty_string
from serenecode.models import (
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
)
from serenecode.ports.dead_code_analyzer import DeadCodeAnalyzer

if TYPE_CHECKING:
    from serenecode.config import SerenecodeConfig
    from serenecode.source_discovery import SourceFile


@icontract.require(lambda file_path: isinstance(file_path, str), "file_path must be a string")
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
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.require(
    lambda dead_code_analyzer: dead_code_analyzer is not None,
    "dead_code_analyzer must be provided",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def run_dead_code_analysis(
    source_files: tuple[SourceFile, ...],
    dead_code_analyzer: DeadCodeAnalyzer,
) -> list[FunctionResult]:
    """Run static dead-code analysis on the current source set."""
    paths = tuple(
        source_file.file_path
        for source_file in source_files
        if not _is_test_file_path(source_file.file_path)
    )
    if not paths:
        return []

    # silent-except: dead-code backend failures must be surfaced as SKIPPED results, not crash the pipeline
    try:
        findings = dead_code_analyzer.analyze_paths(paths)
    except Exception as exc:
        return [_make_dead_code_skipped_result(
            f"Dead-code analysis unavailable: {exc}",
        )]

    results: list[FunctionResult] = []
    # Loop invariant: results contains dead-code findings for findings[0..i]
    for finding in findings:
        results.append(FunctionResult(
            function=finding.symbol_name,
            file=finding.file_path,
            line=finding.line,
            level_requested=1,
            level_achieved=1,
            status=CheckStatus.EXEMPT,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="dead_code",
                finding_type="dead_code",
                message=finding.message,
                suggestion=(
                    "Ask the user whether this likely dead code should be removed "
                    "or allowlisted with '# allow-unused: reason'."
                ),
                counterexample={
                    "symbol_type": finding.symbol_type,
                    "confidence": finding.confidence,
                },
            ),),
        ))

    return results


@icontract.require(
    lambda message: is_non_empty_string(message),
    "message must be a non-empty string",
)
@icontract.ensure(
    lambda result: result.status == CheckStatus.SKIPPED,
    "result must be a skipped dead-code result",
)
def _make_dead_code_skipped_result(message: str) -> FunctionResult:
    """Create a visible skipped result for dead-code analysis unavailability."""
    return FunctionResult(
        function="<dead_code>",
        file="dead_code",
        line=1,
        level_requested=1,
        level_achieved=0,
        status=CheckStatus.SKIPPED,
        details=(Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="dead_code",
            finding_type="unavailable",
            message=message,
            suggestion="Install or fix the dead-code backend, or rerun once it is available.",
        ),),
    )


# Modules with no testable logic are exempt from test-file requirements.
_TEST_FILE_EXEMPT_PATTERNS = (
    "ports/",
    "templates/",
    "tests/fixtures/",
    "exceptions.py",
)


@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def is_test_file_exempt(module_path: str) -> bool:
    """Check whether a module has no testable logic and needs no test file."""
    from serenecode.config import _path_pattern_matches

    # Loop invariant: no pattern in _TEST_FILE_EXEMPT_PATTERNS[0..i] matched
    for pattern in _TEST_FILE_EXEMPT_PATTERNS:
        if _path_pattern_matches(module_path, pattern):
            return True
    return False


@icontract.require(
    lambda known_test_stems: known_test_stems is not None,
    "test stems must be provided",
)
@icontract.ensure(
    lambda result: result is not None,
    "result must not be None",
)
def check_test_existence(
    source_files: tuple[SourceFile, ...],
    known_test_stems: frozenset[str],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check that each source module has a corresponding test file."""
    results: list[FunctionResult] = []

    # Loop invariant: results contains test-existence findings for source_files[0..i]
    for sf in source_files:
        basename = sf.file_path.rsplit("/", 1)[-1] if "/" in sf.file_path else sf.file_path
        if basename == "__init__.py":
            continue
        if is_test_file_exempt(sf.module_path):
            continue

        module_stem = basename.removesuffix(".py")
        expected_test_stem = f"test_{module_stem}"
        has_test = expected_test_stem in known_test_stems

        if has_test:
            results.append(FunctionResult(
                function="<module>",
                file=sf.file_path,
                line=1,
                level_requested=1,
                level_achieved=1,
                status=CheckStatus.PASSED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="test_exists",
                    message=f"Test file found for '{module_stem}'",
                ),),
            ))
        else:
            results.append(FunctionResult(
                function="<module>",
                file=sf.file_path,
                line=1,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="missing_tests",
                    message=f"No test file found for '{module_stem}'",
                    suggestion=(
                        f"Create a test file named '{expected_test_stem}.py' "
                        f"in your tests/ directory."
                    ),
                ),),
            ))

    return results
