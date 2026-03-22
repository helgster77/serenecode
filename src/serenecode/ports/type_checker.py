"""Port definition for type checking (Level 2).

This module defines the Protocol interface for static type analysis
backends such as mypy. The checker module depends on this protocol
rather than concrete implementations.

This is a ports module — no implementations, only abstract contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import icontract

from serenecode.contracts.predicates import is_non_empty_string



@icontract.invariant(
    lambda self: is_non_empty_string(self.file) and self.line >= 0 and self.column >= 0,
    "type issues must reference a file and non-negative position",
)
@dataclass(frozen=True)
class TypeIssue:
    """A single type checking issue reported by a backend like mypy.

    Represents one error, warning, or note from the type checker.
    """

    file: str
    line: int
    column: int
    severity: str  # "error", "warning", "note"
    message: str
    code: str | None = None  # e.g. "arg-type", "return-value"


@icontract.invariant(lambda self: True, "protocol has no runtime state")
class TypeChecker(Protocol):
    """Port for static type checking.

    Implementations run a type checker (e.g. mypy) on source files
    and return structured issue reports.
    """

    @icontract.require(lambda file_paths: isinstance(file_paths, list), "file_paths must be a list")
    @icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
    def check(
        self,
        file_paths: list[str],
        strict: bool = True,
        search_paths: tuple[str, ...] = (),
    ) -> list[TypeIssue]:
        """Run type checking on the given files.

        Args:
            file_paths: Paths to Python files to check.
            strict: Whether to use strict mode.
            search_paths: Import roots needed to resolve local modules.

        Returns:
            List of type issues found.
        """
        ...
