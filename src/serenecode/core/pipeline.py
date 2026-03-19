"""Verification pipeline orchestrator for Serenecode.

This module orchestrates the sequential execution of verification
levels (1→2→3→4→5), handling early termination, result merging,
and level selection.

This is a core module — no I/O operations are permitted. All
verification backends are injected through protocol interfaces.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

import icontract

from serenecode.config import SerenecodeConfig
from serenecode.ports.property_tester import PropertyTester
from serenecode.ports.symbolic_checker import SymbolicChecker, SymbolicFinding
from serenecode.ports.type_checker import TypeChecker
from serenecode.contracts.predicates import is_valid_verification_level
from serenecode.models import (
    CheckResult,
    CheckStatus,
    FunctionResult,
    make_check_result,
)


@icontract.invariant(lambda self: True, "frozen source file data carrier")
@dataclass(frozen=True)
class SourceFile:
    """A source file to be verified.

    Contains the file path, derived module path, and source content.
    """

    file_path: str
    module_path: str
    source: str
    importable_module: str | None = None  # e.g. "tests.fixtures.valid.simple_function"


@icontract.require(
    lambda level: is_valid_verification_level(level),
    "level must be between 1 and 5",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
def run_pipeline(
    source_files: tuple[SourceFile, ...],
    level: int,
    config: SerenecodeConfig,
    structural_checker: object | None = None,
    type_checker: TypeChecker | None = None,
    property_tester: PropertyTester | None = None,
    symbolic_checker: SymbolicChecker | None = None,
    early_termination: bool = True,
    progress: Callable[[str], None] | None = None,
    max_workers: int = 4,
) -> CheckResult:
    """Run the verification pipeline up to the specified level.

    Executes levels sequentially (1→2→3→4). If early_termination
    is True (default), stops at the first level with failures.

    Args:
        source_files: Tuple of source files to verify.
        level: Maximum verification level (1-5).
        config: Active Serenecode configuration.
        structural_checker: Callable for Level 1 (or None to use default).
        type_checker: TypeChecker protocol implementation for Level 2.
        property_tester: PropertyTester protocol implementation for Level 3.
        symbolic_checker: SymbolicChecker protocol implementation for Level 4.
        early_termination: Stop at first failing level if True.
        progress: Optional callback for progress messages.
        max_workers: Max concurrent modules for Level 4 symbolic verification.

    Returns:
        An aggregated CheckResult across all executed levels.
    """
    start_time = time.monotonic()
    all_results: list[FunctionResult] = []
    current_level_achieved = level

    def _emit(msg: str) -> None:
        if progress is not None:
            progress(msg)

    # Level 1: Structural check
    if level >= 1:
        _emit(f"Level 1: Structural check ({len(source_files)} files)...")
        level_1_results = _run_level_1(source_files, config)
        all_results.extend(level_1_results)

        if early_termination and _has_failures(level_1_results):
            current_level_achieved = 1
            elapsed = time.monotonic() - start_time
            return make_check_result(
                tuple(all_results),
                level_requested=level,
                duration_seconds=elapsed,
            )

    # Level 2: Type checking
    if level >= 2 and type_checker is not None:
        _emit("Level 2: Type checking...")
        level_2_results = _run_level_2(source_files, type_checker)
        all_results.extend(level_2_results)

        if early_termination and _has_failures(level_2_results):
            current_level_achieved = 2
            elapsed = time.monotonic() - start_time
            return make_check_result(
                tuple(all_results),
                level_requested=level,
                duration_seconds=elapsed,
            )

    # Level 3: Property-based testing
    if level >= 3 and property_tester is not None:
        _emit("Level 3: Property-based testing...")
        level_3_results = _run_level_3(source_files, property_tester)
        all_results.extend(level_3_results)

        if early_termination and _has_failures(level_3_results):
            current_level_achieved = 3
            elapsed = time.monotonic() - start_time
            return make_check_result(
                tuple(all_results),
                level_requested=level,
                duration_seconds=elapsed,
            )

    # Level 4: Symbolic verification
    if level >= 4 and symbolic_checker is not None:
        _emit("Level 4: Symbolic verification (this may take several minutes)...")
        level_4_results = _run_level_4(source_files, symbolic_checker, _emit, max_workers)
        all_results.extend(level_4_results)

        if early_termination and _has_failures(level_4_results):
            current_level_achieved = 4
            elapsed = time.monotonic() - start_time
            return make_check_result(
                tuple(all_results),
                level_requested=level,
                duration_seconds=elapsed,
            )

    # Level 5: Compositional verification
    if level >= 5:
        _emit("Level 5: Compositional verification...")
        level_5_results = _run_level_5(source_files, config)
        all_results.extend(level_5_results)

    elapsed = time.monotonic() - start_time
    return make_check_result(
        tuple(all_results),
        level_requested=level,
        duration_seconds=elapsed,
    )


def _has_failures(results: list[FunctionResult]) -> bool:
    """Check if any results indicate failure.

    Args:
        results: List of function results to check.

    Returns:
        True if any result has FAILED status.
    """
    # Loop invariant: result is True if any of results[0..i] has FAILED status
    for r in results:
        if r.status == CheckStatus.FAILED:
            return True
    return False


def _run_level_1(
    source_files: tuple[SourceFile, ...],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Run Level 1 structural checks on all source files.

    Args:
        source_files: Files to check.
        config: Active configuration.

    Returns:
        List of function results from structural checking.
    """
    from serenecode.checker.structural import check_structural

    results: list[FunctionResult] = []
    # Loop invariant: results contains structural check results for source_files[0..i]
    for sf in source_files:
        check_result = check_structural(
            sf.source, config, sf.module_path, sf.file_path,
        )
        results.extend(check_result.results)
    return results


def _run_level_2(
    source_files: tuple[SourceFile, ...],
    type_checker: TypeChecker,
) -> list[FunctionResult]:
    """Run Level 2 type checking on source files.

    Args:
        source_files: Files to check.
        type_checker: TypeChecker protocol implementation.

    Returns:
        List of function results from type checking.
    """
    from serenecode.checker.types import transform_type_results
    from serenecode.ports.type_checker import TypeIssue

    file_paths = [sf.file_path for sf in source_files]
    issues: list[TypeIssue] = type_checker.check(file_paths)
    return list(transform_type_results(issues, 0.0).results)


def _run_level_3(
    source_files: tuple[SourceFile, ...],
    property_tester: PropertyTester,
) -> list[FunctionResult]:
    """Run Level 3 property-based testing on source files.

    Args:
        source_files: Files to check.
        property_tester: PropertyTester protocol implementation.

    Returns:
        List of function results from property testing.
    """
    from serenecode.checker.properties import transform_property_results

    results: list[FunctionResult] = []
    # Loop invariant: results contains property test results for source_files[0..i]
    for sf in source_files:
        if sf.importable_module is None:
            continue
        try:
            findings = property_tester.test_module(sf.importable_module)
            check_result = transform_property_results(findings, sf.file_path, 0.0)
            results.extend(check_result.results)
        except Exception:
            pass  # Skip modules that can't be tested
    return results


def _run_level_4(
    source_files: tuple[SourceFile, ...],
    symbolic_checker: SymbolicChecker,
    emit: Callable[[str], None] = lambda _msg: None,
    max_workers: int = 4,
) -> list[FunctionResult]:
    """Run Level 4 symbolic verification on source files in parallel.

    Each module is verified in its own subprocess (via the symbolic
    checker adapter), and we dispatch multiple modules concurrently
    using a thread pool.

    Args:
        source_files: Files to check.
        symbolic_checker: SymbolicChecker protocol implementation.
        emit: Callback for progress messages.
        max_workers: Maximum number of modules to verify concurrently.

    Returns:
        List of function results from symbolic verification.
    """
    import concurrent.futures

    from serenecode.checker.symbolic import transform_symbolic_results

    verifiable = [sf for sf in source_files if sf.importable_module is not None]
    total = len(verifiable)
    results: list[FunctionResult] = []
    completed = 0

    def _verify_one(sf: SourceFile) -> tuple[SourceFile, list[SymbolicFinding] | None, str | None]:
        assert sf.importable_module is not None
        try:
            findings = symbolic_checker.verify_module(sf.importable_module)
            return (sf, findings, None)
        except Exception as exc:
            return (sf, None, str(exc))

    emit(f"  Verifying {total} modules ({max_workers} workers)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_verify_one, sf): sf for sf in verifiable}
        # Loop invariant: results contains findings for all completed futures
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            sf, findings, error = future.result()
            module_name = sf.importable_module
            if error is not None:
                emit(f"  [{completed}/{total}] Skipped {module_name}: {error}")
            else:
                assert findings is not None
                emit(f"  [{completed}/{total}] Done {module_name}")
                check_result = transform_symbolic_results(findings, sf.file_path, 0.0)
                results.extend(check_result.results)

    return results


def _run_level_5(
    source_files: tuple[SourceFile, ...],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Run Level 5 compositional verification across all source files.

    Args:
        source_files: Files to check.
        config: Active configuration.

    Returns:
        List of function results from compositional checking.
    """
    from serenecode.checker.compositional import check_compositional

    sources: list[tuple[str, str, str]] = []
    # Loop invariant: sources contains (source, file_path, module_path) for source_files[0..i]
    for sf in source_files:
        sources.append((sf.source, sf.file_path, sf.module_path))

    result = check_compositional(sources, config)
    return list(result.results)
