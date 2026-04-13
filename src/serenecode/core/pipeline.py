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
from serenecode.contracts.predicates import (
    is_non_empty_string,
    is_positive_int,
    is_valid_verification_level,
)
from serenecode.core.exceptions import ToolNotInstalledError
from serenecode.core.pipeline_helpers import (
    _is_test_file_path,
    check_test_existence as _check_test_existence,
    run_dead_code_analysis as _run_dead_code_analysis,
)
from serenecode.ports.coverage_analyzer import CoverageAnalyzer
from serenecode.ports.dead_code_analyzer import DeadCodeAnalyzer
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
    lambda self: self.max_workers >= 1,
    "max_workers must be at least 1",
)
@dataclass(frozen=True)
class PipelineConfig:
    """Bundle of adapter and option parameters for the verification pipeline.

    Encapsulates the optional backends and knobs so that
    ``run_pipeline`` has a manageable parameter count.
    """

    structural_checker: StructuralChecker | None = None
    type_checker: TypeChecker | None = None
    coverage_analyzer: CoverageAnalyzer | None = None
    property_tester: PropertyTester | None = None
    symbolic_checker: SymbolicChecker | None = None
    dead_code_analyzer: DeadCodeAnalyzer | None = None
    early_termination: bool = True
    progress: Callable[[str], None] | None = None
    max_workers: int = 4
    known_test_stems: frozenset[str] = frozenset()
    spec_content: str | None = None
    test_sources: tuple[tuple[str, str], ...] = ()


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
    context_root: str | None = None  # normalized root used for module-path derivation


@icontract.require(lambda sf: sf is not None, "sf must be provided")
@icontract.ensure(
    lambda sf, result: isinstance(result, str) and len(result) > 0,
    "message must be a non-empty string",
)
def _not_importable_detail_message(sf: SourceFile) -> str:
    """Explain why L3–L5 cannot run when no dotted module name was derived."""
    base = f"Module '{sf.file_path}' is not importable as a Python package"
    if sf.context_root:
        return (
            f"{base}. Inferred project root was {sf.context_root!r}. "
            "Expected path segments that are valid Python identifiers under src/ or the "
            "repository root; pass --project-root if the checker was run from a subfolder."
        )
    return (
        f"{base}. Expected path segments that are valid Python identifiers under src/ or "
        "the repository root; pass --project-root if the checker was run from a subfolder."
    )


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
@icontract.ensure(
    lambda level, result: result.level_requested == level,
    "result must report the correct requested level",
)
def run_pipeline(
    source_files: tuple[SourceFile, ...],
    level: int,
    start_level: int,
    config: SerenecodeConfig,
    pc: PipelineConfig | None = None,
    **kwargs: object,
) -> CheckResult:
    """Run the verification pipeline up to the specified level.

    Implements: REQ-028, REQ-029, REQ-030, INT-001

    Executes levels sequentially (1->2->3->4->5->6). If early_termination
    is True (default), stops at the first level with failures.

    Args:
        source_files: Tuple of source files to verify.
        level: Maximum verification level (1-6).
        start_level: First verification level to execute.
        config: Active Serenecode configuration.
        pc: Optional PipelineConfig bundling adapters and options.
            When None, one is built from ``kwargs``.
        **kwargs: Fields forwarded to ``PipelineConfig`` when *pc* is None.

    Returns:
        An aggregated CheckResult across all executed levels.
    """
    if pc is None:
        pc = PipelineConfig(
            max_workers=min(int(kwargs.pop("max_workers", 4)), 32),  # type: ignore[arg-type]
            **{k: v for k, v in kwargs.items() if k in PipelineConfig.__dataclass_fields__},  # type: ignore[arg-type]
        )
    return _run_pipeline_impl(source_files, level, start_level, config, pc)


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
@icontract.ensure(
    lambda level, result: result.level_requested == level,
    "result must report the correct requested level",
)
def _run_pipeline_impl(
    source_files: tuple[SourceFile, ...],
    level: int,
    start_level: int,
    config: SerenecodeConfig,
    pc: PipelineConfig,
) -> CheckResult:
    """Core pipeline logic with bundled configuration."""
    start_time = time.monotonic()
    all_results: list[FunctionResult] = []
    achieved_level = start_level - 1
    has_source_files = len(source_files) > 0

    def _emit(msg: str) -> None:
        if pc.progress is not None:
            # silent-except: progress callback is best-effort UI; failures must not abort the pipeline
            try:
                pc.progress(msg)
            except Exception:
                pass

    # Level 1: Structural check
    if start_level <= 1 <= level:
        level_1_results = _run_level_1_full(
            source_files, config, pc, _emit,
        )
        all_results.extend(level_1_results)
        if pc.early_termination and _has_failures(level_1_results):
            return _make_early_return(all_results, level, start_time, achieved_level)
        if _level_achieved(level_1_results, has_source_files):
            achieved_level = 1

    # Levels 2-5: backend verification
    level_configs = _backend_level_configs(source_files, pc, _emit)
    # Loop invariant: all_results contains findings for completed levels
    for lv, runner, require_evidence in level_configs:
        if not (start_level <= lv <= level):
            continue
        level_results = runner()
        all_results.extend(level_results)
        if pc.early_termination and _has_failures(level_results):
            return _make_early_return(all_results, level, start_time, achieved_level)
        if _level_achieved(level_results, has_source_files, require_evidence=require_evidence):
            achieved_level = lv

    # Level 6: Compositional verification
    if start_level <= 6 <= level:
        _emit("Level 6: Compositional verification...")
        level_6_results = _run_level_6(source_files, config, pc.spec_content)
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


def _make_early_return(
    all_results: list[FunctionResult],
    level: int,
    start_time: float,
    achieved_level: int,
) -> CheckResult:
    """Build an early-termination CheckResult."""
    elapsed = time.monotonic() - start_time
    return make_check_result(
        tuple(all_results),
        level_requested=level,
        duration_seconds=elapsed,
        level_achieved=achieved_level,
    )


def _run_level_1_full(
    source_files: tuple[SourceFile, ...],
    config: SerenecodeConfig,
    pc: PipelineConfig,
    emit: Callable[[str], None],
) -> list[FunctionResult]:
    """Run all Level 1 checks: structural, spec, dead code, module health."""
    emit(f"Level 1: Structural check ({len(source_files)} files)...")
    results = _run_level_1(source_files, config, pc.structural_checker)

    if pc.known_test_stems:
        results.extend(
            _check_test_existence(source_files, pc.known_test_stems, config)
        )
    if pc.spec_content is not None:
        results.extend(_run_spec_checks(source_files, pc, emit))
    if pc.dead_code_analyzer is not None:
        emit("  Dead code analysis...")
        results.extend(
            _run_dead_code_analysis(source_files, pc.dead_code_analyzer),
        )
    if config.module_health.enabled:
        results.extend(_run_module_health_checks(source_files, config, emit))
    return results


def _run_spec_checks(
    source_files: tuple[SourceFile, ...],
    pc: PipelineConfig,
    emit: Callable[[str], None],
) -> list[FunctionResult]:
    """Run spec validation and traceability checks."""
    from serenecode.checker.spec_traceability import (
        check_spec_traceability,
        validate_spec,
    )

    results: list[FunctionResult] = []
    if pc.spec_content is None:
        return results
    emit("  Spec validation...")
    validation_result = validate_spec(pc.spec_content)
    results.extend(validation_result.results)

    emit("  Spec traceability check...")
    spec_result = check_spec_traceability(
        pc.spec_content, source_files, pc.test_sources,
    )
    results.extend(spec_result.results)
    return results


def _run_module_health_checks(
    source_files: tuple[SourceFile, ...],
    config: SerenecodeConfig,
    emit: Callable[[str], None],
) -> list[FunctionResult]:
    """Run all module health sub-checks."""
    from serenecode.core.module_health import (
        check_class_method_count,
        check_file_length,
        check_function_length,
        check_parameter_count,
    )

    emit("  Module health checks...")
    results: list[FunctionResult] = []
    results.extend(check_file_length(source_files, config))
    results.extend(check_function_length(source_files, config))
    results.extend(check_parameter_count(source_files, config))
    results.extend(check_class_method_count(source_files, config))
    return results


def _backend_level_configs(
    source_files: tuple[SourceFile, ...],
    pc: PipelineConfig,
    emit: Callable[[str], None],
) -> list[tuple[int, Callable[[], list[FunctionResult]], bool]]:
    """Return (level, runner, require_evidence) tuples for levels 2-5."""

    def _level_2() -> list[FunctionResult]:
        if pc.type_checker is not None:
            emit("Level 2: Type checking...")
            return _run_level_2(source_files, pc.type_checker)
        emit("Level 2: Type checking unavailable.")
        return _make_unavailable_results(
            source_files, requested_level=2, level_achieved=1,
            tool="mypy", message="Type checking unavailable: mypy is not installed",
        )

    def _level_3() -> list[FunctionResult]:
        if pc.coverage_analyzer is not None:
            emit("Level 3: Coverage analysis...")
            return _run_level_3_coverage(source_files, pc.coverage_analyzer)
        emit("Level 3: Coverage analysis unavailable.")
        return _make_unavailable_results(
            source_files, requested_level=3, level_achieved=2,
            tool="coverage", message="Coverage analysis unavailable: coverage is not installed",
        )

    def _level_4() -> list[FunctionResult]:
        if pc.property_tester is not None:
            emit("Level 4: Property-based testing...")
            return _run_level_4(source_files, pc.property_tester)
        emit("Level 4: Property-based testing unavailable.")
        return _make_unavailable_results(
            source_files, requested_level=4, level_achieved=3,
            tool="hypothesis", message="Property testing unavailable: Hypothesis is not installed",
        )

    def _level_5() -> list[FunctionResult]:
        if pc.symbolic_checker is not None:
            emit("Level 5: Symbolic verification (this may take several minutes)...")
            return _run_level_5(source_files, pc.symbolic_checker, emit, pc.max_workers)
        emit("Level 5: Symbolic verification unavailable.")
        return _make_unavailable_results(
            source_files, requested_level=5, level_achieved=4,
            tool="crosshair", message="Symbolic verification unavailable: CrossHair is not installed",
        )

    return [
        (2, _level_2, False),
        (3, _level_3, True),
        (4, _level_4, True),
        (5, _level_5, True),
    ]


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
                    message=_not_importable_detail_message(sf),
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
                    message=_not_importable_detail_message(sf),
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
    verifiable, results = _partition_importable(source_files)

    def _safe_emit(msg: str) -> None:
        with emit_lock:
            emit(msg)

    total = len(verifiable)
    _safe_emit(f"  Verifying {total} modules ({max_workers} workers)...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(_verify_one_module, sf, symbolic_checker): sf
            for sf in verifiable
        }
        completed = 0
        # Loop invariant: results contains findings for all completed futures
        for future in concurrent.futures.as_completed(futures):
            completed += 1
            sf, findings, error = future.result()
            _process_symbolic_result(
                results, sf, findings, error, completed, total, _safe_emit,
            )

    return results


def _partition_importable(
    source_files: tuple[SourceFile, ...],
) -> tuple[list[SourceFile], list[FunctionResult]]:
    """Split source files into importable and non-importable, recording skips."""
    verifiable: list[SourceFile] = []
    results: list[FunctionResult] = []
    # Loop invariant: verifiable contains importable files from source_files[0..i]
    for sf in source_files:
        if sf.importable_module is not None:
            verifiable.append(sf)
        else:
            results.append(FunctionResult(
                function="<module>", file=sf.file_path, line=1,
                level_requested=5, level_achieved=4,
                status=CheckStatus.SKIPPED,
                details=(Detail(
                    level=VerificationLevel.SYMBOLIC, tool="crosshair",
                    finding_type="not_importable",
                    message=_not_importable_detail_message(sf),
                ),),
            ))
    return verifiable, results


def _verify_one_module(
    sf: SourceFile,
    symbolic_checker: SymbolicChecker,
) -> tuple[SourceFile, list[SymbolicFinding] | None, Exception | None]:
    """Verify a single module via the symbolic checker."""
    if sf.importable_module is None:
        return (sf, None, ToolNotInstalledError("No importable module"))
    try:
        findings = symbolic_checker.verify_module(
            sf.importable_module, search_paths=sf.import_search_paths,
        )
        return (sf, findings, None)
    except Exception as exc:
        return (sf, None, exc)


def _process_symbolic_result(
    results: list[FunctionResult],
    sf: SourceFile,
    findings: list[SymbolicFinding] | None,
    error: Exception | None,
    completed: int,
    total: int,
    safe_emit: Callable[[str], None],
) -> None:
    """Process one completed symbolic verification future."""
    from serenecode.checker.symbolic import transform_symbolic_results

    module_name = sf.importable_module
    if error is not None:
        safe_emit(f"  [{completed}/{total}] Skipped {module_name}: {error}")
        is_tool_error = isinstance(error, ToolNotInstalledError)
        results.append(FunctionResult(
            function="<module>", file=sf.file_path, line=1,
            level_requested=5, level_achieved=4,
            status=CheckStatus.SKIPPED if is_tool_error else CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.SYMBOLIC, tool="crosshair",
                finding_type="unavailable" if is_tool_error else "error",
                message=(
                    f"Symbolic verification skipped for '{module_name}': {error}"
                    if is_tool_error
                    else f"Symbolic verification failed for '{module_name}': {error}"
                ),
            ),),
        ))
    elif findings is not None:
        safe_emit(f"  [{completed}/{total}] Done {module_name}")
        check_result = transform_symbolic_results(findings, sf.file_path, 0.0)
        results.extend(check_result.results)


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
    spec_content: str | None = None,
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

    result = check_compositional(sources, config, spec_content=spec_content)
    return list(result.results)
