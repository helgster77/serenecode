"""Dead-code adapter used when the real backend is unavailable.

This adapter lets composition roots surface dead-code analysis as a
visible skipped finding without forcing low-level callers of the core
pipeline to treat a missing backend as an error by default.
"""

from __future__ import annotations

from dataclasses import dataclass

import icontract

from serenecode.contracts.predicates import is_non_empty_string
from serenecode.ports.dead_code_analyzer import DeadCodeAnalyzer, DeadCodeFinding


@icontract.invariant(
    lambda self: is_non_empty_string(self.reason),
    "reason must be a non-empty string",
)
@dataclass(frozen=True)
class UnavailableDeadCodeAnalyzer(DeadCodeAnalyzer):
    """Adapter that reports a backend-unavailable condition on use."""

    reason: str

    @icontract.require(
        lambda paths: isinstance(paths, tuple) and len(paths) > 0,
        "paths must be a non-empty tuple",
    )
    @icontract.require(
        lambda min_confidence: isinstance(min_confidence, int) and 0 <= min_confidence <= 100,
        "min_confidence must be between 0 and 100",
    )
    def analyze_paths(
        self,
        paths: tuple[str, ...],
        min_confidence: int = 60,
    ) -> list[DeadCodeFinding]:
        """Raise the stored unavailability reason when analysis is requested."""
        del paths
        del min_confidence
        raise RuntimeError(self.reason)
