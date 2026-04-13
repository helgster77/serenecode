"""Symbolic verification checker for Serenecode (Level 5).

This module implements Level 5 verification: it transforms results from
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
    lambda findings, result: len(result.results) == len(findings),
    "output count must match input findings count",
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
        func_results.append(_transform_single_finding(finding, file_path))

    return make_check_result(
        tuple(func_results), level_requested=5, duration_seconds=duration_seconds,
    )


def _transform_single_finding(
    finding: SymbolicFinding,
    file_path: str,
) -> FunctionResult:
    """Transform a single symbolic finding into a FunctionResult."""
    status, level_achieved, detail = _classify_symbolic_finding(finding)
    return FunctionResult(
        function=finding.function_name, file=file_path, line=1,
        level_requested=5, level_achieved=level_achieved,
        status=status, details=(detail,),
    )


def _classify_symbolic_finding(
    finding: SymbolicFinding,
) -> tuple[CheckStatus, int, Detail]:
    """Classify a symbolic finding into status, level, and detail."""
    if finding.outcome == "verified":
        return CheckStatus.PASSED, 5, Detail(
            level=VerificationLevel.SYMBOLIC, tool="crosshair",
            finding_type="verified",
            message=f"No counterexample found within analysis bounds: '{finding.function_name}'",
        )
    if finding.outcome == "counterexample":
        return CheckStatus.FAILED, 4, Detail(
            level=VerificationLevel.SYMBOLIC, tool="crosshair",
            finding_type="counterexample",
            message=finding.message,
            counterexample=finding.counterexample,
            suggestion=_suggest_fix_symbolic(finding),
        )
    if finding.outcome == "timeout":
        return CheckStatus.SKIPPED, 4, Detail(
            level=VerificationLevel.SYMBOLIC, tool="crosshair",
            finding_type="timeout",
            message=f"Symbolic verification timed out for '{finding.function_name}'",
            suggestion=(
                "The solver ran out of time. Options: "
                "(1) increase --per-condition-timeout or --module-timeout, "
                "(2) simplify the function logic or contracts, "
                "(3) split the function into smaller pieces, "
                "(4) add tighter preconditions to reduce the search space"
            ),
        )
    if finding.outcome == "unsupported":
        return CheckStatus.EXEMPT, 4, Detail(
            level=VerificationLevel.SYMBOLIC, tool="crosshair",
            finding_type="unsupported",
            message=f"Symbolic verification unsupported for '{finding.function_name}'",
            suggestion=(
                "This function cannot be symbolically verified — "
                "it has non-primitive parameter types that the solver cannot generate. "
                "Ensure it is covered by property-based tests (L3) or explicit unit tests"
            ),
        )
    return CheckStatus.FAILED, 4, Detail(
        level=VerificationLevel.SYMBOLIC, tool="crosshair",
        finding_type="error", message=finding.message,
        suggestion=(
            "Symbolic verification encountered an internal error. "
            "Check that the module imports cleanly and that all dependencies are installed"
        ),
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
    if finding.counterexample is not None and isinstance(finding.counterexample, dict) and finding.counterexample:
        inputs = ", ".join(f"{k}={v}" for k, v in finding.counterexample.items())
        fix_steps = (
            f"Counterexample: {inputs}. "
            "To fix: (1) if the inputs are invalid, add a @icontract.require "
            "precondition to exclude them; (2) if the inputs are valid, fix the "
            "implementation so the postcondition holds"
        )
        if finding.condition:
            fix_steps += f"; violated condition: {finding.condition}"
        return fix_steps
    if finding.condition:
        return (
            f"Condition '{finding.condition}' violated. "
            "Either fix the implementation to satisfy the postcondition, "
            "or add a precondition to narrow the valid input domain"
        )
    return (
        "Symbolic verification found a violation. "
        "Read the function's postconditions and check which one can fail"
    )
