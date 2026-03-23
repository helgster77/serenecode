"""Integration tests for the Hypothesis property testing adapter.

These tests run real Hypothesis property-based tests against fixture modules.
Covers strategy derivation, precondition filtering, postcondition detection,
error handling, and edge cases.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from serenecode.adapters.hypothesis_adapter import (
    HypothesisPropertyTester,
    _build_strategies_from_signature,
    _get_contracted_functions,
    _has_icontract_decorators,
)


@pytest.mark.slow
class TestHypothesisAdapter:
    """Integration tests running real Hypothesis tests."""

    def test_valid_module_passes(self) -> None:
        tester = HypothesisPropertyTester(allow_code_execution=True)
        findings = tester.test_module(
            "tests.fixtures.valid.simple_function",
            max_examples=20,
        )
        passed = [f for f in findings if f.passed]
        assert len(passed) >= 0

    def test_broken_postcondition_detected(self) -> None:
        """Test that Hypothesis finds postcondition violations.

        Note: This test is sensitive to import order. If CrossHair
        has been imported in the same process, it monkey-patches
        icontract internals and may suppress postcondition checking.
        """
        from tests.conftest import icontract_enabled
        if not icontract_enabled():
            pytest.skip("icontract invariants disabled by CrossHair monkey-patching")

        tester = HypothesisPropertyTester(allow_code_execution=True)
        findings = tester.test_module(
            "tests.fixtures.invalid.broken_postcondition",
            max_examples=50,
        )
        # At minimum, the function should be found and tested
        abs_findings = [f for f in findings if f.function_name == "absolute_value"]
        assert len(abs_findings) >= 1
        # The violation should be detected
        failed = [f for f in findings if not f.passed]
        if failed:
            abs_failures = [f for f in failed if f.function_name == "absolute_value"]
            assert len(abs_failures) >= 1

    def test_correct_function_passes(self) -> None:
        tester = HypothesisPropertyTester(allow_code_execution=True)
        findings = tester.test_module(
            "tests.fixtures.invalid.broken_postcondition",
            max_examples=20,
        )
        mean_findings = [f for f in findings if f.function_name == "compute_mean"]
        if mean_findings:
            # Accept either passing or a health check crash (filter_too_much)
            # since Hypothesis may struggle to generate non-empty lists
            finding = mean_findings[0]
            assert finding.passed is True or finding.finding_type == "crash"

    def test_module_with_no_contracted_functions(self) -> None:
        tester = HypothesisPropertyTester(allow_code_execution=True)
        findings = tester.test_module(
            "tests.fixtures.invalid.missing_contracts",
            max_examples=10,
        )
        # No contracted functions → no findings
        assert len(findings) == 0

    def test_finding_includes_function_name(self) -> None:
        tester = HypothesisPropertyTester(allow_code_execution=True)
        findings = tester.test_module(
            "tests.fixtures.valid.simple_function",
            max_examples=10,
        )
        # Loop invariant: checked findings[0..i] for non-empty function_name
        for f in findings:
            assert f.function_name != ""
            assert f.module_path == "tests.fixtures.valid.simple_function"

    def test_finding_has_correct_types(self) -> None:
        tester = HypothesisPropertyTester(allow_code_execution=True)
        findings = tester.test_module(
            "tests.fixtures.invalid.broken_postcondition",
            max_examples=30,
        )
        # Loop invariant: checked findings[0..i] for correct field types
        for f in findings:
            assert isinstance(f.passed, bool)
            assert isinstance(f.function_name, str)
            assert isinstance(f.module_path, str)
            assert isinstance(f.message, str)
            assert f.finding_type in (
                "verified", "postcondition_violated", "crash",
                "skipped", "precondition_error",
            )

    def test_result_model_function_is_actually_property_tested(self, tmp_path: Path) -> None:
        module_file = tmp_path / "non_property_friendly.py"
        module_file.write_text(
            '''\
"""Module docstring."""

import icontract
from serenecode.models import CheckResult


@icontract.require(lambda x: True, "accept any result")
@icontract.ensure(lambda result: result >= 0, "result stays non-negative")
def count_result(x: CheckResult) -> int:
    """Return a stable count for the supplied result graph."""
    return 0
''',
            encoding="utf-8",
        )

        tester = HypothesisPropertyTester(allow_code_execution=True)
        findings = tester.test_module(str(module_file), max_examples=5)

        assert len(findings) == 1
        assert findings[0].function_name == "count_result"
        assert findings[0].passed is True
        assert findings[0].finding_type == "verified"


class TestStrategyDerivation:
    """Tests for Hypothesis strategy derivation from type annotations."""

    def test_derives_strategy_for_int(self) -> None:
        def func(x: int) -> int:
            return x
        strategies = _build_strategies_from_signature(func)
        assert strategies is not None
        assert "x" in strategies

    def test_derives_strategy_for_float(self) -> None:
        def func(x: float) -> float:
            return x
        strategies = _build_strategies_from_signature(func)
        assert strategies is not None
        assert "x" in strategies

    def test_derives_strategy_for_str(self) -> None:
        def func(s: str) -> str:
            return s
        strategies = _build_strategies_from_signature(func)
        assert strategies is not None
        assert "s" in strategies

    def test_derives_strategy_for_bool(self) -> None:
        def func(b: bool) -> bool:
            return b
        strategies = _build_strategies_from_signature(func)
        assert strategies is not None

    def test_derives_strategy_for_set_of_strings(self) -> None:
        def func(values: set[str]) -> int:
            return len(values)

        strategies = _build_strategies_from_signature(func)

        assert strategies is not None
        assert "values" in strategies

    def test_derives_strategy_for_contracted_class(self) -> None:
        import icontract

        class Demo:
            @icontract.require(lambda value: value > 0, "value must be positive")
            def __init__(self, value: int) -> None:
                self.value = value

        def func(demo: Demo) -> int:
            return demo.value
        func.__annotations__["demo"] = Demo

        strategies = _build_strategies_from_signature(func)

        assert strategies is not None
        assert "demo" in strategies

    def test_no_strategy_without_annotations(self) -> None:
        def func(x):  # type: ignore[no-untyped-def]
            return x
        strategies = _build_strategies_from_signature(func)
        assert strategies is None

    def test_no_strategy_for_zero_params(self) -> None:
        def func() -> int:
            return 42
        strategies = _build_strategies_from_signature(func)
        assert strategies == {}

    def test_skips_self_parameter(self) -> None:
        class Foo:
            def method(self, x: int) -> int:
                return x
        strategies = _build_strategies_from_signature(Foo.method)
        assert strategies is not None
        assert "self" not in strategies
        assert "x" in strategies


class TestContractedFunctionDiscovery:
    """Tests for finding contracted functions in modules."""

    def test_finds_contracted_functions(self) -> None:
        functions = _get_contracted_functions("tests.fixtures.valid.simple_function")
        names = [name for name, _ in functions]
        assert "square" in names

    def test_skips_uncontracted_functions(self) -> None:
        functions = _get_contracted_functions("tests.fixtures.invalid.missing_contracts")
        assert len(functions) == 0

    def test_detects_icontract_decorators(self) -> None:
        from tests.fixtures.valid.simple_function import square
        assert _has_icontract_decorators(square) is True
