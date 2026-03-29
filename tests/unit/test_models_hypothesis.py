"""Property-based tests for Serenecode data models using Hypothesis.

Tests structural invariants and serialization round-trips across
randomly generated model instances.
"""

from __future__ import annotations

import json

from hypothesis import example, given, settings
from hypothesis import strategies as st

import icontract
import pytest

from serenecode.models import (
    CheckResult,
    CheckStatus,
    CheckSummary,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)

# Strategies for model construction
levels = st.sampled_from(list(VerificationLevel))
statuses = st.sampled_from(list(CheckStatus))
tools = st.sampled_from(["structural", "mypy", "hypothesis", "crosshair"])
finding_types = st.sampled_from(["violation", "counterexample", "timeout", "error"])
non_empty_text = st.text(min_size=1, max_size=50).filter(lambda s: s.strip())


class TestDetailProperty:
    """Property-based tests for Detail dataclass."""

    @given(
        level=levels,
        tool=tools,
        finding_type=finding_types,
        message=non_empty_text,
    )
    def test_construction_always_succeeds_with_valid_inputs(
        self, level: VerificationLevel, tool: str, finding_type: str, message: str,
    ) -> None:
        detail = Detail(level=level, tool=tool, finding_type=finding_type, message=message)
        assert detail.level == level
        assert detail.message == message

    @given(
        level=levels,
        tool=tools,
        finding_type=finding_types,
        message=non_empty_text,
    )
    def test_to_dict_roundtrip(
        self, level: VerificationLevel, tool: str, finding_type: str, message: str,
    ) -> None:
        detail = Detail(level=level, tool=tool, finding_type=finding_type, message=message)
        d = detail.to_dict()
        assert d["level"] == level.value
        assert d["tool"] == tool
        assert d["message"] == message

    @given(
        level=levels,
        tool=tools,
        finding_type=finding_types,
        message=non_empty_text,
    )
    def test_to_dict_json_serializable(
        self, level: VerificationLevel, tool: str, finding_type: str, message: str,
    ) -> None:
        detail = Detail(level=level, tool=tool, finding_type=finding_type, message=message)
        json_str = json.dumps(detail.to_dict())
        parsed = json.loads(json_str)
        assert parsed["level"] == level.value


class TestFunctionResultProperty:
    """Property-based tests for FunctionResult dataclass."""

    @given(
        function=non_empty_text,
        file=non_empty_text,
        line=st.integers(min_value=1, max_value=100000),
        level_requested=st.integers(min_value=1, max_value=6),
        level_achieved=st.integers(min_value=0, max_value=6),
        status=statuses,
    )
    def test_construction_with_valid_inputs(
        self, function: str, file: str, line: int,
        level_requested: int, level_achieved: int, status: CheckStatus,
    ) -> None:
        fr = FunctionResult(
            function=function, file=file, line=line,
            level_requested=level_requested, level_achieved=level_achieved,
            status=status,
        )
        assert fr.line >= 1
        assert fr.function == function

    @given(line=st.integers(max_value=0))
    @example(line=0)
    @example(line=-1)
    @example(line=-999)
    def test_invalid_line_rejected(self, line: int) -> None:
        from tests.conftest import assert_violation_or_skip
        assert_violation_or_skip(lambda: FunctionResult(
            function="f", file="f.py", line=line,
            level_requested=1, level_achieved=1, status=CheckStatus.PASSED,
        ))

    @given(
        function=non_empty_text,
        file=non_empty_text,
        line=st.integers(min_value=1, max_value=1000),
    )
    def test_to_dict_preserves_data(
        self, function: str, file: str, line: int,
    ) -> None:
        fr = FunctionResult(
            function=function, file=file, line=line,
            level_requested=1, level_achieved=1, status=CheckStatus.PASSED,
        )
        d = fr.to_dict()
        assert d["function"] == function
        assert d["file"] == file
        assert d["line"] == line


class TestCheckSummaryProperty:
    """Property-based tests for CheckSummary dataclass."""

    @given(
        passed=st.integers(min_value=0, max_value=100),
        failed=st.integers(min_value=0, max_value=100),
        skipped=st.integers(min_value=0, max_value=100),
        exempt=st.integers(min_value=0, max_value=100),
        duration=st.floats(min_value=0, max_value=1000, allow_nan=False),
    )
    def test_counts_must_sum(
        self, passed: int, failed: int, skipped: int, exempt: int, duration: float,
    ) -> None:
        total = passed + failed + skipped + exempt
        summary = CheckSummary(
            total_functions=total,
            passed_count=passed,
            failed_count=failed,
            skipped_count=skipped,
            exempt_count=exempt,
            duration_seconds=duration,
        )
        assert summary.total_functions == summary.passed_count + summary.failed_count + summary.skipped_count + summary.exempt_count

    @given(
        passed=st.integers(min_value=0, max_value=60),
        failed=st.integers(min_value=0, max_value=60),
        skipped=st.integers(min_value=0, max_value=60),
        exempt=st.integers(min_value=0, max_value=60),
    )
    def test_mismatched_total_rejected(
        self, passed: int, failed: int, skipped: int, exempt: int,
    ) -> None:
        from tests.conftest import assert_violation_or_skip
        total = passed + failed + skipped + exempt + 1  # off by one
        assert_violation_or_skip(lambda: CheckSummary(
            total_functions=total,
            passed_count=passed,
            failed_count=failed,
            skipped_count=skipped,
            exempt_count=exempt,
            duration_seconds=0.1,
        ))


class TestMakeCheckResultProperty:
    """Property-based tests for make_check_result factory."""

    @given(
        n_passed=st.integers(min_value=0, max_value=10),
        n_failed=st.integers(min_value=0, max_value=10),
        n_skipped=st.integers(min_value=0, max_value=10),
        level=st.integers(min_value=1, max_value=6),
        duration=st.floats(min_value=0, max_value=100, allow_nan=False),
    )
    def test_summary_counts_match_results(
        self, n_passed: int, n_failed: int, n_skipped: int,
        level: int, duration: float,
    ) -> None:
        results: list[FunctionResult] = []
        # Loop invariant: results contains n_passed passed + n_failed failed + n_skipped skipped so far
        for i in range(n_passed):
            results.append(FunctionResult(
                function=f"pass_{i}", file="f.py", line=i + 1,
                level_requested=level, level_achieved=level,
                status=CheckStatus.PASSED,
            ))
        for i in range(n_failed):
            results.append(FunctionResult(
                function=f"fail_{i}", file="f.py", line=100 + i,
                level_requested=level, level_achieved=0,
                status=CheckStatus.FAILED,
            ))
        for i in range(n_skipped):
            results.append(FunctionResult(
                function=f"skip_{i}", file="f.py", line=200 + i,
                level_requested=level, level_achieved=level,
                status=CheckStatus.SKIPPED,
            ))

        check = make_check_result(tuple(results), level_requested=level, duration_seconds=duration)
        assert check.summary.total_functions == n_passed + n_failed + n_skipped
        assert check.summary.passed_count == n_passed
        assert check.summary.failed_count == n_failed
        assert check.summary.skipped_count == n_skipped
        assert check.passed == (n_failed == 0 and n_skipped == 0)

    @given(duration=st.floats(min_value=0, max_value=100, allow_nan=False))
    def test_empty_results_always_passes(self, duration: float) -> None:
        check = make_check_result((), level_requested=1, duration_seconds=duration)
        assert check.passed is True
        assert check.summary.total_functions == 0

    @given(
        n_results=st.integers(min_value=1, max_value=20),
        level=st.integers(min_value=1, max_value=6),
    )
    def test_failures_property_filters_correctly(
        self, n_results: int, level: int,
    ) -> None:
        results: list[FunctionResult] = []
        expected_failures = 0
        # Loop invariant: results has i entries, expected_failures counts FAILED
        for i in range(n_results):
            status = CheckStatus.FAILED if i % 3 == 0 else CheckStatus.PASSED
            if status == CheckStatus.FAILED:
                expected_failures += 1
            results.append(FunctionResult(
                function=f"func_{i}", file="f.py", line=i + 1,
                level_requested=level, level_achieved=level if status == CheckStatus.PASSED else 0,
                status=status,
            ))
        check = make_check_result(tuple(results), level_requested=level, duration_seconds=0.0)
        assert len(check.failures) == expected_failures
