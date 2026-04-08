"""Port definition for dead-code analysis.

This module defines the Protocol interface for static dead-code
backends. Implementations may use tools such as Vulture to identify
likely unused code in shipped project source.

This is a ports module — no implementations, only abstract contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import icontract

from serenecode.contracts.predicates import is_non_empty_string


@icontract.invariant(
    lambda self: is_non_empty_string(self.symbol_name),
    "symbol name must be non-empty",
)
@icontract.invariant(
    lambda self: is_non_empty_string(self.file_path),
    "file path must be non-empty",
)
@icontract.invariant(
    lambda self: is_non_empty_string(self.symbol_type),
    "symbol type must be non-empty",
)
@icontract.invariant(
    lambda self: is_non_empty_string(self.message),
    "message must be non-empty",
)
@icontract.invariant(
    lambda self: self.line >= 1,
    "line number must be at least 1",
)
@icontract.invariant(
    lambda self: 0 <= self.confidence <= 100,
    "confidence must be between 0 and 100",
)
@dataclass(frozen=True)
class DeadCodeFinding:
    """A likely dead-code finding returned by a static analyzer."""

    symbol_name: str
    file_path: str
    line: int
    symbol_type: str
    confidence: int
    message: str


# Protocol classes are exempt from @icontract.invariant — see ports/file_system.py.
class DeadCodeAnalyzer(Protocol):
    """Port for static dead-code analysis."""

    @icontract.require(
        lambda paths: isinstance(paths, tuple) and all(is_non_empty_string(path) for path in paths),
        "paths must be a tuple of non-empty path strings",
    )
    @icontract.ensure(
        lambda result: isinstance(result, list),
        "result must be a list",
    )
    def analyze_paths(
        self,
        paths: tuple[str, ...],
        min_confidence: int = 60,
    ) -> list[DeadCodeFinding]:
        """Analyze source paths for likely dead code.

        Args:
            paths: Source file or directory paths to analyze.
            min_confidence: Minimum confidence threshold from 0 to 100.

        Returns:
            List of dead-code findings.
        """
        ...
