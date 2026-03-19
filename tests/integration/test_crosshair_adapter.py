"""Integration tests for the CrossHair symbolic verification adapter.

These tests run real CrossHair verification against fixture modules.
Marked as slow since symbolic execution can take significant time.
"""

from __future__ import annotations

import pytest

from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker


@pytest.mark.slow
class TestCrossHairAdapter:
    """Integration tests running real CrossHair verification."""

    def test_broken_postcondition_found(self) -> None:
        checker = CrossHairSymbolicChecker(
            per_condition_timeout=10,
            per_path_timeout=5,
        )
        findings = checker.verify_module(
            "tests.fixtures.invalid.broken_postcondition",
        )
        # Should find the absolute_value bug
        counterexamples = [
            f for f in findings if f.outcome == "counterexample"
        ]
        assert len(counterexamples) >= 1

    def test_valid_module_verified(self) -> None:
        checker = CrossHairSymbolicChecker(
            per_condition_timeout=10,
            per_path_timeout=5,
        )
        findings = checker.verify_module(
            "tests.fixtures.valid.simple_function",
        )
        # square function should be verified or at least not fail
        verified = [f for f in findings if f.outcome == "verified"]
        failed = [f for f in findings if f.outcome == "counterexample"]
        # Simple function should verify successfully
        assert len(failed) == 0
