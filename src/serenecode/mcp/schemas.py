"""Typed dataclasses for MCP tool inputs and outputs.

These mirror the structure of `CheckResult` / `FunctionResult` but are
flattened into the JSON-friendly shapes that MCP tools return. Keeping
the wire format here lets the tool layer stay a thin shim.

This module is part of the MCP composition root and is exempt from
full structural verification.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import icontract

from serenecode.models import CheckResult, CheckStatus


# no-invariant: pure JSON wire-format dataclass; structural fields validated by mypy
@dataclass(frozen=True)
class FindingDTO:
    """A single verification finding ready for the wire."""

    file: str
    line: int
    function: str
    status: str
    level_requested: int
    level_achieved: int
    finding_type: str
    message: str
    suggestion: str | None
    counterexample: dict[str, object] | None


# no-invariant: pure JSON wire-format dataclass; structural fields validated by mypy
@dataclass(frozen=True)
class CheckResponse:
    """Response payload for any tool that runs the verification pipeline."""

    passed: bool
    level_requested: int
    level_achieved: int
    duration_seconds: float
    summary: dict[str, int]
    findings: list[FindingDTO]


@icontract.require(
    lambda check_result: isinstance(check_result, CheckResult),
    "check_result must be a CheckResult",
)
@icontract.ensure(
    lambda check_result, result: result.level_requested == check_result.level_requested,
    "wire response must preserve the requested verification level",
)
def to_check_response(check_result: CheckResult) -> CheckResponse:
    """Project a CheckResult into the wire-shaped CheckResponse.

    Drops PASSED and EXEMPT entries from `findings` so agents see only
    actionable items by default. The summary preserves the full counts.
    """
    findings: list[FindingDTO] = []
    for r in check_result.results:
        if r.status not in (CheckStatus.FAILED, CheckStatus.SKIPPED):
            continue
        for d in r.details:
            findings.append(FindingDTO(
                file=r.file,
                line=r.line,
                function=r.function,
                status=r.status.value,
                level_requested=r.level_requested,
                level_achieved=r.level_achieved,
                finding_type=d.finding_type,
                message=d.message,
                suggestion=d.suggestion,
                counterexample=dict(d.counterexample) if d.counterexample else None,
            ))
    return CheckResponse(
        passed=check_result.passed,
        level_requested=check_result.level_requested,
        level_achieved=check_result.level_achieved,
        duration_seconds=check_result.summary.duration_seconds,
        summary={
            "passed": check_result.summary.passed_count,
            "failed": check_result.summary.failed_count,
            "skipped": check_result.summary.skipped_count,
            "exempt": check_result.summary.exempt_count,
        },
        findings=findings,
    )


@icontract.require(
    lambda response: isinstance(response, CheckResponse),
    "response must be a CheckResponse",
)
@icontract.ensure(
    lambda response, result: result["level_requested"] == response.level_requested,
    "JSON dict must preserve the level_requested field",
)
def response_to_dict(response: CheckResponse) -> dict[str, object]:
    """Serialize a CheckResponse into a JSON-friendly dict."""
    return asdict(response)
