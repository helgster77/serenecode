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
from serenecode.contracts.predicates import is_non_empty_string, is_positive_int, is_valid_verification_level
from serenecode.core.exceptions import ToolNotInstalledError
from serenecode.ports.property_tester import PropertyTester
from serenecode.ports.symbolic_checker import SymbolicChecker, SymbolicFinding
from serenecode.ports.type_checker import TypeChecker
from serenecode.models import (
    CheckResult,
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)

StructuralChecker = Callable[[str, SerenecodeConfig, str, str], CheckResult]


@icontract.invariant(
    lambda self: len(self.file_path) > 0,
    "Source file must have a non-empty file path",
)
@dataclass(frozen=True)
class SourceFile:
    """A source file to be verified.

    Contains the file path, derived module path, and source content.
    """

    file_path: str
    module_path: str
    source: str
    importable_module: str | None = None  # e.g. "tests.fixtures.valid.simple_function"
    import_search_paths: tuple[str, ...] = ()


@icontract.require(
    lambda level: is_valid_verification_level(level),
    "level must be between 1 and 5",
)
@icontract.require(
    lambda start_level: is_valid_verification_level(start_level),
    "start_level must be between 1 and 5",
)
@icontract.require(
    lambda level, start_level: start_level <= level,
    "start_level must not exceed level",
)
@icontract.require(
    lambda max_workers: is_positive_int(max_workers),
    "max_workers must be at least 1",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
def run_pipeline(
    source_files: tuple[SourceFile, ...],
    level: int,
    start_level: int,
    config: SerenecodeConfig,
    structural_checker: StructuralChecker | None = None,
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
        start_level: First verification level to execute.
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
    achieved_level = start_level - 1

    def _emit(msg: str) -> None:
        if progress is not None:
            progress(msg)

    # Level 1: Structural check
    if start_level <= 1 <= level:
        _emit(f"Level 1: Structural check ({len(source_files)} files)...")
        level_1_results = _run_level_1(source_files, config, structural_checker)
        all_results.extend(level_1_results)

        if early_termination and _has_failures(level_1_results):
            elapsed = time.monotonic() - start_time
            return make_check_result(
                tuple(all_results),
                level_requested=level,
                duration_seconds=elapsed,
                level_achieved=achieved_level,
            )
        achieved_level = 1

    # Level 2: Type checking
    if start_level <= 2 <= level:
        if type_checker is not None:
            _emit("Level 2: Type checking...")
            level_2_results = _run_level_2(source_files, type_checker)
        else:
            _emit("Level 2: Type checking unavailable.")
            level_2_results = _make_unavailable_results(
                source_files,
                requested_level=2,
                level_achieved=1,
                tool="mypy",
                message="Type checking unavailable: mypy is not installed",
            )
        all_results.extend(level_2_results)

        if early_termination and _has_failures(level_2_results):
            elapsed = time.monotonic() - start_time
            return make_check_result(
                tuple(all_results),
                level_requested=level,
                duration_seconds=elapsed,
                level_achieved=achieved_level,
            )
        if not _has_skips(level_2_results):
            achieved_level = 2

    # Level 3: Property-based testing
    if start_level <= 3 <= level:
        if property_tester is not None:
            _emit("Level 3: Property-based testing...")
            level_3_results = _run_level_3(source_files, property_tester)
        else:
            _emit("Level 3: Property-based testing unavailable.")
            level_3_results = _make_unavailable_results(
                source_files,
                requested_level=3,
                level_achieved=2,
                tool="hypothesis",
                message="Property testing unavailable: Hypothesis is not installed",
            )
        all_results.extend(level_3_results)

        if early_termination and _has_failures(level_3_results):
            elapsed = time.monotonic() - start_time
            return make_check_result(
                tuple(all_results),
                level_requested=level,
                duration_seconds=elapsed,
                level_achieved=achieved_level,
            )
        if level_3_results and not _has_skips(level_3_results):
            achieved_level = 3

    # Level 4: Symbolic verification
    if start_level <= 4 <= level:
        if symbolic_checker is not None:
            _emit("Level 4: Symbolic verification (this may take several minutes)...")
            level_4_results = _run_level_4(source_files, symbolic_checker, _emit, max_workers)
        else:
            _emit("Level 4: Symbolic verification unavailable.")
            level_4_results = _make_unavailable_results(
                source_files,
                requested_level=4,
                level_achieved=3,
                tool="crosshair",
                message="Symbolic verification unavailable: CrossHair is not installed",
            )
        all_results.extend(level_4_results)

        if early_termination and _has_failures(level_4_results):
            elapsed = time.monotonic() - start_time
            return make_check_result(
                tuple(all_results),
                level_requested=level,
                duration_seconds=elapsed,
                level_achieved=achieved_level,
            )
        if level_4_results and not _has_skips(level_4_results):
            achieved_level = 4

    # Level 5: Compositional verification
    if start_level <= 5 <= level:
        _emit("Level 5: Compositional verification...")
        level_5_results = _run_level_5(source_files, config)
        all_results.extend(level_5_results)
        if not _has_failures(level_5_results) and not _has_skips(level_5_results):
            achieved_level = 5

    elapsed = time.monotonic() - start_time
    return make_check_result(
        tuple(all_results),
        level_requested=level,
        duration_seconds=elapsed,
        level_achieved=achieved_level,
    )


@icontract.require(
    lambda results: isinstance(results, list),
    "results must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
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


@icontract.require(
    lambda results: isinstance(results, list),
    "results must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _has_skips(results: list[FunctionResult]) -> bool:
    """Check if any results indicate an incomplete verification step."""
    # Loop invariant: result is True if any of results[0..i] has SKIPPED status
    for r in results:
        if r.status == CheckStatus.SKIPPED:
            return True
    return False


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.require(
    lambda requested_level: requested_level in (2, 3, 4),
    "requested_level must be a backend verification level",
)
@icontract.require(
    lambda level_achieved: 0 <= level_achieved <= 4,
    "level_achieved must be within the completed pipeline range",
)
@icontract.require(
    lambda tool: is_non_empty_string(tool),
    "tool must be a non-empty string",
)
@icontract.require(
    lambda message: is_non_empty_string(message),
    "message must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def _make_unavailable_results(
    source_files: tuple[SourceFile, ...],
    requested_level: int,
    level_achieved: int,
    tool: str,
    message: str,
) -> list[FunctionResult]:
    """Create per-file skipped results when a verification backend is unavailable."""
    level_map = {
        2: VerificationLevel.TYPES,
        3: VerificationLevel.PROPERTIES,
        4: VerificationLevel.SYMBOLIC,
    }
    verification_level = level_map[requested_level]
    results: list[FunctionResult] = []

    # Loop invariant: results contains unavailable-backend findings for source_files[0..i]
    for sf in source_files:
        results.append(FunctionResult(
            function="<module>",
            file=sf.file_path,
            line=1,
            level_requested=requested_level,
            level_achieved=level_achieved,
            status=CheckStatus.SKIPPED,
            details=(Detail(
                level=verification_level,
                tool=tool,
                finding_type="unavailable",
                message=message,
            ),),
        ))

    return results


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.require(
    lambda config: isinstance(config, SerenecodeConfig),
    "config must be a SerenecodeConfig",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def _run_level_1(
    source_files: tuple[SourceFile, ...],
    config: SerenecodeConfig,
    structural_checker: StructuralChecker | None = None,
) -> list[FunctionResult]:
    """Run Level 1 structural checks on all source files.

    Args:
        source_files: Files to check.
        config: Active configuration.

    Returns:
        List of function results from structural checking.
    """
    checker = structural_checker
    if checker is None:
        from serenecode.checker.structural import check_structural

        checker = check_structural

    results: list[FunctionResult] = []
    # Loop invariant: results contains structural check results for source_files[0..i]
    for sf in source_files:
        check_result = checker(
            sf.source, config, sf.module_path, sf.file_path,
        )
        results.extend(check_result.results)
    return results


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.require(
    lambda type_checker: type_checker is not None,
    "type_checker must be provided",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
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
    search_paths = _collect_type_check_search_paths(source_files)
    issues: list[TypeIssue] = type_checker.check(
        file_paths,
        search_paths=search_paths,
    )
    return list(transform_type_results(issues, 0.0).results)


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple),
    "result must be a tuple",
)
def _collect_type_check_search_paths(
    source_files: tuple[SourceFile, ...],
) -> tuple[str, ...]:
    """Collect unique import roots needed for static type checking."""
    search_paths: list[str] = []

    # Loop invariant: search_paths contains unique import roots from source_files[0..i]
    for sf in source_files:
        # Loop invariant: search_paths contains unique roots from sf.import_search_paths[0..j]
        for path in sf.import_search_paths:
            if path not in search_paths:
                search_paths.append(path)

    return tuple(search_paths)


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.require(
    lambda property_tester: property_tester is not None,
    "property_tester must be provided",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
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
            findings = property_tester.test_module(
                sf.importable_module,
                search_paths=sf.import_search_paths,
            )
            check_result = transform_property_results(findings, sf.file_path, 0.0)
            results.extend(check_result.results)
        except Exception as exc:
            # Record the error as a skipped result rather than silently dropping it
            results.append(FunctionResult(
                function="<module>",
                file=sf.file_path,
                line=1,
                level_requested=3,
                level_achieved=2,
                status=CheckStatus.SKIPPED,
                details=(Detail(
                    level=VerificationLevel.PROPERTIES,
                    tool="hypothesis",
                    finding_type="unavailable" if isinstance(exc, ToolNotInstalledError) else "error",
                    message=f"Property testing skipped for '{sf.importable_module}': {exc}",
                ),),
            ))
    return results


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.require(
    lambda symbolic_checker: symbolic_checker is not None,
    "symbolic_checker must be provided",
)
@icontract.require(
    lambda emit: emit is not None,
    "emit callback must be provided",
)
@icontract.require(
    lambda max_workers: is_positive_int(max_workers),
    "max_workers must be at least 1",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
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

    def _verify_one(
        sf: SourceFile,
    ) -> tuple[SourceFile, list[SymbolicFinding] | None, Exception | None]:
        if sf.importable_module is None:
            return (sf, None, ToolNotInstalledError("No importable module"))
        try:
            findings = symbolic_checker.verify_module(
                sf.importable_module,
                search_paths=sf.import_search_paths,
            )
            return (sf, findings, None)
        except Exception as exc:
            return (sf, None, exc)

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
                results.append(FunctionResult(
                    function="<module>",
                    file=sf.file_path,
                    line=1,
                    level_requested=4,
                    level_achieved=3,
                    status=CheckStatus.SKIPPED if isinstance(error, ToolNotInstalledError) else CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.SYMBOLIC,
                        tool="crosshair",
                        finding_type="unavailable" if isinstance(error, ToolNotInstalledError) else "error",
                        message=f"Symbolic verification skipped for '{module_name}': {error}"
                        if isinstance(error, ToolNotInstalledError)
                        else f"Symbolic verification failed for '{module_name}': {error}",
                    ),),
                ))
            elif findings is not None:
                emit(f"  [{completed}/{total}] Done {module_name}")
                check_result = transform_symbolic_results(findings, sf.file_path, 0.0)
                results.extend(check_result.results)

    return results


@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.require(
    lambda config: isinstance(config, SerenecodeConfig),
    "config must be a SerenecodeConfig",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
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
