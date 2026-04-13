"""Port definition for coverage analysis (Level 3).

This module defines the Protocol interface for coverage analysis
backends using coverage.py. The checker module depends on this
protocol rather than concrete implementations.

This is a ports module — no implementations, only abstract contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import icontract

from serenecode.contracts.predicates import is_non_empty_string


@icontract.invariant(
    lambda self: is_non_empty_string(self.name),
    "dependency name must be non-empty",
)
@dataclass(frozen=True)
class MockDependency:
    """A dependency that must be mocked for a test to reach uncovered code.

    Represents a call target found via AST analysis in an uncovered path.
    """

    name: str
    import_module: str
    is_external: bool
    mock_necessary: bool
    reason: str


@icontract.invariant(
    lambda self: is_non_empty_string(self.description),
    "test suggestion description must be non-empty",
)
@dataclass(frozen=True)
class CoverageSuggestion:
    """A suggested test to cover an uncovered code path.

    Includes the mock setup code needed and whether each mock
    could be replaced with a real implementation.
    """

    description: str
    target_lines: tuple[int, ...]
    suggested_test_code: str
    required_mocks: tuple[MockDependency, ...]
    all_mocks_necessary: bool  # allow-unused: dataclass field used by consumers


@icontract.invariant(
    lambda self: is_non_empty_string(self.function_name)
    and is_non_empty_string(self.module_path),
    "finding names must be non-empty",
)
@icontract.invariant(
    lambda self: 0.0 <= self.line_coverage_percent <= 100.0,
    "line coverage must be a valid percentage",
)
@icontract.invariant(
    lambda self: 0.0 <= self.branch_coverage_percent <= 100.0,
    "branch coverage must be a valid percentage",
)
@dataclass(frozen=True)
class CoverageFinding:
    """A coverage analysis finding for a single function.

    Contains coverage metrics, uncovered locations, test suggestions
    with mock assessments, and the threshold pass/fail determination.
    """

    function_name: str
    module_path: str
    line_start: int
    line_end: int
    line_coverage_percent: float
    branch_coverage_percent: float
    uncovered_lines: tuple[int, ...]
    uncovered_branches: tuple[tuple[int, int], ...]  # allow-unused: dataclass field used by consumers
    suggestions: tuple[CoverageSuggestion, ...]
    meets_threshold: bool
    message: str


# Protocol classes are exempt from @icontract.invariant — see ports/file_system.py.
class CoverageAnalyzer(Protocol):
    """Port for coverage analysis.

    Implementations use coverage.py and AST analysis to measure
    test coverage per function and generate test suggestions for
    uncovered paths.
    """

    @icontract.require(
        lambda module_path: is_non_empty_string(module_path),
        "module_path must be a non-empty string",
    )
    @icontract.ensure(
        lambda result: isinstance(result, list),
        "result must be a list",
    )
    def analyze_module(
        self,
        module_path: str,
        search_paths: tuple[str, ...] = (),
        coverage_threshold: float = 80.0,
    ) -> list[CoverageFinding]:
        """Run coverage analysis on all functions in a module.

        Args:
            module_path: Importable Python module path to analyze.
            search_paths: sys.path roots needed to import the module.
            coverage_threshold: Minimum coverage percentage to pass.

        Returns:
            List of coverage findings per function.
        """
        ...
