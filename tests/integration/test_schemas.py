"""Integration tests for the wire-shape conversions in serenecode.mcp.schemas."""

from __future__ import annotations

from serenecode.mcp.schemas import (
    CheckResponse,
    FindingDTO,
    response_to_dict,
    to_check_response,
)
from serenecode.models import (
    CheckResult,
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)


class TestToCheckResponse:
    """Tests for projecting a CheckResult into the wire shape."""

    def test_passed_result_yields_empty_findings(self) -> None:
        passed = FunctionResult(
            function="foo",
            file="bar.py",
            line=10,
            level_requested=1,
            level_achieved=1,
            status=CheckStatus.PASSED,
            details=(),
        )
        result = make_check_result((passed,), level_requested=1, duration_seconds=0.0)
        response = to_check_response(result)
        assert response.passed is True
        assert response.findings == []
        assert response.summary["passed"] == 1
        assert response.summary["failed"] == 0

    def test_failed_result_includes_finding(self) -> None:
        failed = FunctionResult(
            function="foo",
            file="bar.py",
            line=10,
            level_requested=1,
            level_achieved=0,
            status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
                finding_type="violation",
                message="missing contract",
                suggestion="add @ensure",
            ),),
        )
        result = make_check_result((failed,), level_requested=1, duration_seconds=0.0)
        response = to_check_response(result)
        assert response.passed is False
        assert len(response.findings) == 1
        finding = response.findings[0]
        assert finding.function == "foo"
        assert finding.file == "bar.py"
        assert finding.line == 10
        assert finding.message == "missing contract"
        assert finding.suggestion == "add @ensure"

    def test_skipped_result_included_in_findings(self) -> None:
        skipped = FunctionResult(
            function="foo",
            file="bar.py",
            line=1,
            level_requested=3,
            level_achieved=2,
            status=CheckStatus.SKIPPED,
            details=(Detail(
                level=VerificationLevel.COVERAGE,
                tool="coverage",
                finding_type="error",
                message="adapter timed out",
            ),),
        )
        result = make_check_result((skipped,), level_requested=3, duration_seconds=120.0)
        response = to_check_response(result)
        assert len(response.findings) == 1
        assert response.findings[0].status == "skipped"

    def test_exempt_result_dropped(self) -> None:
        exempt = FunctionResult(
            function="<module>",
            file="cli.py",
            line=1,
            level_requested=1,
            level_achieved=0,
            status=CheckStatus.EXEMPT,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
                finding_type="exempt",
                message="exempt module",
            ),),
        )
        result = make_check_result((exempt,), level_requested=1, duration_seconds=0.0)
        response = to_check_response(result)
        # Exempt details should NOT appear in findings (only failed/skipped do)
        assert response.findings == []
        assert response.summary["exempt"] == 1

    def test_dead_code_advisory_exempt_is_included(self) -> None:
        passed = FunctionResult(
            function="live",
            file="app.py",
            line=1,
            level_requested=1,
            level_achieved=1,
            status=CheckStatus.PASSED,
            details=(),
        )
        advisory = FunctionResult(
            function="stale",
            file="app.py",
            line=7,
            level_requested=1,
            level_achieved=1,
            status=CheckStatus.EXEMPT,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="dead_code",
                finding_type="dead_code",
                message="unused function 'stale'",
                suggestion="Ask the user whether this likely dead code should be removed.",
            ),),
        )
        result = make_check_result((passed, advisory), level_requested=1, duration_seconds=0.0)
        response = to_check_response(result)
        assert response.passed is True
        assert response.verdict == "complete"
        assert response.summary["advisory_count"] == 1
        assert len(response.findings) == 1
        assert response.findings[0].status == "exempt"
        assert response.findings[0].finding_type == "dead_code"

    def test_response_to_dict_is_json_friendly(self) -> None:
        import json
        finding = FindingDTO(
            file="x.py",
            line=1,
            function="f",
            status="failed",
            level_requested=1,
            level_achieved=0,
            finding_type="violation",
            message="m",
            suggestion="s",
            counterexample={"x": 1},
        )
        response = CheckResponse(
            passed=False,
            level_requested=1,
            level_achieved=0,
            verdict="failed",
            duration_seconds=0.1,
            summary={"passed": 0, "failed": 1, "skipped": 0, "exempt": 0, "advisory_count": 0},
            findings=[finding],
        )
        d = response_to_dict(response)
        # Round-trip through JSON to confirm everything is serializable
        text = json.dumps(d)
        assert "violation" in text
        assert "x.py" in text
