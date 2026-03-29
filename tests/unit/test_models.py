"""Tests for Serenecode data models.

Tests construction, invariants, serialization, and the make_check_result
factory function.
"""

from __future__ import annotations

import json

import icontract
import pytest

from tests.conftest import assert_violation_or_skip

from serenecode.models import (
    CheckResult,
    CheckStatus,
    CheckSummary,
    Detail,
    ExitCode,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)


class TestVerificationLevel:
    """Tests for VerificationLevel enum."""

    def test_values(self) -> None:
        assert VerificationLevel.STRUCTURAL.value == 1
        assert VerificationLevel.TYPES.value == 2
        assert VerificationLevel.COVERAGE.value == 3
        assert VerificationLevel.PROPERTIES.value == 4
        assert VerificationLevel.SYMBOLIC.value == 5
        assert VerificationLevel.COMPOSITIONAL.value == 6

    def test_all_levels_present(self) -> None:
        assert len(VerificationLevel) == 6


class TestCheckStatus:
    """Tests for CheckStatus enum."""

    def test_values(self) -> None:
        assert CheckStatus.PASSED.value == "passed"
        assert CheckStatus.FAILED.value == "failed"
        assert CheckStatus.SKIPPED.value == "skipped"


class TestExitCode:
    """Tests for ExitCode enum."""

    def test_values(self) -> None:
        assert ExitCode.PASSED == 0
        assert ExitCode.STRUCTURAL == 1
        assert ExitCode.TYPES == 2
        assert ExitCode.COVERAGE == 3
        assert ExitCode.PROPERTIES == 4
        assert ExitCode.SYMBOLIC == 5
        assert ExitCode.COMPOSITIONAL == 6
        assert ExitCode.INTERNAL == 10

    def test_is_int(self) -> None:
        assert isinstance(ExitCode.PASSED, int)


class TestDetail:
    """Tests for Detail frozen dataclass."""

    def test_construction(self) -> None:
        detail = Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="structural",
            finding_type="violation",
            message="Missing @icontract.require",
        )
        assert detail.level == VerificationLevel.STRUCTURAL
        assert detail.tool == "structural"
        assert detail.finding_type == "violation"
        assert detail.message == "Missing @icontract.require"
        assert detail.counterexample is None
        assert detail.suggestion is None

    def test_construction_with_optionals(self) -> None:
        detail = Detail(
            level=VerificationLevel.SYMBOLIC,
            tool="crosshair",
            finding_type="counterexample",
            message="Postcondition violated",
            counterexample={"x": -1},
            suggestion="Add precondition: x >= 0",
        )
        assert detail.counterexample == {"x": -1}
        assert detail.suggestion == "Add precondition: x >= 0"

    def test_empty_message_rejected(self) -> None:
        assert_violation_or_skip(lambda: Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="structural",
            finding_type="violation",
            message="",
        ))

    def test_whitespace_message_rejected(self) -> None:
        assert_violation_or_skip(lambda: Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="structural",
            finding_type="violation",
            message="   ",
        ))

    def test_frozen(self) -> None:
        detail = Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="structural",
            finding_type="violation",
            message="test",
        )
        with pytest.raises(AttributeError):
            detail.message = "changed"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        detail = Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="structural",
            finding_type="violation",
            message="Missing contract",
            suggestion="Add @icontract.require",
        )
        d = detail.to_dict()
        assert d["level"] == 1
        assert d["tool"] == "structural"
        assert d["type"] == "violation"
        assert d["message"] == "Missing contract"
        assert d["suggestion"] == "Add @icontract.require"
        assert "counterexample" not in d

    def test_to_dict_with_counterexample(self) -> None:
        detail = Detail(
            level=VerificationLevel.SYMBOLIC,
            tool="crosshair",
            finding_type="counterexample",
            message="Failed",
            counterexample={"x": 5},
        )
        d = detail.to_dict()
        assert d["counterexample"] == {"x": 5}


class TestFunctionResult:
    """Tests for FunctionResult frozen dataclass."""

    def test_construction(self) -> None:
        result = FunctionResult(
            function="core.pricing.calculate_total",
            file="src/core/pricing.py",
            line=15,
            level_requested=5,
            level_achieved=5,
            status=CheckStatus.PASSED,
        )
        assert result.function == "core.pricing.calculate_total"
        assert result.line == 15
        assert result.details == ()

    def test_line_zero_rejected(self) -> None:
        assert_violation_or_skip(lambda: FunctionResult(
            function="test", file="test.py", line=0,
            level_requested=1, level_achieved=1, status=CheckStatus.PASSED,
        ))

    def test_negative_line_rejected(self) -> None:
        assert_violation_or_skip(lambda: FunctionResult(
            function="test", file="test.py", line=-1,
            level_requested=1, level_achieved=1, status=CheckStatus.PASSED,
        ))

    def test_empty_function_name_rejected(self) -> None:
        assert_violation_or_skip(lambda: FunctionResult(
            function="", file="test.py", line=1,
            level_requested=1, level_achieved=1, status=CheckStatus.PASSED,
        ))

    def test_empty_file_rejected(self) -> None:
        assert_violation_or_skip(lambda: FunctionResult(
            function="test", file="", line=1,
            level_requested=1, level_achieved=1, status=CheckStatus.PASSED,
        ))

    def test_to_dict(self) -> None:
        detail = Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="structural",
            finding_type="violation",
            message="Missing contract",
        )
        result = FunctionResult(
            function="test_func",
            file="test.py",
            line=10,
            level_requested=1,
            level_achieved=1,
            status=CheckStatus.FAILED,
            details=(detail,),
        )
        d = result.to_dict()
        assert d["function"] == "test_func"
        assert d["status"] == "failed"
        assert len(d["details"]) == 1  # type: ignore[arg-type]


class TestCheckSummary:
    """Tests for CheckSummary frozen dataclass."""

    def test_construction(self) -> None:
        summary = CheckSummary(
            total_functions=10,
            passed_count=8,
            failed_count=1,
            skipped_count=1,
            exempt_count=0,
            duration_seconds=0.5,
        )
        assert summary.total_functions == 10

    def test_counts_must_sum_to_total(self) -> None:
        assert_violation_or_skip(lambda: CheckSummary(
            total_functions=10, passed_count=5, failed_count=3,
            skipped_count=1, exempt_count=0, duration_seconds=0.1,  # 5+3+1+0=9 != 10
        ))

    def test_negative_counts_rejected(self) -> None:
        assert_violation_or_skip(lambda: CheckSummary(
            total_functions=5, passed_count=-1, failed_count=3,
            skipped_count=3, exempt_count=0, duration_seconds=0.1,
        ))

    def test_to_dict(self) -> None:
        summary = CheckSummary(
            total_functions=10,
            passed_count=8,
            failed_count=1,
            skipped_count=1,
            exempt_count=0,
            duration_seconds=0.5,
        )
        d = summary.to_dict()
        assert d == {
            "total_functions": 10,
            "passed": 8,
            "failed": 1,
            "skipped": 1,
            "exempt": 0,
        }


class TestCheckResult:
    """Tests for CheckResult frozen dataclass."""

    def _make_passed_result(self) -> FunctionResult:
        return FunctionResult(
            function="test_func",
            file="test.py",
            line=1,
            level_requested=1,
            level_achieved=1,
            status=CheckStatus.PASSED,
        )

    def _make_failed_result(self) -> FunctionResult:
        return FunctionResult(
            function="bad_func",
            file="test.py",
            line=5,
            level_requested=1,
            level_achieved=0,
            status=CheckStatus.FAILED,
            details=(
                Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="violation",
                    message="Missing contract",
                ),
            ),
        )

    def test_failures_property(self) -> None:
        passed = self._make_passed_result()
        failed = self._make_failed_result()
        summary = CheckSummary(
            total_functions=2,
            passed_count=1,
            failed_count=1,
            skipped_count=0,
            exempt_count=0,
            duration_seconds=0.1,
        )
        result = CheckResult(
            passed=False,
            level_requested=1,
            level_achieved=0,
            results=(passed, failed),
            summary=summary,
        )
        failures = result.failures
        assert len(failures) == 1
        assert failures[0].function == "bad_func"

    def test_to_json_valid(self) -> None:
        summary = CheckSummary(
            total_functions=1,
            passed_count=1,
            failed_count=0,
            skipped_count=0,
            exempt_count=0,
            duration_seconds=0.1,
        )
        result = CheckResult(
            passed=True,
            level_requested=1,
            level_achieved=1,
            results=(self._make_passed_result(),),
            summary=summary,
        )
        json_str = result.to_json()
        parsed = json.loads(json_str)
        assert parsed["version"] == "0.1.0"
        assert parsed["summary"]["total_functions"] == 1
        assert len(parsed["results"]) == 1


class TestMakeCheckResult:
    """Tests for the make_check_result factory function."""

    def test_all_passed(self) -> None:
        results = (
            FunctionResult(
                function="func_a",
                file="a.py",
                line=1,
                level_requested=1,
                level_achieved=1,
                status=CheckStatus.PASSED,
            ),
            FunctionResult(
                function="func_b",
                file="b.py",
                line=1,
                level_requested=1,
                level_achieved=1,
                status=CheckStatus.PASSED,
            ),
        )
        check = make_check_result(results, level_requested=1, duration_seconds=0.1)
        assert check.passed is True
        assert check.summary.passed_count == 2
        assert check.summary.failed_count == 0

    def test_with_failures(self) -> None:
        results = (
            FunctionResult(
                function="func_a",
                file="a.py",
                line=1,
                level_requested=1,
                level_achieved=1,
                status=CheckStatus.PASSED,
            ),
            FunctionResult(
                function="func_b",
                file="b.py",
                line=5,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
            ),
        )
        check = make_check_result(results, level_requested=1, duration_seconds=0.1)
        assert check.passed is False
        assert check.summary.failed_count == 1
        assert check.level_achieved == 0

    def test_empty_results(self) -> None:
        check = make_check_result((), level_requested=1, duration_seconds=0.0)
        assert check.passed is True
        assert check.summary.total_functions == 0

    def test_with_skipped(self) -> None:
        results = (
            FunctionResult(
                function="func_a",
                file="a.py",
                line=1,
                level_requested=3,
                level_achieved=1,
                status=CheckStatus.SKIPPED,
            ),
        )
        check = make_check_result(results, level_requested=3, duration_seconds=0.1)
        assert check.passed is False
        assert check.summary.skipped_count == 1
        assert check.level_achieved == 1
