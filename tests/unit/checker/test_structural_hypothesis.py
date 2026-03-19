"""Property-based tests for the structural checker using Hypothesis.

Generates random Python source code patterns and verifies the structural
checker correctly identifies violations or passes. Uses @example decorators
for known regression cases.
"""

from __future__ import annotations

import textwrap

from hypothesis import example, given, settings
from hypothesis import strategies as st

from serenecode.checker.structural import check_structural
from serenecode.config import default_config, minimal_config, strict_config
from serenecode.models import CheckStatus


# Strategies for generating Python source fragments
_PYTHON_KEYWORDS = frozenset({
    "and", "as", "assert", "async", "await", "break", "class", "continue",
    "def", "del", "elif", "else", "except", "finally", "for", "from",
    "global", "if", "import", "in", "is", "lambda", "nonlocal", "not",
    "or", "pass", "raise", "return", "try", "while", "with", "yield",
})

func_names = st.from_regex(r"[a-z][a-z_]{2,15}", fullmatch=True).filter(lambda s: s not in _PYTHON_KEYWORDS and "__" not in s)
param_names = st.from_regex(r"[a-z][a-z_]{1,8}", fullmatch=True).filter(lambda s: s not in _PYTHON_KEYWORDS and "__" not in s)
type_annotations = st.sampled_from(["int", "str", "float", "bool", "list[int]", "dict[str, int]"])


@st.composite
def contracted_function_source(draw: st.DrawFn) -> str:
    """Generate source for a function with proper icontract contracts."""
    name = draw(func_names)
    param = draw(param_names)
    ret_type = draw(st.sampled_from(["int", "str", "float", "bool"]))
    return textwrap.dedent(f'''\
        """Module docstring."""

        import icontract

        @icontract.require(lambda {param}: isinstance({param}, {ret_type}), "{param} must be {ret_type}")
        @icontract.ensure(lambda result: result is not None, "result must not be None")
        def {name}({param}: {ret_type}) -> {ret_type}:
            """Function docstring."""
            return {param}
    ''')


@st.composite
def uncontracted_function_source(draw: st.DrawFn) -> str:
    """Generate source for a function WITHOUT contracts."""
    name = draw(func_names)
    param = draw(param_names)
    ret_type = draw(st.sampled_from(["int", "str", "float"]))
    return textwrap.dedent(f'''\
        """Module docstring."""

        def {name}({param}: {ret_type}) -> {ret_type}:
            """Function docstring."""
            return {param}
    ''')


@st.composite
def unannotated_function_source(draw: st.DrawFn) -> str:
    """Generate source for a function missing type annotations."""
    name = draw(func_names)
    param = draw(param_names)
    return textwrap.dedent(f'''\
        """Module docstring."""

        import icontract

        @icontract.require(lambda {param}: True, "always true")
        @icontract.ensure(lambda result: True, "always true")
        def {name}({param}):
            """Function docstring."""
            return {param}
    ''')


class TestContractedFunctionsPass:
    """Property: functions with contracts should pass structural check."""

    @given(source=contracted_function_source())
    @settings(max_examples=30)
    @example(source=textwrap.dedent('''\
        """Module docstring."""

        import icontract

        @icontract.require(lambda x: x > 0, "x must be positive")
        @icontract.ensure(lambda result: result > 0, "result positive")
        def double(x: int) -> int:
            """Double a number."""
            return x * 2
    '''))
    def test_contracted_functions_pass(self, source: str) -> None:
        result = check_structural(source, default_config(), file_path="test.py")
        contract_failures = [
            r for r in result.results
            if r.status == CheckStatus.FAILED
            and any("contract" in d.message.lower() or "require" in d.message.lower()
                    or "ensure" in d.message.lower() for d in r.details)
        ]
        assert len(contract_failures) == 0


class TestUncontractedFunctionsFail:
    """Property: functions without contracts should fail structural check."""

    @given(source=uncontracted_function_source())
    @settings(max_examples=30)
    @example(source=textwrap.dedent('''\
        """Module docstring."""

        def add(x: int, y: int) -> int:
            """Add two numbers."""
            return x + y
    '''))
    def test_uncontracted_functions_fail(self, source: str) -> None:
        result = check_structural(source, default_config(), file_path="test.py")
        assert result.passed is False


class TestUnannotatedFunctionsFail:
    """Property: functions missing type annotations should fail."""

    @given(source=unannotated_function_source())
    @settings(max_examples=30)
    @example(source=textwrap.dedent('''\
        """Module docstring."""

        import icontract

        @icontract.require(lambda x: True, "ok")
        @icontract.ensure(lambda result: True, "ok")
        def process(x):
            """Process data."""
            return x
    '''))
    def test_unannotated_functions_fail(self, source: str) -> None:
        result = check_structural(source, default_config(), file_path="test.py")
        annotation_failures = [
            r for r in result.results
            if any("annotation" in d.message.lower() or "return type" in d.message.lower()
                    for d in r.details)
        ]
        assert len(annotation_failures) > 0


class TestCheckerAlwaysReturnsCheckResult:
    """Property: checker should always return a CheckResult, never raise."""

    @given(source=st.text(max_size=500))
    @settings(max_examples=50)
    @example(source="")
    @example(source="def broken(:\n  pass")
    @example(source="import os\nimport sys\n")
    @example(source="# just a comment\n")
    @example(source='"""docstring only"""')
    def test_never_raises(self, source: str) -> None:
        result = check_structural(source, default_config(), file_path="test.py")
        assert result is not None
        assert hasattr(result, "passed")
        assert hasattr(result, "summary")
        assert result.summary.duration_seconds >= 0


class TestMinimalConfigRelaxesChecks:
    """Property: minimal config should accept code that default rejects."""

    @given(source=uncontracted_function_source())
    @settings(max_examples=20)
    def test_minimal_does_not_require_class_invariants(self, source: str) -> None:
        # minimal_config doesn't require class invariants
        class_source = textwrap.dedent('''\
            """Module docstring."""

            class Foo:
                """A class without invariant."""
                pass
        ''')
        result = check_structural(class_source, minimal_config(), file_path="test.py")
        invariant_failures = [
            r for r in result.results
            if any("invariant" in d.message.lower() for d in r.details)
        ]
        assert len(invariant_failures) == 0


class TestEdgeCaseFixtures:
    """Systematic tests using edge case fixture content."""

    @given(import_style=st.sampled_from([
        "import icontract",
        "import icontract as ic",
        "from icontract import require, ensure",
    ]))
    def test_all_import_styles_recognized(self, import_style: str) -> None:
        if "as ic" in import_style:
            decorator_prefix = "ic"
        elif "from" in import_style:
            decorator_prefix = ""
        else:
            decorator_prefix = "icontract"

        req = f"@{decorator_prefix}.require" if decorator_prefix else "@require"
        ens = f"@{decorator_prefix}.ensure" if decorator_prefix else "@ensure"

        source = textwrap.dedent(f'''\
            """Module docstring."""

            {import_style}

            {req}(lambda x: x >= 0, "x non-neg")
            {ens}(lambda result: result >= 0, "result non-neg")
            def square(x: float) -> float:
                """Square a number."""
                return x * x
        ''')
        result = check_structural(source, default_config(), file_path="test.py")
        contract_failures = [
            r for r in result.results
            if r.function == "square" and r.status == CheckStatus.FAILED
            and any("require" in d.message.lower() or "ensure" in d.message.lower()
                    for d in r.details)
        ]
        assert len(contract_failures) == 0, f"Contracts not recognized with: {import_style}"
