"""Data models for Serenecode verification results.

This module defines the core data structures used throughout Serenecode
to represent verification results. All models are frozen dataclasses
with icontract invariants to enforce correctness.

This is a core module — no I/O imports are permitted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum, IntEnum

import icontract

from serenecode.contracts.predicates import is_non_empty_string, is_non_negative_int


@icontract.ensure(lambda result: len(result) > 0, "version string must be non-empty")
def _get_version() -> str:
    """Read the package version from metadata, falling back to a default."""
    try:
        from importlib.metadata import version
        return version("serenecode")
    except Exception:
        return "0.0.0"


_VERSION = _get_version()


class VerificationLevel(Enum):
    """Verification levels in the Serenecode pipeline."""

    STRUCTURAL = 1
    TYPES = 2
    COVERAGE = 3
    PROPERTIES = 4
    SYMBOLIC = 5
    COMPOSITIONAL = 6


class CheckStatus(Enum):
    """Status of a verification check."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    EXEMPT = "exempt"


class ExitCode(IntEnum):
    """Exit codes for the Serenecode CLI."""

    PASSED = 0
    STRUCTURAL = 1
    TYPES = 2
    COVERAGE = 3
    PROPERTIES = 4
    SYMBOLIC = 5
    COMPOSITIONAL = 6
    INTERNAL = 10


@icontract.invariant(
    lambda self: is_non_empty_string(self.message),
    "message must be a non-empty string",
)
@dataclass(frozen=True)
class Detail:
    """A single verification finding.

    Represents one specific issue or confirmation found during
    verification at a particular level.
    """

    level: VerificationLevel
    tool: str
    finding_type: str
    message: str
    counterexample: dict[str, object] | None = None
    suggestion: str | None = None

    @icontract.ensure(
        lambda self, result: result["level"] == self.level.value and result["message"] == self.message,
        "serialized dict must preserve level and message",
    )
    def to_dict(self) -> dict[str, object]:
        """Convert to a plain dictionary for serialization."""
        result: dict[str, object] = {
            "level": self.level.value,
            "tool": self.tool,
            "type": self.finding_type,
            "message": self.message,
        }
        if self.counterexample is not None:
            result["counterexample"] = self.counterexample
        if self.suggestion is not None:
            result["suggestion"] = self.suggestion
        return result


@icontract.invariant(
    lambda self: self.line >= 1,
    "line number must be at least 1",
)
@icontract.invariant(
    lambda self: is_non_empty_string(self.function),
    "function name must be non-empty",
)
@icontract.invariant(
    lambda self: is_non_empty_string(self.file),
    "file path must be non-empty",
)
@dataclass(frozen=True)
class FunctionResult:
    """Verification result for a single function.

    Aggregates all findings across verification levels for one function.
    """

    function: str
    file: str
    line: int
    level_requested: int
    level_achieved: int
    status: CheckStatus
    details: tuple[Detail, ...] = ()

    @icontract.ensure(
        lambda self, result: result["function"] == self.function and result["status"] == self.status.value,
        "serialized dict must preserve function name and status",
    )
    def to_dict(self) -> dict[str, object]:
        """Convert to a plain dictionary matching the JSON output spec."""
        return {
            "function": self.function,
            "file": self.file,
            "line": self.line,
            "level_requested": self.level_requested,
            "level_achieved": self.level_achieved,
            "status": self.status.value,
            "details": [d.to_dict() for d in self.details],
        }


@icontract.invariant(
    lambda self: is_non_negative_int(self.total_functions),
    "total_functions must be non-negative",
)
@icontract.invariant(
    lambda self: is_non_negative_int(self.passed_count),
    "passed_count must be non-negative",
)
@icontract.invariant(
    lambda self: is_non_negative_int(self.failed_count),
    "failed_count must be non-negative",
)
@icontract.invariant(
    lambda self: is_non_negative_int(self.skipped_count),
    "skipped_count must be non-negative",
)
@icontract.invariant(
    lambda self: is_non_negative_int(self.exempt_count),
    "exempt_count must be non-negative",
)
@icontract.invariant(
    lambda self: self.total_functions == self.passed_count + self.failed_count + self.skipped_count + self.exempt_count,
    "counts must sum to total",
)
@icontract.invariant(
    lambda self: self.duration_seconds >= 0.0,
    "duration must be non-negative",
)
@dataclass(frozen=True)
class CheckSummary:
    """Summary statistics for a verification run."""

    total_functions: int
    passed_count: int
    failed_count: int
    skipped_count: int
    exempt_count: int = 0
    duration_seconds: float = 0.0

    @icontract.ensure(
        lambda self, result: result["total_functions"] == self.total_functions and result["passed"] == self.passed_count,
        "serialized dict must preserve counts",
    )
    def to_dict(self) -> dict[str, object]:
        """Convert to the summary dict matching the JSON output spec."""
        return {
            "total_functions": self.total_functions,
            "passed": self.passed_count,
            "failed": self.failed_count,
            "skipped": self.skipped_count,
            "exempt": self.exempt_count,
        }


@icontract.invariant(
    lambda self: 1 <= self.level_requested <= 6,
    "level_requested must be between 1 and 6",
)
@icontract.invariant(
    lambda self: 0 <= self.level_achieved <= 6,
    "level_achieved must be between 0 and 6",
)
@icontract.invariant(
    lambda self: self.level_achieved <= self.level_requested,
    "level_achieved must not exceed level_requested",
)
@dataclass(frozen=True)
class CheckResult:
    """Complete result of a verification run.

    Contains the overall pass/fail status, all per-function results,
    and summary statistics.
    """

    passed: bool
    level_requested: int
    level_achieved: int
    results: tuple[FunctionResult, ...]
    summary: CheckSummary
    version: str = _VERSION

    @property
    def failures(self) -> list[FunctionResult]:
        """Return only the failed function results."""
        # Loop invariant: accumulated list contains only FAILED results seen so far
        return [r for r in self.results if r.status == CheckStatus.FAILED]

    @icontract.ensure(
        lambda self, result: result["passed"] == self.passed and result["level_requested"] == self.level_requested,
        "serialized dict must preserve pass status and level",
    )
    def to_dict(self) -> dict[str, object]:
        """Convert to a plain dictionary matching the JSON output spec."""
        return {
            "version": self.version,
            "passed": self.passed,
            "level_requested": self.level_requested,
            "level_achieved": self.level_achieved,
            "summary": self.summary.to_dict(),
            "results": [r.to_dict() for r in self.results],
        }

    @icontract.ensure(
        lambda self, result: len(result) > 0 and '"passed"' in result,
        "JSON output must be non-empty and contain required fields",
    )
    def to_json(self) -> str:
        """Convert to a JSON string matching the spec output format."""
        return json.dumps(self.to_dict(), indent=2)


@icontract.require(
    lambda results: isinstance(results, tuple),
    "results must be a tuple",
)
@icontract.ensure(
    lambda results, level_requested, result: result.level_requested == level_requested,
    "result must report the correct requested level",
)
@icontract.ensure(
    lambda result: result.level_achieved <= result.level_requested,
    "achieved level must not exceed requested level",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
def make_check_result(
    results: tuple[FunctionResult, ...],
    level_requested: int,
    duration_seconds: float,
    level_achieved: int | None = None,
) -> CheckResult:
    """Create a CheckResult from a tuple of FunctionResults.

    Automatically computes passed/failed/skipped counts and overall status.

    Args:
        results: Tuple of per-function results.
        level_requested: The verification level that was requested.
        duration_seconds: How long the check took.
        level_achieved: Optional aggregate level achieved override.

    Returns:
        A fully constructed CheckResult.
    """
    passed_count = 0
    failed_count = 0
    skipped_count = 0
    exempt_count = 0
    has_non_exempt = False
    min_achieved = level_requested

    # Loop invariant: counts reflect classifications of results[0..i];
    # has_non_exempt is True iff any non-EXEMPT result was seen so far.
    for r in results:
        if r.status == CheckStatus.EXEMPT:
            exempt_count += 1
            continue
        has_non_exempt = True
        if r.level_achieved < min_achieved:
            min_achieved = r.level_achieved
        if r.status == CheckStatus.PASSED:
            passed_count += 1
        elif r.status == CheckStatus.FAILED:
            failed_count += 1
        else:
            skipped_count += 1

    # If results exist but every one is EXEMPT, nothing was actually
    # verified — do not claim the requested level was achieved.
    # (Truly empty results are left at level_requested; the pipeline's
    # _level_achieved() with require_evidence=True handles L3-L5 empty
    # results separately.)
    if exempt_count > 0 and not has_non_exempt:
        min_achieved = 0

    overall_level_achieved = (
        min_achieved if level_achieved is None else level_achieved
    )

    summary = CheckSummary(
        total_functions=len(results),
        passed_count=passed_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        exempt_count=exempt_count,
        duration_seconds=duration_seconds,
    )

    # Exempt results are visible but do not block a passing result.
    passed = (
        failed_count == 0
        and skipped_count == 0
        and overall_level_achieved == level_requested
    )

    return CheckResult(
        passed=passed,
        level_requested=level_requested,
        level_achieved=overall_level_achieved,
        results=results,
        summary=summary,
    )
