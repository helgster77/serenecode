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


@icontract.invariant(lambda self: True, "frozen type issue data carrier")
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


class TypeChecker(Protocol):
    """Port for static type checking.

    Implementations run a type checker (e.g. mypy) on source files
    and return structured issue reports.
    """

    def check(
        self,
        file_paths: list[str],
        strict: bool = True,
    ) -> list[TypeIssue]:
        """Run type checking on the given files.

        Args:
            file_paths: Paths to Python files to check.
            strict: Whether to use strict mode.

        Returns:
            List of type issues found.
        """
        ...
