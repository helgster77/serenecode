"""Coverage-gap tests for reporter._format_human_file_sections.

Targets the "N skipped" and "N exempt" summary parts, which only appear
when a file mixes those statuses with failed/passed results.
"""

from __future__ import annotations

from serenecode.models import (
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)
from serenecode.reporter import format_human


def _result(
    function: str,
    status: CheckStatus,
    details: tuple[Detail, ...] = (),
) -> FunctionResult:
    return FunctionResult(
        function=function,
        file="src/mixed.py",
        line=1,
        level_requested=1,
        level_achieved=0 if status == CheckStatus.FAILED else 1,
        status=status,
        details=details,
    )


class TestFormatHumanMixedStatuses:
    """Skipped and exempt counts appear in the per-file summary line."""

    def test_skipped_and_exempt_counts_are_listed(self) -> None:
        failed = _result("broken", CheckStatus.FAILED, (Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="structural",
            finding_type="violation",
            message="Missing @icontract.require",
            suggestion="Add @icontract.require(lambda ...: ...)",
        ),))
        skipped = _result("unimportable", CheckStatus.SKIPPED, (Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="structural",
            finding_type="unavailable",
            message="module not importable",
        ),))
        exempt = _result("adapter_func", CheckStatus.EXEMPT)
        passed = _result("fine", CheckStatus.PASSED)

        check_result = make_check_result(
            (failed, skipped, exempt, passed),
            level_requested=1,
            duration_seconds=0.01,
        )
        output = format_human(check_result)
        assert "1 passed" in output
        assert "1 failed" in output
        assert "1 skipped" in output
        assert "1 exempt" in output
        assert "src/mixed.py" in output

    def test_summary_line_orders_parts(self) -> None:
        failed = _result("broken", CheckStatus.FAILED, (Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="structural",
            finding_type="violation",
            message="Missing contract",
        ),))
        skipped = _result("unimportable", CheckStatus.SKIPPED)
        exempt = _result("adapter_func", CheckStatus.EXEMPT)

        check_result = make_check_result(
            (failed, skipped, exempt),
            level_requested=1,
            duration_seconds=0.01,
        )
        output = format_human(check_result)
        assert "src/mixed.py — 1 failed, 1 skipped, 1 exempt" in output
