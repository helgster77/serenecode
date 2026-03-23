"""Integration tests for checker modules with real code samples.

These tests run the actual verification engines (not just result
transformation) against fixture code to verify end-to-end behavior.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from serenecode.checker.structural import check_structural
from serenecode.checker.types import transform_type_results
from serenecode.config import default_config
from serenecode.models import CheckStatus


class TestStructuralCheckerWithFixtures:
    """Integration tests using all fixture files."""

    def test_valid_simple_function(self) -> None:
        source = Path("tests/fixtures/valid/simple_function.py").read_text()
        result = check_structural(source, default_config(), file_path="simple_function.py")
        assert result.passed is True

    def test_valid_class_with_invariant(self) -> None:
        source = Path("tests/fixtures/valid/class_with_invariant.py").read_text()
        result = check_structural(source, default_config(), file_path="class.py")
        assert result.passed is True

    def test_valid_full_module(self) -> None:
        source = Path("tests/fixtures/valid/full_module.py").read_text()
        result = check_structural(source, default_config(), file_path="full.py")
        assert result.passed is True

    def test_invalid_missing_contracts(self) -> None:
        source = Path("tests/fixtures/invalid/missing_contracts.py").read_text()
        result = check_structural(source, default_config(), file_path="mc.py")
        assert result.passed is False
        failures = [r for r in result.results if r.status == CheckStatus.FAILED]
        assert len(failures) >= 2  # add and multiply

    def test_invalid_missing_types(self) -> None:
        source = Path("tests/fixtures/invalid/missing_types.py").read_text()
        result = check_structural(source, default_config(), file_path="mt.py")
        assert result.passed is False
        annotation_failures = [
            r for r in result.results
            if any("annotation" in d.message.lower() or "return type" in d.message.lower()
                   for d in r.details)
        ]
        assert len(annotation_failures) >= 1

    def test_invalid_io_in_core(self) -> None:
        source = Path("tests/fixtures/invalid/io_in_core.py").read_text()
        result = check_structural(
            source, default_config(),
            module_path="core/io_test.py", file_path="io.py",
        )
        assert result.passed is False
        import_failures = [
            r for r in result.results
            if any("forbidden import" in d.message.lower() for d in r.details)
        ]
        assert len(import_failures) >= 1

    def test_invalid_missing_invariant(self) -> None:
        source = Path("tests/fixtures/invalid/missing_invariant.py").read_text()
        result = check_structural(source, default_config(), file_path="mi.py")
        assert result.passed is False
        invariant_failures = [
            r for r in result.results
            if any("invariant" in d.message.lower() for d in r.details)
        ]
        assert len(invariant_failures) >= 1

    def test_edge_case_from_import(self) -> None:
        source = Path("tests/fixtures/edge_cases/from_import.py").read_text()
        result = check_structural(source, default_config(), file_path="fi.py")
        assert result.passed is True

    def test_edge_case_aliased_import(self) -> None:
        source = Path("tests/fixtures/edge_cases/aliased_import.py").read_text()
        result = check_structural(source, default_config(), file_path="ai.py")
        assert result.passed is True

    def test_edge_case_async_function(self) -> None:
        source = Path("tests/fixtures/edge_cases/async_functions.py").read_text()
        result = check_structural(source, default_config(), file_path="async.py")
        assert result.passed is True

    def test_edge_case_empty_module(self) -> None:
        source = Path("tests/fixtures/edge_cases/empty_module.py").read_text()
        result = check_structural(source, default_config(), file_path="empty.py")
        assert result.passed is True


@pytest.mark.slow
class TestTypesCheckerWithRealMypy:
    """Integration tests running real mypy on sample code."""

    def test_valid_code_passes_mypy(self, tmp_path: Path) -> None:
        source = textwrap.dedent('''\
            def add(x: int, y: int) -> int:
                return x + y
        ''')
        test_file = tmp_path / "valid.py"
        test_file.write_text(source, encoding="utf-8")

        from serenecode.adapters.mypy_adapter import MypyTypeChecker
        checker = MypyTypeChecker(timeout=30)
        issues = checker.check([str(test_file)], strict=False)
        result = transform_type_results(issues, 0.5)
        assert result.passed is True

    def test_type_error_caught_by_mypy(self, tmp_path: Path) -> None:
        source = textwrap.dedent('''\
            def add(x: int, y: int) -> int:
                return "not an int"
        ''')
        test_file = tmp_path / "bad.py"
        test_file.write_text(source, encoding="utf-8")

        from serenecode.adapters.mypy_adapter import MypyTypeChecker
        checker = MypyTypeChecker(timeout=30)
        issues = checker.check([str(test_file)], strict=False)
        result = transform_type_results(issues, 0.3)
        assert result.passed is False
        assert result.summary.failed_count >= 1


@pytest.mark.slow
class TestPropertiesCheckerWithRealHypothesis:
    """Integration tests running real Hypothesis on sample code."""

    def test_broken_postcondition_found(self) -> None:
        from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
        from serenecode.checker.properties import transform_property_results
        from tests.conftest import icontract_enabled

        if not icontract_enabled():
            pytest.skip("icontract invariants disabled by CrossHair monkey-patching")

        tester = HypothesisPropertyTester(allow_code_execution=True)
        findings = tester.test_module(
            "tests.fixtures.invalid.broken_postcondition",
            max_examples=50,
        )
        result = transform_property_results(findings, "broken.py", 1.0)
        # absolute_value should fail
        assert result.summary.failed_count >= 1

    def test_valid_function_passes(self) -> None:
        from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
        from serenecode.checker.properties import transform_property_results

        tester = HypothesisPropertyTester(allow_code_execution=True)
        findings = tester.test_module(
            "tests.fixtures.valid.simple_function",
            max_examples=20,
        )
        result = transform_property_results(findings, "valid.py", 0.5)
        # simple_function.square should pass or be skipped
        assert result.summary.failed_count == 0


@pytest.mark.slow
class TestSymbolicCheckerWithRealCrossHair:
    """Integration tests running real CrossHair on sample code."""

    def test_broken_postcondition_counterexample(self) -> None:
        from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
        from serenecode.checker.symbolic import transform_symbolic_results

        checker = CrossHairSymbolicChecker(
            per_condition_timeout=10,
            per_path_timeout=5,
            allow_code_execution=True,
        )
        findings = checker.verify_module("tests.fixtures.invalid.broken_postcondition")
        result = transform_symbolic_results(findings, "broken.py", 10.0)
        counterexamples = [
            r for r in result.results
            if any(d.finding_type == "counterexample" for d in r.details)
        ]
        assert len(counterexamples) >= 1

    def test_valid_function_verified(self) -> None:
        from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
        from serenecode.checker.symbolic import transform_symbolic_results

        checker = CrossHairSymbolicChecker(
            per_condition_timeout=10,
            per_path_timeout=5,
            allow_code_execution=True,
        )
        findings = checker.verify_module("tests.fixtures.valid.simple_function")
        result = transform_symbolic_results(findings, "valid.py", 5.0)
        # No counterexamples for the valid function
        counterexamples = [
            r for r in result.results
            if any(d.finding_type == "counterexample" for d in r.details)
        ]
        assert len(counterexamples) == 0
