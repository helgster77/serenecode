"""Symbolic verification checker for Serenecode (Level 4).

This module implements Level 4 verification: it transforms results from
symbolic execution backends into structured CheckResult objects.
The actual verification is delegated to adapters.

This is a core module — no I/O operations are permitted. Verification
results are received as structured data, not generated here.
"""

from __future__ import annotations

import icontract

from serenecode.models import (
    CheckResult,
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)
from serenecode.ports.symbolic_checker import SymbolicFinding


@icontract.require(
    lambda findings: isinstance(findings, list),
    "findings must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
def transform_symbolic_results(
    findings: list[SymbolicFinding],
    file_path: str,
    duration_seconds: float,
) -> CheckResult:
    """Transform symbolic verification findings into a CheckResult.

    Maps each SymbolicFinding to a FunctionResult with appropriate
    status, counterexamples, and suggestions.

    Args:
        findings: List of symbolic findings from a verification adapter.
        file_path: Source file path for reporting.
        duration_seconds: How long the verification took.

    Returns:
        A CheckResult containing all symbolic verification results.
    """
    func_results: list[FunctionResult] = []

    # Loop invariant: func_results contains transformed results for findings[0..i]
    for finding in findings:
        details: list[Detail] = []
        status: CheckStatus
        level_achieved: int

        if finding.outcome == "verified":
            status = CheckStatus.PASSED
            level_achieved = 4
            details.append(Detail(
                level=VerificationLevel.SYMBOLIC,
                tool="crosshair",
                finding_type="verified",
                message=f"Symbolically verified: '{finding.function_name}'",
            ))
        elif finding.outcome == "counterexample":
            status = CheckStatus.FAILED
            level_achieved = 3
            details.append(Detail(
                level=VerificationLevel.SYMBOLIC,
                tool="crosshair",
                finding_type="counterexample",
                message=finding.message,
                counterexample=finding.counterexample,
                suggestion=_suggest_fix_symbolic(finding),
            ))
        elif finding.outcome == "timeout":
            status = CheckStatus.SKIPPED
            level_achieved = 3
            details.append(Detail(
                level=VerificationLevel.SYMBOLIC,
                tool="crosshair",
                finding_type="timeout",
                message=f"Symbolic verification timed out for '{finding.function_name}'",
            ))
        elif finding.outcome == "unsupported":
            status = CheckStatus.SKIPPED
            level_achieved = 3
            details.append(Detail(
                level=VerificationLevel.SYMBOLIC,
                tool="crosshair",
                finding_type="unsupported",
                message=f"Symbolic verification unsupported for '{finding.function_name}'",
            ))
        else:
            status = CheckStatus.FAILED
            level_achieved = 3
            details.append(Detail(
                level=VerificationLevel.SYMBOLIC,
                tool="crosshair",
                finding_type="error",
                message=finding.message,
            ))

        func_results.append(FunctionResult(
            function=finding.function_name,
            file=file_path,
            line=1,
            level_requested=4,
            level_achieved=level_achieved,
            status=status,
            details=tuple(details),
        ))

    return make_check_result(
        tuple(func_results),
        level_requested=4,
        duration_seconds=duration_seconds,
    )


@icontract.require(
    lambda finding: isinstance(finding, SymbolicFinding),
    "finding must be a SymbolicFinding",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be None or a string",
)
def _suggest_fix_symbolic(finding: SymbolicFinding) -> str | None:
    """Generate a fix suggestion from a symbolic finding.

    Args:
        finding: The symbolic finding to generate a suggestion for.

    Returns:
        A suggestion string, or None.
    """
    if finding.counterexample:
        return (
            f"Counterexample found: {finding.counterexample}. "
            "Fix the implementation or add a precondition to exclude this input."
        )
    if finding.condition:
        return f"Condition violated: {finding.condition}. Review the postcondition or implementation."
    return "Symbolic verification found a violation. Review the function logic."
