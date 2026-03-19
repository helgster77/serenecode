"""Port definition for property-based testing (Level 3).

This module defines the Protocol interface for property-based testing
backends such as Hypothesis. The checker module depends on this
protocol rather than concrete implementations.

This is a ports module — no implementations, only abstract contracts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import icontract


@icontract.invariant(lambda self: True, "frozen property finding data carrier")
@dataclass(frozen=True)
class PropertyFinding:
    """A single finding from property-based testing.

    Represents either a successful verification or a counterexample
    that violates a postcondition.
    """

    function_name: str
    module_path: str
    passed: bool
    finding_type: str  # "postcondition_violated", "precondition_error", "crash", "verified"
    message: str
    counterexample: dict[str, object] | None = None
    exception_type: str | None = None
    exception_message: str | None = None


class PropertyTester(Protocol):
    """Port for property-based testing.

    Implementations use Hypothesis (or similar) to generate test inputs
    from function contracts and verify postconditions hold.
    """

    def test_module(
        self,
        module_path: str,
        max_examples: int = 100,
    ) -> list[PropertyFinding]:
        """Run property-based tests on all contracted functions in a module.

        Args:
            module_path: Importable Python module path to test.
            max_examples: Maximum number of test examples per function.

        Returns:
            List of property findings.
        """
        ...
