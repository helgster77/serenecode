"""Tests for the Level 4 symbolic verification checker."""

from __future__ import annotations

from serenecode.checker.symbolic import (
    _suggest_fix_symbolic,
    transform_symbolic_results,
)
from serenecode.models import CheckStatus
from serenecode.ports.symbolic_checker import SymbolicFinding


class TestTransformSymbolicResults:
    """Tests for transforming symbolic findings into CheckResult."""

    def test_verified_finding(self) -> None:
        findings = [
            SymbolicFinding(
                function_name="square",
                module_path="test_module",
                outcome="verified",
                message="Verified: all postconditions hold",
                duration_seconds=1.5,
            ),
        ]
        result = transform_symbolic_results(findings, "test.py", 1.5)
        assert result.passed is True
        assert result.summary.passed_count == 1

    def test_counterexample_finding(self) -> None:
        findings = [
            SymbolicFinding(
                function_name="abs_value",
                module_path="test_module",
                outcome="counterexample",
                message="Postcondition violated: result >= 0",
                counterexample={"x": 0},
                duration_seconds=0.5,
            ),
        ]
        result = transform_symbolic_results(findings, "test.py", 0.5)
        assert result.passed is False
        assert result.summary.failed_count == 1
        detail = result.results[0].details[0]
        assert detail.counterexample == {"x": 0}
        assert detail.suggestion is not None

    def test_timeout_finding(self) -> None:
        findings = [
            SymbolicFinding(
                function_name="complex_func",
                module_path="test_module",
                outcome="timeout",
                message="Verification timed out",
                duration_seconds=30.0,
            ),
        ]
        result = transform_symbolic_results(findings, "test.py", 30.0)
        assert result.passed is False
        assert result.summary.passed_count == 0
        assert result.summary.skipped_count == 1
        assert result.results[0].status == CheckStatus.SKIPPED
        assert result.results[0].level_achieved == 4

    def test_unsupported_finding(self) -> None:
        findings = [
            SymbolicFinding(
                function_name="dynamic_func",
                module_path="test_module",
                outcome="unsupported",
                message="Uses unsupported feature",
                duration_seconds=0.1,
            ),
        ]
        result = transform_symbolic_results(findings, "test.py", 0.1)
        # Unsupported functions are EXEMPT: visible but don't block passing.
        # All-EXEMPT results do not claim the level was achieved.
        assert result.passed is False
        assert result.level_achieved == 0
        assert result.summary.passed_count == 0
        assert result.summary.exempt_count == 1
        assert result.results[0].status == CheckStatus.EXEMPT
        assert result.results[0].level_achieved == 4

    def test_error_finding(self) -> None:
        findings = [
            SymbolicFinding(
                function_name="broken",
                module_path="test_module",
                outcome="error",
                message="Internal error",
                duration_seconds=0.01,
            ),
        ]
        result = transform_symbolic_results(findings, "test.py", 0.01)
        assert result.passed is False
        assert result.summary.failed_count == 1

    def test_mixed_findings(self) -> None:
        findings = [
            SymbolicFinding(
                function_name="good",
                module_path="m",
                outcome="verified",
                message="OK",
            ),
            SymbolicFinding(
                function_name="bad",
                module_path="m",
                outcome="counterexample",
                message="Failed",
                counterexample={"x": -1},
            ),
            SymbolicFinding(
                function_name="slow",
                module_path="m",
                outcome="timeout",
                message="Timed out",
            ),
        ]
        result = transform_symbolic_results(findings, "test.py", 5.0)
        assert result.passed is False
        assert result.summary.passed_count == 1
        assert result.summary.failed_count == 1
        assert result.summary.skipped_count == 1

    def test_counterexample_never_reclassified_as_passed(self) -> None:
        """Regression: counterexamples must always be FAILED, never PASSED."""
        findings = [
            SymbolicFinding(
                function_name="buggy",
                module_path="m",
                outcome="counterexample",
                message="Postcondition violated",
                counterexample={"x": -1},
            ),
        ]
        result = transform_symbolic_results(findings, "test.py", 1.0)
        assert result.passed is False
        assert result.summary.failed_count == 1
        assert result.summary.passed_count == 0
        assert result.results[0].status == CheckStatus.FAILED

    def test_empty_findings(self) -> None:
        result = transform_symbolic_results([], "test.py", 0.0)
        assert result.passed is True
        assert result.summary.total_functions == 0


class TestSuggestFixSymbolic:
    """Tests for _suggest_fix_symbolic — covers branch gaps at lines 167, 170."""

    def test_counterexample_with_condition(self) -> None:
        """Branch (line 167): both counterexample and condition present."""
        finding = SymbolicFinding(
            function_name="f",
            module_path="test",
            outcome="counterexample",
            message="violated",
            counterexample={"x": -1},
            condition="result >= 0",
            duration_seconds=0.1,
        )
        suggestion = _suggest_fix_symbolic(finding)
        assert suggestion is not None
        assert "x=-1" in suggestion
        assert "result >= 0" in suggestion

    def test_counterexample_without_condition(self) -> None:
        finding = SymbolicFinding(
            function_name="f",
            module_path="test",
            outcome="counterexample",
            message="violated",
            counterexample={"x": -1},
            duration_seconds=0.1,
        )
        suggestion = _suggest_fix_symbolic(finding)
        assert suggestion is not None
        assert "x=-1" in suggestion

    def test_condition_only_no_counterexample(self) -> None:
        """Branch (line 170): no counterexample, just a condition string."""
        finding = SymbolicFinding(
            function_name="f",
            module_path="test",
            outcome="counterexample",
            message="violated",
            counterexample=None,
            condition="result >= 0",
            duration_seconds=0.1,
        )
        suggestion = _suggest_fix_symbolic(finding)
        assert suggestion is not None
        assert "result >= 0" in suggestion

    def test_no_counterexample_no_condition(self) -> None:
        finding = SymbolicFinding(
            function_name="f",
            module_path="test",
            outcome="counterexample",
            message="violated",
            counterexample=None,
            condition=None,
            duration_seconds=0.1,
        )
        suggestion = _suggest_fix_symbolic(finding)
        assert suggestion is not None
        assert "Symbolic verification" in suggestion

    def test_empty_counterexample_dict_treated_as_none(self) -> None:
        finding = SymbolicFinding(
            function_name="f",
            module_path="test",
            outcome="counterexample",
            message="violated",
            counterexample={},
            condition=None,
            duration_seconds=0.1,
        )
        suggestion = _suggest_fix_symbolic(finding)
        assert suggestion is not None
