"""Tests for the verification pipeline orchestrator."""

from __future__ import annotations

from dataclasses import dataclass

from serenecode.config import default_config, minimal_config
from serenecode.core.exceptions import ToolNotInstalledError
from serenecode.core.pipeline import SourceFile, run_pipeline
from serenecode.models import CheckResult, CheckStatus, FunctionResult, make_check_result
from serenecode.ports.coverage_analyzer import CoverageFinding
from serenecode.ports.property_tester import PropertyFinding
from serenecode.ports.symbolic_checker import SymbolicFinding
from serenecode.ports.type_checker import TypeIssue
from tests.conftest import assert_violation_or_skip


@dataclass
class _NoIssuesTypeChecker:
    def check(
        self,
        file_paths: list[str],
        strict: bool = True,
        search_paths: tuple[str, ...] = (),
    ) -> list[TypeIssue]:
        return []


@dataclass
class _CapturingTypeChecker:
    captured_search_paths: list[tuple[str, ...]]

    def check(
        self,
        file_paths: list[str],
        strict: bool = True,
        search_paths: tuple[str, ...] = (),
    ) -> list[TypeIssue]:
        self.captured_search_paths.append(search_paths)
        return []


@dataclass
class _RaisingPropertyTester:
    error: Exception

    def test_module(
        self,
        module_path: str,
        max_examples: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[PropertyFinding]:
        raise self.error


@dataclass
class _CapturingPropertyTester:
    captured_search_paths: list[tuple[str, ...]]

    def test_module(
        self,
        module_path: str,
        max_examples: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[PropertyFinding]:
        self.captured_search_paths.append(search_paths)
        return [
            PropertyFinding(
                function_name="square",
                module_path=module_path,
                passed=True,
                finding_type="verified",
                message="ok",
            )
        ]


@dataclass
class _EmptyPropertyTester:
    def test_module(
        self,
        module_path: str,
        max_examples: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[PropertyFinding]:
        return []


@dataclass
class _EmptySymbolicChecker:
    def verify_module(
        self,
        module_path: str,
        per_condition_timeout: int | None = None,
        per_path_timeout: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[SymbolicFinding]:
        return []


@dataclass
class _PassingCoverageAnalyzer:
    """Coverage analyzer that returns a passing finding for each module."""

    def analyze_module(
        self,
        module_path: str,
        search_paths: tuple[str, ...] = (),
        coverage_threshold: float = 80.0,
    ) -> list[CoverageFinding]:
        return [
            CoverageFinding(
                function_name="func",
                module_path=module_path,
                line_start=1,
                line_end=10,
                line_coverage_percent=95.0,
                branch_coverage_percent=90.0,
                uncovered_lines=(),
                uncovered_branches=(),
                suggestions=(),
                meets_threshold=True,
                message=f"'{module_path}' has 95% coverage",
            ),
        ]


@dataclass
class _FailingCoverageAnalyzer:
    """Coverage analyzer that returns a failing finding."""

    def analyze_module(
        self,
        module_path: str,
        search_paths: tuple[str, ...] = (),
        coverage_threshold: float = 80.0,
    ) -> list[CoverageFinding]:
        return [
            CoverageFinding(
                function_name="func",
                module_path=module_path,
                line_start=1,
                line_end=10,
                line_coverage_percent=30.0,
                branch_coverage_percent=20.0,
                uncovered_lines=(5, 6, 7),
                uncovered_branches=(),
                suggestions=(),
                meets_threshold=False,
                message=f"'{module_path}' has 30% coverage",
            ),
        ]


@dataclass
class _EmptyCoverageAnalyzer:
    """Coverage analyzer that returns no findings."""

    def analyze_module(
        self,
        module_path: str,
        search_paths: tuple[str, ...] = (),
        coverage_threshold: float = 80.0,
    ) -> list[CoverageFinding]:
        return []


@dataclass
class _RaisingCoverageAnalyzer:
    """Coverage analyzer that raises an exception."""

    error: Exception

    def analyze_module(
        self,
        module_path: str,
        search_paths: tuple[str, ...] = (),
        coverage_threshold: float = 80.0,
    ) -> list[CoverageFinding]:
        raise self.error


def _make_valid_source() -> str:
    return '''\
"""Module docstring."""

import icontract


@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result: result >= 0, "result must be non-negative")
def square(x: float) -> float:
    """Square a number."""
    return x * x
'''


def _make_invalid_source() -> str:
    return '''\
"""Module docstring."""


def broken(x: int, y: int) -> int:
    """Missing contracts."""
    return x + y
'''


class TestPipelineLevel1:
    """Tests for pipeline running Level 1 only.

    Verifies: REQ-029, INT-001
    """

    def test_valid_source_passes(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=1, start_level=1, config=default_config())
        assert result.passed is True

    def test_invalid_source_fails(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline((sf,), level=1, start_level=1, config=default_config())
        assert result.passed is False

    def test_empty_files_passes(self) -> None:
        result = run_pipeline((), level=1, start_level=1, config=default_config())
        assert result.passed is True
        assert result.summary.total_functions == 0

    def test_multiple_files(self) -> None:
        valid = SourceFile(
            file_path="valid.py",
            module_path="valid.py",
            source=_make_valid_source(),
        )
        invalid = SourceFile(
            file_path="invalid.py",
            module_path="invalid.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline((valid, invalid), level=1, start_level=1, config=default_config())
        assert result.passed is False
        assert result.summary.total_functions > 0

    def test_exempt_module_skipped(self) -> None:
        sf = SourceFile(
            file_path="adapters/test.py",
            module_path="adapters/test.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline((sf,), level=1, start_level=1, config=default_config())
        assert result.passed is True  # exempt modules produce no results

    def test_level_requested_recorded(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=3, start_level=1, config=default_config())
        assert result.level_requested == 3

    def test_rejects_non_positive_max_workers(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )

        assert_violation_or_skip(lambda: run_pipeline(
            (sf,),
            level=1,
            start_level=1,
            config=default_config(),
            max_workers=0,
        ))

    def test_uses_injected_structural_checker(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        captured: list[tuple[str, str]] = []

        def fake_structural_checker(
            source: str,
            config: object,
            module_path: str,
            file_path: str,
        ) -> CheckResult:
            captured.append((module_path, file_path))
            # Return a real passing result so the pipeline considers L1 achieved
            passing_result = FunctionResult(
                function="fake",
                file=file_path,
                line=1,
                level_requested=1,
                level_achieved=1,
                status=CheckStatus.PASSED,
            )
            return make_check_result((passing_result,), level_requested=1, duration_seconds=0.0)

        result = run_pipeline(
            (sf,),
            level=1,
            start_level=1,
            config=default_config(),
            structural_checker=fake_structural_checker,
        )

        assert result.passed is True
        assert captured == [("test.py", "test.py")]


class TestPipelineEarlyTermination:
    """Tests for early termination behavior."""

    def test_stops_at_level_1_failure(self) -> None:
        """Verifies: REQ-030"""
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        # Even though level=5, should stop at 1 due to failures
        result = run_pipeline((sf,), level=5, start_level=1, config=default_config())
        assert result.passed is False

    def test_no_early_termination_runs_all(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline(
            (sf,), level=1, start_level=1, config=default_config(),
            early_termination=False,
        )
        assert result.passed is False


class TestPipelineWithMockAdapters:
    """Tests for pipeline with mock verification adapters."""

    def test_level_2_with_no_adapter_skips(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        # level=2 but no type_checker adapter → just runs level 1
        result = run_pipeline((sf,), level=2, start_level=1, config=default_config())
        assert result.passed is False
        assert result.summary.skipped_count == 1

    def test_level_3_with_no_adapter_skips(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=3, start_level=1, config=default_config())
        assert result.passed is False
        assert result.summary.skipped_count == 2

    def test_level_5_with_no_adapter_skips(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=5, start_level=1, config=default_config())
        assert result.passed is False
        # 4 skips: L2 mypy + L3 coverage + L4 property + L5 symbolic
        assert result.summary.skipped_count == 4

    def test_start_level_skips_structural_checks(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
            importable_module="demo.module",
        )
        property_tester = _CapturingPropertyTester(captured_search_paths=[])
        result = run_pipeline(
            (sf,),
            level=4,
            start_level=4,
            config=default_config(),
            property_tester=property_tester,
        )
        assert result.passed is True
        assert result.summary.total_functions == 1

    def test_property_import_errors_prevent_false_pass(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )
        result = run_pipeline(
            (sf,),
            level=4,
            start_level=4,
            config=default_config(),
            property_tester=_RaisingPropertyTester(ImportError("No module named 'pkg'")),
        )
        assert result.passed is False
        assert result.level_achieved == 3
        assert result.summary.skipped_count == 1

    def test_property_tester_receives_import_search_paths(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
            import_search_paths=("/tmp/project/src",),
        )
        property_tester = _CapturingPropertyTester(captured_search_paths=[])
        result = run_pipeline(
            (sf,),
            level=4,
            start_level=4,
            config=minimal_config(),
            property_tester=property_tester,
        )
        assert result.passed is True
        assert property_tester.captured_search_paths == [("/tmp/project/src",)]

    def test_type_checker_receives_import_search_paths(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            import_search_paths=("/tmp/project/src", "/tmp/project"),
        )
        type_checker = _CapturingTypeChecker(captured_search_paths=[])
        result = run_pipeline(
            (sf,),
            level=2,
            start_level=1,
            config=minimal_config(),
            type_checker=type_checker,
        )

        assert result.passed is True
        assert type_checker.captured_search_paths == [
            ("/tmp/project/src", "/tmp/project"),
        ]

    def test_duration_recorded(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=1, start_level=1, config=default_config())
        assert result.summary.duration_seconds >= 0

    def test_empty_symbolic_results_do_not_claim_level_five(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )
        property_tester = _CapturingPropertyTester(captured_search_paths=[])
        symbolic_checker = _EmptySymbolicChecker()

        result = run_pipeline(
            (sf,),
            level=5,
            start_level=1,
            config=minimal_config(),
            type_checker=_NoIssuesTypeChecker(),
            property_tester=property_tester,
            symbolic_checker=symbolic_checker,
        )

        assert result.passed is False
        assert result.level_achieved == 4

    def test_empty_property_results_do_not_claim_level_four(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )

        result = run_pipeline(
            (sf,),
            level=4,
            start_level=4,
            config=minimal_config(),
            property_tester=_EmptyPropertyTester(),
        )

        assert result.passed is False
        assert result.level_achieved == 3


class TestPipelineLevel3Coverage:
    """Tests for pipeline Level 3 coverage analysis."""

    def test_passing_coverage_achieves_level_3(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )
        result = run_pipeline(
            (sf,),
            level=3,
            start_level=3,
            config=minimal_config(),
            coverage_analyzer=_PassingCoverageAnalyzer(),
        )
        assert result.passed is True
        assert result.level_achieved == 3

    def test_failing_coverage_blocks_pass(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )
        result = run_pipeline(
            (sf,),
            level=3,
            start_level=3,
            config=minimal_config(),
            coverage_analyzer=_FailingCoverageAnalyzer(),
        )
        assert result.passed is False
        assert result.summary.failed_count == 1

    def test_empty_coverage_results_do_not_claim_level_3(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )
        result = run_pipeline(
            (sf,),
            level=3,
            start_level=3,
            config=minimal_config(),
            coverage_analyzer=_EmptyCoverageAnalyzer(),
        )
        # Empty results → level not achieved
        assert result.passed is False
        assert result.level_achieved == 2

    def test_coverage_tool_not_installed_skips(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )
        result = run_pipeline(
            (sf,),
            level=3,
            start_level=3,
            config=minimal_config(),
            coverage_analyzer=_RaisingCoverageAnalyzer(
                ToolNotInstalledError("coverage not installed"),
            ),
        )
        assert result.passed is False
        assert result.summary.skipped_count == 1

    def test_coverage_import_error_skips(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )
        result = run_pipeline(
            (sf,),
            level=3,
            start_level=3,
            config=minimal_config(),
            coverage_analyzer=_RaisingCoverageAnalyzer(
                ImportError("No module named 'pkg'"),
            ),
        )
        assert result.passed is False
        assert result.summary.skipped_count == 1

    def test_non_importable_module_skips_coverage(self) -> None:
        sf = SourceFile(
            file_path="standalone.py",
            module_path="standalone.py",
            source=_make_valid_source(),
            importable_module=None,
        )
        result = run_pipeline(
            (sf,),
            level=3,
            start_level=3,
            config=minimal_config(),
            coverage_analyzer=_PassingCoverageAnalyzer(),
        )
        assert result.passed is False
        assert result.summary.skipped_count == 1

    def test_coverage_early_termination_prevents_level_4(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )
        property_tester = _CapturingPropertyTester(captured_search_paths=[])
        result = run_pipeline(
            (sf,),
            level=4,
            start_level=3,
            config=minimal_config(),
            coverage_analyzer=_FailingCoverageAnalyzer(),
            property_tester=property_tester,
        )
        assert result.passed is False
        # Property tester should not have been called due to early termination
        assert property_tester.captured_search_paths == []


class TestPipelineTestExistence:
    """Tests for L1 test-existence checking."""

    def test_missing_test_file_fails(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/engine.py",
            module_path="pkg/engine.py",
            source=_make_valid_source(),
        )
        result = run_pipeline(
            (sf,),
            level=1,
            start_level=1,
            config=default_config(),
            known_test_stems=frozenset({"test_models"}),
        )
        assert result.passed is False
        failed = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert any("missing_tests" in d.finding_type for r in failed for d in r.details)

    def test_present_test_file_passes(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/engine.py",
            module_path="pkg/engine.py",
            source=_make_valid_source(),
        )
        result = run_pipeline(
            (sf,),
            level=1,
            start_level=1,
            config=default_config(),
            known_test_stems=frozenset({"test_engine"}),
        )
        assert result.passed is True

    def test_init_files_are_skipped(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/__init__.py",
            module_path="pkg/__init__.py",
            source='"""Package init."""\n',
        )
        result = run_pipeline(
            (sf,),
            level=1,
            start_level=1,
            config=default_config(),
            known_test_stems=frozenset(),
        )
        # __init__.py is exempt from structural checks AND skipped by test-existence
        assert result.passed is True

    def test_empty_test_stems_skips_check(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/engine.py",
            module_path="pkg/engine.py",
            source=_make_valid_source(),
        )
        # When known_test_stems is empty (default), no test-existence check runs
        result = run_pipeline(
            (sf,),
            level=1,
            start_level=1,
            config=default_config(),
            known_test_stems=frozenset(),
        )
        assert result.passed is True

    def test_suggestion_includes_expected_filename(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/engine.py",
            module_path="pkg/engine.py",
            source=_make_valid_source(),
        )
        result = run_pipeline(
            (sf,),
            level=1,
            start_level=1,
            config=default_config(),
            known_test_stems=frozenset({"test_other"}),
        )
        failed = [r for r in result.results if r.status == CheckStatus.FAILED]
        suggestions = [d.suggestion for r in failed for d in r.details if d.suggestion]
        assert any("test_engine.py" in s for s in suggestions)


class TestLevelAchievedEvidenceRequirement:
    """Tests for the pipeline's require_evidence guard on L3-L5.

    make_check_result intentionally claims the requested level for empty
    results (correct for L1/L2/L6). The pipeline layer uses
    _level_achieved(require_evidence=True) to prevent L3-L5 from
    advancing when no functions were actually exercised.
    """

    def test_level_achieved_requires_evidence_for_empty_results(self) -> None:
        """Empty results with require_evidence=True must not claim achievement."""
        from serenecode.core.pipeline import _level_achieved

        assert _level_achieved([], has_source_files=True, require_evidence=True) is False

    def test_level_achieved_allows_empty_without_evidence_flag(self) -> None:
        """Empty results without require_evidence still count as achieved (L1/L2/L6)."""
        from serenecode.core.pipeline import _level_achieved

        assert _level_achieved([], has_source_files=True, require_evidence=False) is True

    def test_level_achieved_empty_results_no_source_files(self) -> None:
        """No source files at all — always achieved regardless of evidence flag."""
        from serenecode.core.pipeline import _level_achieved

        assert _level_achieved([], has_source_files=False, require_evidence=True) is True


class TestIsTestFileExempt:
    """Tests for _is_test_file_exempt — covers branch at line 515."""

    def test_ports_module_is_exempt(self) -> None:
        """Branch (line 515): pattern matches → return True."""
        from serenecode.core.pipeline_helpers import is_test_file_exempt as _is_test_file_exempt
        assert _is_test_file_exempt("serenecode/ports/file_system.py") is True

    def test_templates_module_is_exempt(self) -> None:
        from serenecode.core.pipeline_helpers import is_test_file_exempt as _is_test_file_exempt
        assert _is_test_file_exempt("serenecode/templates/content.py") is True

    def test_exceptions_module_is_exempt(self) -> None:
        from serenecode.core.pipeline_helpers import is_test_file_exempt as _is_test_file_exempt
        assert _is_test_file_exempt("serenecode/exceptions.py") is True

    def test_fixture_module_is_exempt(self) -> None:
        from serenecode.core.pipeline_helpers import is_test_file_exempt as _is_test_file_exempt
        assert _is_test_file_exempt("tests/fixtures/broken.py") is True

    def test_regular_module_is_not_exempt(self) -> None:
        """Regular core modules are NOT exempt — they need test files."""
        from serenecode.core.pipeline_helpers import is_test_file_exempt as _is_test_file_exempt
        assert _is_test_file_exempt("serenecode/core/pipeline.py") is False

    def test_adapter_module_is_not_exempt(self) -> None:
        from serenecode.core.pipeline_helpers import is_test_file_exempt as _is_test_file_exempt
        assert _is_test_file_exempt("serenecode/adapters/local_fs.py") is False
