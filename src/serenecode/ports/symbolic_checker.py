"""Port definition for symbolic verification (Level 4).

This module defines the Protocol interface for symbolic execution
backends such as CrossHair. The checker module depends on this
protocol rather than concrete implementations.

This is a ports module — no implementations, only abstract contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import icontract

from serenecode.contracts.predicates import is_non_empty_string



@icontract.invariant(
    lambda self: is_non_empty_string(self.function_name) and is_non_empty_string(self.module_path),
    "finding names must be non-empty",
)
@dataclass(frozen=True)
class SymbolicFinding:
    """A single finding from symbolic verification.

    Represents one of three outcomes: verified (proven correct),
    counterexample (violation found), or unknown (timeout/unsupported).
    """

    function_name: str
    module_path: str
    outcome: str  # "verified", "counterexample", "timeout", "unsupported", "error"
    message: str
    counterexample: dict[str, object] | None = None
    condition: str | None = None  # which postcondition was violated
    duration_seconds: float = 0.0


@icontract.invariant(lambda self: True, "protocol has no runtime state")
class SymbolicChecker(Protocol):
    """Port for symbolic verification.

    Implementations use CrossHair/Z3 (or similar) to symbolically
    execute functions and prove contracts hold for all valid inputs.
    """

    @icontract.require(lambda module_path: is_non_empty_string(module_path), "module_path must be a non-empty string")
    @icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
    def verify_module(
        self,
        module_path: str,
        per_condition_timeout: int | None = None,
        per_path_timeout: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[SymbolicFinding]:
        """Run symbolic verification on all contracted functions in a module.

        Args:
            module_path: Importable Python module path to verify.
            per_condition_timeout: Max seconds per postcondition.
            per_path_timeout: Max seconds per execution path.
            search_paths: sys.path roots needed to import the module.

        Returns:
            List of symbolic findings.
        """
        ...
