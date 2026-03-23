"""Integration tests for the CrossHair symbolic verification adapter.

These tests run real CrossHair verification against fixture modules.
Marked as slow since symbolic execution can take significant time.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker


@pytest.mark.slow
class TestCrossHairAdapter:
    """Integration tests running real CrossHair verification."""

    def test_broken_postcondition_found(self) -> None:
        checker = CrossHairSymbolicChecker(
            per_condition_timeout=10,
            per_path_timeout=5,
            allow_code_execution=True,
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
            allow_code_execution=True,
        )
        findings = checker.verify_module(
            "tests.fixtures.valid.simple_function",
        )
        # square function should be verified or at least not fail
        verified = [f for f in findings if f.outcome == "verified"]
        failed = [f for f in findings if f.outcome == "counterexample"]
        # Simple function should verify successfully
        assert len(failed) == 0

    def test_standalone_file_with_non_importable_name_is_verified(self, tmp_path: Path) -> None:
        module_file = tmp_path / "bad-name.py"
        module_file.write_text(
            '''"""Standalone verification module."""
import icontract


@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result: result >= 0, "result must be non-negative")
def identity(x: int) -> int:
    """Return the input unchanged."""
    return x
''',
            encoding="utf-8",
        )

        checker = CrossHairSymbolicChecker(
            per_condition_timeout=10,
            per_path_timeout=5,
            allow_code_execution=True,
        )
        findings = checker.verify_module(
            str(module_file),
            search_paths=(str(tmp_path),),
        )

        verified = [
            finding
            for finding in findings
            if finding.outcome == "verified" and finding.function_name == "identity"
        ]
        failed = [
            finding
            for finding in findings
            if finding.outcome in {"counterexample", "error"}
        ]

        assert verified
        assert len(failed) == 0
