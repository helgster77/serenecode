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
    verdict: str
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

    Drops PASSED entries from `findings`. EXEMPT entries stay hidden by
    default except for dead-code advisories, which remain visible so
    agents can ask the user whether to remove or allowlist them.
    """
    findings: list[FindingDTO] = []
    for r in check_result.results:
        include_result = r.status in (CheckStatus.FAILED, CheckStatus.SKIPPED)
        if r.status == CheckStatus.EXEMPT:
            include_result = any(
                detail.finding_type == "dead_code"
                for detail in r.details
            )
        if not include_result:
            continue
        for d in r.details:
            if (
                r.status == CheckStatus.EXEMPT
                and d.finding_type != "dead_code"
            ):
                continue
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
        verdict=check_result.summary.verdict,
        duration_seconds=check_result.summary.duration_seconds,
        summary={
            "passed": check_result.summary.passed_count,
            "failed": check_result.summary.failed_count,
            "skipped": check_result.summary.skipped_count,
            "exempt": check_result.summary.exempt_count,
            "advisory_count": check_result.summary.advisory_count,
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
