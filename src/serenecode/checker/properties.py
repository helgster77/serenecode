"""Property-based testing checker for Serenecode (Level 3).

This module implements Level 3 verification: it transforms results from
property-based testing backends into structured CheckResult objects.
The actual test execution is delegated to adapters.

This is a core module — no I/O operations are permitted. Test results
are received as structured data, not generated here.
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
from serenecode.ports.property_tester import PropertyFinding


@icontract.require(
    lambda findings: isinstance(findings, list),
    "findings must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
def transform_property_results(
    findings: list[PropertyFinding],
    file_path: str,
    duration_seconds: float,
) -> CheckResult:
    """Transform property-based testing findings into a CheckResult.

    Maps each PropertyFinding to a FunctionResult with appropriate
    status and detail information including counterexamples.

    Args:
        findings: List of property findings from a testing adapter.
        file_path: Source file path for reporting.
        duration_seconds: How long the testing took.

    Returns:
        A CheckResult containing all property testing results.
    """
    func_results: list[FunctionResult] = []

    # Loop invariant: func_results contains transformed results for findings[0..i]
    for finding in findings:
        details: list[Detail] = []

        if finding.passed:
            status = CheckStatus.PASSED
            details.append(Detail(
                level=VerificationLevel.PROPERTIES,
                tool="hypothesis",
                finding_type="verified",
                message=f"Property tests passed for '{finding.function_name}'",
            ))
        else:
            status = CheckStatus.FAILED
            detail = Detail(
                level=VerificationLevel.PROPERTIES,
                tool="hypothesis",
                finding_type=finding.finding_type,
                message=finding.message,
                counterexample=finding.counterexample,
                suggestion=_suggest_fix(finding),
            )
            details.append(detail)

        func_results.append(FunctionResult(
            function=finding.function_name,
            file=file_path,
            line=1,  # line info not available from property testing
            level_requested=3,
            level_achieved=3 if finding.passed else 2,
            status=status,
            details=tuple(details),
        ))

    return make_check_result(
        tuple(func_results),
        level_requested=3,
        duration_seconds=duration_seconds,
    )


@icontract.require(
    lambda finding: isinstance(finding, PropertyFinding),
    "finding must be a PropertyFinding",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be None or a string",
)
def _suggest_fix(finding: PropertyFinding) -> str | None:
    """Generate a fix suggestion from a property finding.

    Args:
        finding: The property finding to generate a suggestion for.

    Returns:
        A suggestion string, or None if no suggestion can be generated.
    """
    if finding.finding_type == "postcondition_violated":
        if finding.counterexample:
            return (
                f"Postcondition violated with inputs: {finding.counterexample}. "
                "Fix the implementation or tighten the precondition."
            )
        return "Postcondition violated. Review the implementation logic."
    elif finding.finding_type == "crash":
        return (
            f"Function crashed with {finding.exception_type}: {finding.exception_message}. "
            "Add a precondition to exclude this input or fix the implementation."
        )
    return None
