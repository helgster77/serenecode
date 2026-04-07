"""Tests for the Level 2 type checking checker."""

from __future__ import annotations

from serenecode.checker.types import _suggest_from_mypy_code, transform_type_results
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

    def test_empty_message_does_not_request_suggestion(self) -> None:
        issues = [
            TypeIssue(
                file="test.py",
                line=10,
                column=5,
                severity="error",
                message="",
                code="arg-type",
            ),
        ]
        result = transform_type_results(issues, 0.3)
        assert result.summary.failed_count == 1
        assert result.results[0].details[0].suggestion is None


class TestSuggestFromMypyCode:
    """Tests for _suggest_from_mypy_code — covers branch gaps at lines 146-148."""

    def test_known_code_returns_specific_suggestion(self) -> None:
        result = _suggest_from_mypy_code("arg-type", "argument has wrong type")
        assert result is not None
        assert "argument" in result.lower() or "parameter" in result.lower()

    def test_unknown_code_returns_generic_with_code(self) -> None:
        """Branch (line 146): code is not in the known dict → generic with code."""
        result = _suggest_from_mypy_code("some-unknown-code", "weird error")
        assert result is not None
        assert "some-unknown-code" in result

    def test_no_code_returns_generic(self) -> None:
        """Branch (line 148): code is None → fully generic suggestion."""
        result = _suggest_from_mypy_code(None, "some error")
        assert result is not None
        assert "mypy --strict" in result

    def test_known_code_arg_type(self) -> None:
        result = _suggest_from_mypy_code("arg-type", "argument has wrong type")
        assert result is not None
        assert "argument" in result.lower() or "parameter" in result.lower()

    def test_known_code_return_value(self) -> None:
        result = _suggest_from_mypy_code("return-value", "incompatible return value type")
        assert result is not None
        assert "return" in result.lower()

    def test_known_code_attr_defined(self) -> None:
        result = _suggest_from_mypy_code("attr-defined", "attribute does not exist")
        assert result is not None
        assert "attribute" in result.lower()
