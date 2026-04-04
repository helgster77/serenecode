"""Tests for the Level 3 coverage verification checker."""

from __future__ import annotations

from serenecode.checker.coverage import (
    _build_suggestion,
    _format_line_ranges,
    transform_coverage_results,
)
from serenecode.models import CheckStatus
from serenecode.ports.coverage_analyzer import (
    CoverageFinding,
    MockDependency,
    CoverageSuggestion,
)
from tests.conftest import assert_violation_or_skip


def _make_passing_finding(name: str = "func") -> CoverageFinding:
    return CoverageFinding(
        function_name=name,
        module_path="test_module",
        line_start=1,
        line_end=10,
        line_coverage_percent=95.0,
        branch_coverage_percent=90.0,
        uncovered_lines=(),
        uncovered_branches=(),
        suggestions=(),
        meets_threshold=True,
        message=f"'{name}' has 95% line coverage and 90% branch coverage (threshold: 80%)",
    )


def _make_failing_finding(name: str = "func") -> CoverageFinding:
    return CoverageFinding(
        function_name=name,
        module_path="test_module",
        line_start=1,
        line_end=20,
        line_coverage_percent=40.0,
        branch_coverage_percent=30.0,
        uncovered_lines=(5, 6, 7, 12, 13),
        uncovered_branches=((8, 9),),
        suggestions=(
            CoverageSuggestion(
                description="branch: if x > 0 (lines 5-7)",
                target_lines=(5, 6, 7),
                suggested_test_code="def test_func_line_5():\n    result = func()\n    assert result is not None",
                required_mocks=(
                    MockDependency(
                        name="open",
                        import_module="builtins",
                        is_external=False,
                        mock_necessary=True,
                        reason="file system I/O",
                    ),
                ),
                all_mocks_necessary=True,
            ),
        ),
        meets_threshold=False,
        message="'func' has 40% line coverage and 30% branch coverage — 5 lines uncovered (threshold: 80%)",
    )


class TestTransformCoverageResults:
    """Tests for transforming coverage findings into CheckResult."""

    def test_passing_finding(self) -> None:
        findings = [_make_passing_finding()]
        result = transform_coverage_results(findings, "test.py", 1.0)
        assert result.passed is True
        assert result.summary.passed_count == 1
        assert result.summary.failed_count == 0
        assert len(result.results) == 1
        assert result.results[0].status == CheckStatus.PASSED
        assert result.results[0].level_achieved == 3

    def test_failing_finding(self) -> None:
        findings = [_make_failing_finding()]
        result = transform_coverage_results(findings, "test.py", 1.0)
        assert result.passed is False
        assert result.summary.failed_count == 1
        assert len(result.results) == 1
        assert result.results[0].status == CheckStatus.FAILED
        assert result.results[0].level_achieved == 2

    def test_mixed_findings(self) -> None:
        findings = [_make_passing_finding("good"), _make_failing_finding("bad")]
        result = transform_coverage_results(findings, "test.py", 2.0)
        assert result.passed is False
        assert result.summary.passed_count == 1
        assert result.summary.failed_count == 1
        assert len(result.results) == 2

    def test_empty_findings(self) -> None:
        result = transform_coverage_results([], "test.py", 0.0)
        assert result.passed is True
        assert result.summary.total_functions == 0
        assert len(result.results) == 0

    def test_output_count_matches_input(self) -> None:
        findings = [_make_passing_finding(f"f{i}") for i in range(5)]
        result = transform_coverage_results(findings, "test.py", 0.0)
        assert len(result.results) == 5

    def test_file_path_propagated(self) -> None:
        findings = [_make_passing_finding()]
        result = transform_coverage_results(findings, "src/pkg/mod.py", 0.0)
        assert result.results[0].file == "src/pkg/mod.py"

    def test_line_number_from_finding(self) -> None:
        finding = CoverageFinding(
            function_name="deep_func",
            module_path="m",
            line_start=42,
            line_end=50,
            line_coverage_percent=100.0,
            branch_coverage_percent=100.0,
            uncovered_lines=(),
            uncovered_branches=(),
            suggestions=(),
            meets_threshold=True,
            message="ok",
        )
        result = transform_coverage_results([finding], "test.py", 0.0)
        assert result.results[0].line == 42

    def test_suggestion_included_in_details(self) -> None:
        findings = [_make_failing_finding()]
        result = transform_coverage_results(findings, "test.py", 0.0)
        detail = result.results[0].details[0]
        assert detail.suggestion is not None
        assert "Coverage:" in detail.suggestion
        assert "Uncovered lines:" in detail.suggestion

    def test_level_requested_always_3(self) -> None:
        findings = [_make_passing_finding()]
        result = transform_coverage_results(findings, "test.py", 0.0)
        assert result.results[0].level_requested == 3


class TestBuildSuggestion:
    """Tests for the suggestion builder."""

    def test_includes_coverage_percentages(self) -> None:
        finding = _make_failing_finding()
        result = _build_suggestion(finding)
        assert "40%" in result
        assert "30%" in result

    def test_includes_uncovered_lines(self) -> None:
        finding = _make_failing_finding()
        result = _build_suggestion(finding)
        assert "5-7" in result
        assert "12-13" in result

    def test_includes_test_code(self) -> None:
        finding = _make_failing_finding()
        result = _build_suggestion(finding)
        assert "def test_func_line_5" in result

    def test_includes_mock_assessment(self) -> None:
        finding = _make_failing_finding()
        result = _build_suggestion(finding)
        assert "REQUIRED" in result
        assert "file system I/O" in result

    def test_no_mocks_message(self) -> None:
        finding = CoverageFinding(
            function_name="simple",
            module_path="m",
            line_start=1,
            line_end=5,
            line_coverage_percent=50.0,
            branch_coverage_percent=50.0,
            uncovered_lines=(3, 4),
            uncovered_branches=(),
            suggestions=(
                CoverageSuggestion(
                    description="lines 3-4",
                    target_lines=(3, 4),
                    suggested_test_code="def test(): pass",
                    required_mocks=(),
                    all_mocks_necessary=True,
                ),
            ),
            meets_threshold=False,
            message="50% coverage",
        )
        result = _build_suggestion(finding)
        assert "No mocks needed" in result

    def test_optional_mock_message(self) -> None:
        finding = CoverageFinding(
            function_name="internal",
            module_path="m",
            line_start=1,
            line_end=5,
            line_coverage_percent=50.0,
            branch_coverage_percent=50.0,
            uncovered_lines=(3,),
            uncovered_branches=(),
            suggestions=(
                CoverageSuggestion(
                    description="line 3",
                    target_lines=(3,),
                    suggested_test_code="def test(): pass",
                    required_mocks=(
                        MockDependency(
                            name="helper",
                            import_module="pkg.helpers",
                            is_external=False,
                            mock_necessary=False,
                            reason="internal code — can use real implementation",
                        ),
                    ),
                    all_mocks_necessary=False,
                ),
            ),
            meets_threshold=False,
            message="50% coverage",
        )
        result = _build_suggestion(finding)
        assert "OPTIONAL" in result

    def test_result_is_nonempty(self) -> None:
        finding = _make_failing_finding()
        result = _build_suggestion(finding)
        assert len(result) > 0


class TestFormatLineRanges:
    """Tests for line range formatting."""

    def test_empty_input(self) -> None:
        assert _format_line_ranges(()) == "none"

    def test_single_line(self) -> None:
        assert _format_line_ranges((5,)) == "5"

    def test_contiguous_range(self) -> None:
        assert _format_line_ranges((3, 4, 5)) == "3-5"

    def test_non_contiguous(self) -> None:
        result = _format_line_ranges((1, 3, 5))
        assert result == "1, 3, 5"

    def test_mixed_ranges_and_singles(self) -> None:
        result = _format_line_ranges((1, 2, 3, 7, 10, 11))
        assert result == "1-3, 7, 10-11"

    def test_unsorted_input(self) -> None:
        result = _format_line_ranges((5, 3, 4, 1))
        assert result == "1, 3-5"

    def test_two_elements_contiguous(self) -> None:
        assert _format_line_ranges((8, 9)) == "8-9"

    def test_two_elements_non_contiguous(self) -> None:
        assert _format_line_ranges((2, 7)) == "2, 7"

    def test_large_range(self) -> None:
        lines = tuple(range(100, 201))
        assert _format_line_ranges(lines) == "100-200"

    def test_precondition_rejects_non_positive(self) -> None:
        assert_violation_or_skip(lambda: _format_line_ranges((0,)))

    def test_precondition_rejects_negative(self) -> None:
        assert_violation_or_skip(lambda: _format_line_ranges((-1,)))
