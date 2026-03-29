"""Coverage analysis checker for Serenecode (Level 3).

This module implements Level 3 verification: it transforms results from
coverage analysis backends into structured CheckResult objects.
The actual analysis is delegated to adapters.

This is a core module — no I/O operations are permitted. Coverage
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
from serenecode.ports.coverage_analyzer import CoverageFinding


@icontract.require(
    lambda findings: isinstance(findings, list),
    "findings must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
@icontract.ensure(
    lambda findings, result: len(result.results) == len(findings),
    "output count must match input findings count",
)
def transform_coverage_results(
    findings: list[CoverageFinding],
    file_path: str,
    duration_seconds: float,
) -> CheckResult:
    """Transform coverage analysis findings into a CheckResult.

    Maps each CoverageFinding to a FunctionResult with appropriate
    status and detailed suggestions for uncovered paths.

    Args:
        findings: List of coverage findings from an analysis adapter.
        file_path: Source file path for reporting.
        duration_seconds: How long the analysis took.

    Returns:
        A CheckResult containing all coverage analysis results.
    """
    func_results: list[FunctionResult] = []

    # Loop invariant: func_results contains transformed results for findings[0..i]
    for finding in findings:
        details: list[Detail] = []
        status: CheckStatus
        level_achieved: int

        if finding.meets_threshold:
            status = CheckStatus.PASSED
            level_achieved = 3
            details.append(Detail(
                level=VerificationLevel.COVERAGE,
                tool="coverage",
                finding_type="sufficient_coverage",
                message=finding.message,
            ))
        else:
            status = CheckStatus.FAILED
            level_achieved = 2
            details.append(Detail(
                level=VerificationLevel.COVERAGE,
                tool="coverage",
                finding_type="insufficient_coverage",
                message=finding.message,
                suggestion=_build_suggestion(finding),
            ))

        func_results.append(FunctionResult(
            function=finding.function_name,
            file=file_path,
            line=finding.line_start,
            level_requested=3,
            level_achieved=level_achieved,
            status=status,
            details=tuple(details),
        ))

    return make_check_result(
        tuple(func_results),
        level_requested=3,
        duration_seconds=duration_seconds,
    )


@icontract.require(
    lambda finding: isinstance(finding, CoverageFinding),
    "finding must be a CoverageFinding",
)
@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty string",
)
def _build_suggestion(finding: CoverageFinding) -> str:
    """Build an agent-friendly suggestion string from a coverage finding.

    Args:
        finding: The coverage finding to generate a suggestion for.

    Returns:
        A detailed suggestion string with coverage info and test suggestions.
    """
    parts: list[str] = []
    parts.append(
        f"Coverage: {finding.line_coverage_percent:.0f}% lines, "
        f"{finding.branch_coverage_percent:.0f}% branches. "
        f"Uncovered lines: {_format_line_ranges(finding.uncovered_lines)}."
    )

    # Loop invariant: parts contains formatted info for suggestions[0..i]
    for suggestion in finding.suggestions:
        parts.append(f"\nSuggested test ({suggestion.description}):")
        parts.append(suggestion.suggested_test_code)
        if suggestion.required_mocks:
            mock_parts: list[str] = []
            # Loop invariant: mock_parts contains assessments for mocks[0..j]
            for mock in suggestion.required_mocks:
                necessity = (
                    "REQUIRED — external I/O"
                    if mock.mock_necessary
                    else "OPTIONAL — internal code, consider using real implementation"
                )
                mock_parts.append(f"  - mock {mock.name} ({mock.import_module}): {necessity} ({mock.reason})")
            parts.append("Mock assessment:\n" + "\n".join(mock_parts))
        else:
            parts.append("No mocks needed — test can use real implementations.")

    return "\n".join(parts)


@icontract.require(
    lambda lines: isinstance(lines, tuple)
    and all(isinstance(l, int) and l >= 1 for l in lines),
    "lines must be a tuple of positive integers",
)
@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty string",
)
@icontract.ensure(
    lambda lines, result: result == "none" if not lines else len(result) > 0,
    "empty input produces 'none', non-empty input produces non-empty output",
)
def _format_line_ranges(lines: tuple[int, ...]) -> str:
    """Format a tuple of line numbers into compact ranges.

    Args:
        lines: Tuple of positive line numbers (need not be sorted).

    Returns:
        A string like "5-8, 12, 15-20".
    """
    if not lines:
        return "none"
    sorted_lines = sorted(lines)
    ranges: list[str] = []
    start = sorted_lines[0]
    end = start

    # Loop invariant: [start..end] is the current contiguous range,
    # ranges contains all completed ranges before sorted_lines[i]
    for line in sorted_lines[1:]:
        if line == end + 1:
            end = line
        else:
            ranges.append(f"{start}-{end}" if start != end else str(start))
            start = line
            end = line
    ranges.append(f"{start}-{end}" if start != end else str(start))

    return ", ".join(ranges)
