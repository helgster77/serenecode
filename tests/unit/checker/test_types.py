"""Tests for the Level 2 type checking checker."""

from __future__ import annotations

from serenecode.checker.types import transform_type_results
from serenecode.models import CheckStatus
from serenecode.ports.type_checker import TypeIssue


class TestTransformTypeResults:
    """Tests for transforming type issues into CheckResult."""

    def test_no_issues_passes(self) -> None:
        result = transform_type_results([], 0.5)
        assert result.passed is True
        assert result.summary.total_functions == 0

    def test_error_issues_fail(self) -> None:
        issues = [
            TypeIssue(
                file="test.py",
                line=10,
                column=5,
                severity="error",
                message="Incompatible return value type",
                code="return-value",
            ),
        ]
        result = transform_type_results(issues, 0.3)
        assert result.passed is False
        assert result.summary.failed_count == 1
        assert result.results[0].details[0].suggestion is not None

    def test_warning_only_passes(self) -> None:
        issues = [
            TypeIssue(
                file="test.py",
                line=10,
                column=5,
                severity="warning",
                message="Some warning",
            ),
        ]
        result = transform_type_results(issues, 0.1)
        assert result.passed is True

    def test_multiple_errors_same_line_grouped(self) -> None:
        issues = [
            TypeIssue(file="test.py", line=10, column=5, severity="error",
                      message="Error 1", code="arg-type"),
            TypeIssue(file="test.py", line=10, column=15, severity="error",
                      message="Error 2", code="return-value"),
        ]
        result = transform_type_results(issues, 0.2)
        assert result.summary.failed_count == 1
        assert len(result.results[0].details) == 2

    def test_multiple_files(self) -> None:
        issues = [
            TypeIssue(file="a.py", line=5, column=1, severity="error",
                      message="Error in a"),
            TypeIssue(file="b.py", line=10, column=1, severity="error",
                      message="Error in b"),
        ]
        result = transform_type_results(issues, 0.3)
        assert result.summary.failed_count == 2
