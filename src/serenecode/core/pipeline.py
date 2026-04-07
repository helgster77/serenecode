"""Verification pipeline orchestrator for Serenecode.

This module orchestrates the sequential execution of verification
levels (1→2→3→4→5→6), handling early termination, result merging,
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
from serenecode.ports.coverage_analyzer import CoverageAnalyzer
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
    "level must be between 1 and 6",
)
@icontract.require(
    lambda start_level: is_valid_verification_level(start_level),
    "start_level must be between 1 and 6",
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
    lambda level, result: result.level_requested == level,
    "result must report the correct requested level",
)
def run_pipeline(
    source_files: tuple[SourceFile, ...],
    level: int,
    start_level: int,
    config: SerenecodeConfig,
    structural_checker: StructuralChecker | None = None,
    type_checker: TypeChecker | None = None,
    coverage_analyzer: CoverageAnalyzer | None = None,
    property_tester: PropertyTester | None = None,
    symbolic_checker: SymbolicChecker | None = None,
    early_termination: bool = True,
    progress: Callable[[str], None] | None = None,
    max_workers: int = 4,
    known_test_stems: frozenset[str] = frozenset(),
    spec_content: str | None = None,
    test_sources: tuple[tuple[str, str], ...] = (),
) -> CheckResult:
    """Run the verification pipeline up to the specified level.

    Executes levels sequentially (1→2→3→4→5→6). If early_termination
    is True (default), stops at the first level with failures.

    Args:
        source_files: Tuple of source files to verify.
        level: Maximum verification level (1-6).
        start_level: First verification level to execute.
        config: Active Serenecode configuration.
        structural_checker: Callable for Level 1 (or None to use default).
        type_checker: TypeChecker protocol implementation for Level 2.
        coverage_analyzer: CoverageAnalyzer protocol implementation for Level 3.
        property_tester: PropertyTester protocol implementation for Level 4.
        symbolic_checker: SymbolicChecker protocol implementation for Level 5.
        early_termination: Stop at first failing level if True.
        progress: Optional callback for progress messages.
        max_workers: Max concurrent modules for Level 5 symbolic verification.
        known_test_stems: Frozenset of test file stems (e.g. {"test_engine",
            "test_models"}) discovered from the tests/ directory. When
            non-empty, Level 1 checks that each source module has a
            corresponding test file.
        spec_content: Content of SPEC.md for traceability checking. When
            provided, Level 1 verifies that every REQ-xxx in the spec
            has implementation and test references.
        test_sources: Tuple of (file_path, source_content) for test files,
            used by spec traceability checking to find Verifies: tags.

    Returns:
        An aggregated CheckResult across all executed levels.
    """
    start_time = time.monotonic()
    all_results: list[FunctionResult] = []
    achieved_level = start_level - 1
    has_source_files = len(source_files) > 0

    # Cap max_workers to a reasonable limit to avoid resource exhaustion
    max_workers = min(max_workers, 32)

    def _emit(msg: str) -> None:
        if progress is not None:
            # silent-except: progress callback is best-effort UI; failures must not abort the pipeline
            try:
                progress(msg)
            except Exception:
                pass

    # Level 1: Structural check
    if start_level <= 1 <= level:
        _emit(f"Level 1: Structural check ({len(source_files)} files)...")
        level_1_results = _run_level_1(source_files, config, structural_checker)
        if known_test_stems:
            level_1_results.extend(
                _check_test_existence(source_files, known_test_stems, config)
            )
        if spec_content is not None:
            from serenecode.checker.spec_traceability import (
                check_spec_traceability,
                validate_spec,
            )

            _emit("  Spec validation...")
            validation_result = validate_spec(spec_content)
            level_1_results.extend(validation_result.results)

            _emit("  Spec traceability check...")
            spec_result = check_spec_traceability(
                spec_content, source_files, test_sources,
            )
            level_1_results.extend(spec_result.results)
        all_results.extend(level_1_results)

        if early_termination and _has_failures(level_1_results):
            elapsed = time.monotonic() - start_time
            return make_check_result(
                tuple(all_results),
                level_requested=level,
                duration_seconds=elapsed,
                level_achieved=achieved_level,
            )
        if _level_achieved(level_1_results, has_source_files):
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
        if _level_achieved(level_2_results, has_source_files):
            achieved_level = 2

    # Level 3: Coverage analysis
    if start_level <= 3 <= level:
        if coverage_analyzer is not None:
            _emit("Level 3: Coverage analysis...")
            level_3_results = _run_level_3_coverage(source_files, coverage_analyzer)
        else:
            _emit("Level 3: Coverage analysis unavailable.")
            level_3_results = _make_unavailable_results(
                source_files,
                requested_level=3,
                level_achieved=2,
                tool="coverage",
                message="Coverage analysis unavailable: coverage is not installed",
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
        if _level_achieved(level_3_results, has_source_files, require_evidence=True):
            achieved_level = 3

    # Level 4: Property-based testing
    if start_level <= 4 <= level:
        if property_tester is not None:
            _emit("Level 4: Property-based testing...")
            level_4_results = _run_level_4(source_files, property_tester)
        else:
            _emit("Level 4: Property-based testing unavailable.")
            level_4_results = _make_unavailable_results(
                source_files,
                requested_level=4,
                level_achieved=3,
                tool="hypothesis",
                message="Property testing unavailable: Hypothesis is not installed",
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
        if _level_achieved(level_4_results, has_source_files, require_evidence=True):
            achieved_level = 4

    # Level 5: Symbolic verification
    if start_level <= 5 <= level:
        if symbolic_checker is not None:
            _emit("Level 5: Symbolic verification (this may take several minutes)...")
            level_5_results = _run_level_5(source_files, symbolic_checker, _emit, max_workers)
        else:
            _emit("Level 5: Symbolic verification unavailable.")
            level_5_results = _make_unavailable_results(
                source_files,
                requested_level=5,
                level_achieved=4,
                tool="crosshair",
                message="Symbolic verification unavailable: CrossHair is not installed",
            )
        all_results.extend(level_5_results)

        if early_termination and _has_failures(level_5_results):
            elapsed = time.monotonic() - start_time
            return make_check_result(
                tuple(all_results),
                level_requested=level,
                duration_seconds=elapsed,
                level_achieved=achieved_level,
            )
        if _level_achieved(level_5_results, has_source_files, require_evidence=True):
            achieved_level = 5

    # Level 6: Compositional verification
    if start_level <= 6 <= level:
        _emit("Level 6: Compositional verification...")
        level_6_results = _run_level_6(source_files, config)
        all_results.extend(level_6_results)
        if _level_achieved(level_6_results, has_source_files):
            achieved_level = 6

    elapsed = time.monotonic() - start_time
    return make_check_result(
        tuple(all_results),
        level_requested=level,
        duration_seconds=elapsed,
        level_achieved=achieved_level,
    )


@icontract.require(
    lambda results: results is not None,
    "results must not be None",
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
    lambda results: results is not None,
    "results must not be None",
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
    lambda results: results is not None,
    "results must not be None",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _level_achieved(
    results: list[FunctionResult],
    has_source_files: bool,
    require_evidence: bool = False,
) -> bool:
    """Check if a verification level was achieved.

    Args:
        results: Level results to evaluate.
        has_source_files: Whether the pipeline had any source files.
        require_evidence: If True, empty results with source files
            means the level is NOT achieved (used for L3/L4/L5 where
            empty results means no functions were exercised). If False,
            empty results means "no issues found" which counts as a pass
            (used for L1/L2/L6 where the checker examines all files).

    Returns:
        True if the level should be considered achieved.
    """
    if _has_failures(results) or _has_skips(results):
        return False
    if not has_source_files:
        return True
    if require_evidence and not results:
        return False
    return True


@icontract.require(
    lambda requested_level: requested_level in (2, 3, 4, 5),
    "requested_level must be a backend verification level",
)
@icontract.require(
    lambda level_achieved: 0 <= level_achieved <= 5,
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
    lambda source_files, result: len(result) == len(source_files),
    "must produce one result per source file",
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
        3: VerificationLevel.COVERAGE,
        4: VerificationLevel.PROPERTIES,
        5: VerificationLevel.SYMBOLIC,
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
    lambda source_files: source_files is not None,
    "source_files must be provided",
)
@icontract.require(
    lambda config: config.template_name in ("default", "strict", "minimal"),
    "config must have a valid template",
)
@icontract.ensure(
    lambda result: result is not None,
    "result must not be None",
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


# Modules with no testable logic are exempt from test-file requirements.
# This is narrower than the contract-exemption list: adapters, CLI, and
# init modules contain real logic that must be tested, even though they
# are exempt from full contract requirements.
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
def _is_test_file_exempt(module_path: str) -> bool:
    """Check whether a module has no testable logic and needs no test file.

    Exempts only protocol definitions, static templates, exception
    hierarchies, and test fixtures. Adapter modules, CLI code, and
    other modules with real logic are NOT exempt.

    Args:
        module_path: The module's path (e.g. 'serenecode/ports/file_system.py').

    Returns:
        True if the module needs no dedicated test file.
    """
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
def _check_test_existence(
    source_files: tuple[SourceFile, ...],
    known_test_stems: frozenset[str],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Check that each source module has a corresponding test file.

    For a source file named ``foo.py``, this looks for ``test_foo`` in the
    known test stems. ``__init__.py`` files are skipped since they rarely
    need dedicated test files. Modules with no testable logic (protocol
    definitions, static templates, exception hierarchies, test fixtures)
    are also skipped.

    Args:
        source_files: Source files to check.
        known_test_stems: Set of test file stems (e.g. ``test_engine``).
        config: Active configuration (used for exemption checks).

    Returns:
        List of FAILED results for source modules missing test files,
        and PASSED results for those that have them.
    """
    results: list[FunctionResult] = []

    # Loop invariant: results contains test-existence findings for source_files[0..i]
    for sf in source_files:
        basename = sf.file_path.rsplit("/", 1)[-1] if "/" in sf.file_path else sf.file_path
        if basename == "__init__.py":
            continue

        # Skip modules with no testable logic: protocol definitions,
        # static templates, exception hierarchies, and test fixtures.
        if _is_test_file_exempt(sf.module_path):
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


@icontract.require(
    lambda type_checker: type_checker is not None,
    "type_checker must be provided",
)
@icontract.ensure(
    lambda result: result is not None,
    "result must not be None",
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
    lambda source_files: source_files is not None,
    "source_files must be provided",
)
@icontract.ensure(
    lambda result: result is not None,
    "result must not be None",
)
def _collect_type_check_search_paths(
    source_files: tuple[SourceFile, ...],
) -> tuple[str, ...]:
    """Collect unique import roots needed for static type checking."""
    seen: set[str] = set()
    search_paths: list[str] = []

    # Loop invariant: search_paths contains unique import roots from source_files[0..i]
    for sf in source_files:
        # Loop invariant: search_paths contains unique roots from sf.import_search_paths[0..j]
        for path in sf.import_search_paths:
            if path not in seen:
                seen.add(path)
                search_paths.append(path)

    return tuple(search_paths)


@icontract.require(
    lambda coverage_analyzer: coverage_analyzer is not None,
    "coverage_analyzer must be provided",
)
@icontract.ensure(
    lambda result: result is not None,
    "result must not be None",
)
def _run_level_3_coverage(
    source_files: tuple[SourceFile, ...],
    coverage_analyzer: CoverageAnalyzer,
) -> list[FunctionResult]:
    """Run Level 3 coverage analysis on source files.

    Args:
        source_files: Files to check.
        coverage_analyzer: CoverageAnalyzer protocol implementation.

    Returns:
        List of function results from coverage analysis.
    """
    from serenecode.checker.coverage import transform_coverage_results

    results: list[FunctionResult] = []
    # Loop invariant: results contains coverage findings for source_files[0..i]
    for sf in source_files:
        if sf.importable_module is None:
            results.append(FunctionResult(
                function="<module>",
                file=sf.file_path,
                line=1,
                level_requested=3,
                level_achieved=2,
                status=CheckStatus.SKIPPED,
                details=(Detail(
                    level=VerificationLevel.COVERAGE,
                    tool="coverage",
                    finding_type="not_importable",
                    message=f"Module '{sf.file_path}' is not importable as a Python package",
                ),),
            ))
            continue
        try:
            findings = coverage_analyzer.analyze_module(
                sf.importable_module,
                search_paths=sf.import_search_paths,
            )
            check_result = transform_coverage_results(findings, sf.file_path, 0.0)
            results.extend(check_result.results)
        except Exception as exc:
            results.append(FunctionResult(
                function="<module>",
                file=sf.file_path,
                line=1,
                level_requested=3,
                level_achieved=2,
                status=CheckStatus.SKIPPED,
                details=(Detail(
                    level=VerificationLevel.COVERAGE,
                    tool="coverage",
                    finding_type="unavailable" if isinstance(exc, ToolNotInstalledError) else "error",
                    message=f"Coverage analysis skipped for '{sf.importable_module}': {exc}",
                ),),
            ))
    return results


@icontract.require(
    lambda property_tester: property_tester is not None,
    "property_tester must be provided",
)
@icontract.ensure(
    lambda result: result is not None,
    "result must not be None",
)
def _run_level_4(
    source_files: tuple[SourceFile, ...],
    property_tester: PropertyTester,
) -> list[FunctionResult]:
    """Run Level 4 property-based testing on source files.

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
            results.append(FunctionResult(
                function="<module>",
                file=sf.file_path,
                line=1,
                level_requested=4,
                level_achieved=3,
                status=CheckStatus.SKIPPED,
                details=(Detail(
                    level=VerificationLevel.PROPERTIES,
                    tool="hypothesis",
                    finding_type="not_importable",
                    message=f"Module '{sf.file_path}' is not importable as a Python package",
                ),),
            ))
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
                level_requested=4,
                level_achieved=3,
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
    lambda result: result is not None,
    "result must not be None",
)
def _run_level_5(
    source_files: tuple[SourceFile, ...],
    symbolic_checker: SymbolicChecker,
    emit: Callable[[str], None] = lambda _msg: None,
    max_workers: int = 4,
) -> list[FunctionResult]:
    """Run Level 5 symbolic verification on source files in parallel.

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
    import threading

    from serenecode.checker.symbolic import transform_symbolic_results

    emit_lock = threading.Lock()
    verifiable: list[SourceFile] = []
    results: list[FunctionResult] = []

    # Record non-importable modules as skipped so they appear in the output
    # Loop invariant: verifiable contains importable files from source_files[0..i]
    for sf in source_files:
        if sf.importable_module is not None:
            verifiable.append(sf)
        else:
            results.append(FunctionResult(
                function="<module>",
                file=sf.file_path,
                line=1,
                level_requested=5,
                level_achieved=4,
                status=CheckStatus.SKIPPED,
                details=(Detail(
                    level=VerificationLevel.SYMBOLIC,
                    tool="crosshair",
                    finding_type="not_importable",
                    message=f"Module '{sf.file_path}' is not importable as a Python package",
                ),),
            ))

    total = len(verifiable)
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

    def _safe_emit(msg: str) -> None:
        with emit_lock:
            emit(msg)

    _safe_emit(f"  Verifying {total} modules ({max_workers} workers)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_verify_one, sf): sf for sf in verifiable}
        # Loop invariant: results contains findings for all completed futures
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            sf, findings, error = future.result()
            module_name = sf.importable_module
            if error is not None:
                _safe_emit(f"  [{completed}/{total}] Skipped {module_name}: {error}")
                results.append(FunctionResult(
                    function="<module>",
                    file=sf.file_path,
                    line=1,
                    level_requested=5,
                    level_achieved=4,
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
                _safe_emit(f"  [{completed}/{total}] Done {module_name}")
                check_result = transform_symbolic_results(findings, sf.file_path, 0.0)
                results.extend(check_result.results)

    return results


@icontract.require(
    lambda config: config.template_name in ("default", "strict", "minimal"),
    "config must have a valid template",
)
@icontract.ensure(
    lambda result: result is not None,
    "result must not be None",
)
def _run_level_6(
    source_files: tuple[SourceFile, ...],
    config: SerenecodeConfig,
) -> list[FunctionResult]:
    """Run Level 6 compositional verification across all source files.

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
