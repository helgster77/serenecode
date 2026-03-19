"""Tests for report formatting functions."""

from __future__ import annotations

import json

from serenecode.models import (
    CheckResult,
    CheckStatus,
    CheckSummary,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)
from serenecode.reporter import format_html, format_human, format_json


def _make_sample_result() -> CheckResult:
    """Create a sample CheckResult for testing."""
    passed = FunctionResult(
        function="square",
        file="src/math.py",
        line=5,
        level_requested=1,
        level_achieved=1,
        status=CheckStatus.PASSED,
    )
    failed = FunctionResult(
        function="add",
        file="src/math.py",
        line=15,
        level_requested=1,
        level_achieved=0,
        status=CheckStatus.FAILED,
        details=(
            Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
                finding_type="violation",
                message="Missing @icontract.require",
                suggestion="Add @icontract.require(lambda ...: ...)",
            ),
        ),
    )
    return make_check_result((passed, failed), level_requested=1, duration_seconds=0.05)


class TestFormatHuman:
    """Tests for human-readable formatting."""

    def test_contains_status(self) -> None:
        result = _make_sample_result()
        output = format_human(result)
        assert "FAILED" in output

    def test_contains_function_names(self) -> None:
        result = _make_sample_result()
        output = format_human(result)
        # Failed functions are shown by name, passing ones are summarized
        assert "add" in output

    def test_contains_summary(self) -> None:
        result = _make_sample_result()
        output = format_human(result)
        assert "2 functions checked" in output
        assert "1 passed" in output
        assert "1 failed" in output

    def test_contains_pass_fail_markers(self) -> None:
        result = _make_sample_result()
        output = format_human(result)
        # Compact format only shows [FAIL] markers; passing is summarized as "N passed"
        assert "[FAIL]" in output

    def test_contains_suggestion(self) -> None:
        result = _make_sample_result()
        output = format_human(result)
        assert "icontract.require" in output

    def test_passed_result(self) -> None:
        result = make_check_result((), level_requested=1, duration_seconds=0.01)
        output = format_human(result)
        assert "PASSED" in output

    def test_file_grouping(self) -> None:
        result = _make_sample_result()
        output = format_human(result)
        assert "src/math.py" in output


class TestFormatJson:
    """Tests for JSON formatting."""

    def test_valid_json(self) -> None:
        result = _make_sample_result()
        output = format_json(result)
        parsed = json.loads(output)
        assert isinstance(parsed, dict)

    def test_has_version(self) -> None:
        result = _make_sample_result()
        output = format_json(result)
        parsed = json.loads(output)
        assert "version" in parsed
        assert parsed["version"] == "0.1.0"

    def test_has_timestamp(self) -> None:
        result = _make_sample_result()
        output = format_json(result)
        parsed = json.loads(output)
        assert "timestamp" in parsed

    def test_has_summary(self) -> None:
        result = _make_sample_result()
        output = format_json(result)
        parsed = json.loads(output)
        assert parsed["summary"]["total_functions"] == 2
        assert parsed["summary"]["passed"] == 1
        assert parsed["summary"]["failed"] == 1

    def test_has_results(self) -> None:
        result = _make_sample_result()
        output = format_json(result)
        parsed = json.loads(output)
        assert len(parsed["results"]) == 2

    def test_result_structure(self) -> None:
        result = _make_sample_result()
        output = format_json(result)
        parsed = json.loads(output)
        first = parsed["results"][0]
        assert "function" in first
        assert "file" in first
        assert "line" in first
        assert "status" in first
        assert "details" in first


class TestFormatHtml:
    """Tests for HTML report formatting."""

    def test_valid_html_structure(self) -> None:
        result = _make_sample_result()
        output = format_html(result)
        assert output.startswith("<!DOCTYPE html>")
        assert "</html>" in output

    def test_contains_title(self) -> None:
        result = _make_sample_result()
        output = format_html(result)
        assert "Serenecode Verification Report" in output

    def test_contains_status(self) -> None:
        result = _make_sample_result()
        output = format_html(result)
        assert "FAILED" in output

    def test_contains_function_names(self) -> None:
        result = _make_sample_result()
        output = format_html(result)
        assert "square" in output
        assert "add" in output

    def test_contains_summary_counts(self) -> None:
        result = _make_sample_result()
        output = format_html(result)
        assert ">2<" in output  # total
        assert ">1<" in output  # passed/failed

    def test_contains_file_path(self) -> None:
        result = _make_sample_result()
        output = format_html(result)
        assert "src/math.py" in output

    def test_contains_suggestion(self) -> None:
        result = _make_sample_result()
        output = format_html(result)
        assert "icontract.require" in output

    def test_passed_result(self) -> None:
        result = make_check_result((), level_requested=1, duration_seconds=0.01)
        output = format_html(result)
        assert "PASSED" in output

    def test_contains_level_badges(self) -> None:
        result = _make_sample_result()
        output = format_html(result)
        assert "badge" in output

    def test_contains_version(self) -> None:
        result = _make_sample_result()
        output = format_html(result)
        assert "0.1.0" in output

    def test_escapes_html_special_chars(self) -> None:
        detail = Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="structural",
            finding_type="violation",
            message="Value <script>alert('xss')</script> is invalid",
        )
        func = FunctionResult(
            function="test_func",
            file="test.py",
            line=1,
            level_requested=1,
            level_achieved=0,
            status=CheckStatus.FAILED,
            details=(detail,),
        )
        result = make_check_result((func,), level_requested=1, duration_seconds=0.01)
        output = format_html(result)
        assert "<script>" not in output
        assert "&lt;script&gt;" in output

    def test_counterexample_in_html(self) -> None:
        detail = Detail(
            level=VerificationLevel.SYMBOLIC,
            tool="crosshair",
            finding_type="counterexample",
            message="Postcondition violated",
            counterexample={"x": -1},
        )
        func = FunctionResult(
            function="abs_val",
            file="test.py",
            line=5,
            level_requested=4,
            level_achieved=3,
            status=CheckStatus.FAILED,
            details=(detail,),
        )
        result = make_check_result((func,), level_requested=4, duration_seconds=0.5)
        output = format_html(result)
        assert "Counterexample" in output
        assert "x" in output
