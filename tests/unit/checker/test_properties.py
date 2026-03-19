"""Tests for the Level 3 property-based testing checker."""

from __future__ import annotations

from serenecode.checker.properties import transform_property_results
from serenecode.models import CheckStatus
from serenecode.ports.property_tester import PropertyFinding


class TestTransformPropertyResults:
    """Tests for transforming property findings into CheckResult."""

    def test_passed_finding(self) -> None:
        findings = [
            PropertyFinding(
                function_name="square",
                module_path="test_module",
                passed=True,
                finding_type="verified",
                message="Property tests passed for 'square'",
            ),
        ]
        result = transform_property_results(findings, "test.py", 0.1)
        assert result.passed is True
        assert result.summary.passed_count == 1

    def test_failed_finding_with_counterexample(self) -> None:
        findings = [
            PropertyFinding(
                function_name="abs_value",
                module_path="test_module",
                passed=False,
                finding_type="postcondition_violated",
                message="Postcondition violated: result >= 0",
                counterexample={"x": 0},
            ),
        ]
        result = transform_property_results(findings, "test.py", 0.1)
        assert result.passed is False
        assert result.summary.failed_count == 1
        assert result.results[0].details[0].counterexample == {"x": 0}

    def test_crash_finding(self) -> None:
        findings = [
            PropertyFinding(
                function_name="divide",
                module_path="test_module",
                passed=False,
                finding_type="crash",
                message="Function 'divide' crashed",
                exception_type="ZeroDivisionError",
                exception_message="division by zero",
            ),
        ]
        result = transform_property_results(findings, "test.py", 0.1)
        assert result.passed is False
        assert "ZeroDivisionError" in (result.results[0].details[0].suggestion or "")

    def test_mixed_findings(self) -> None:
        findings = [
            PropertyFinding(
                function_name="good_func",
                module_path="test_module",
                passed=True,
                finding_type="verified",
                message="OK",
            ),
            PropertyFinding(
                function_name="bad_func",
                module_path="test_module",
                passed=False,
                finding_type="postcondition_violated",
                message="Failed",
            ),
        ]
        result = transform_property_results(findings, "test.py", 0.2)
        assert result.passed is False
        assert result.summary.passed_count == 1
        assert result.summary.failed_count == 1

    def test_empty_findings(self) -> None:
        result = transform_property_results([], "test.py", 0.0)
        assert result.passed is True
        assert result.summary.total_functions == 0
