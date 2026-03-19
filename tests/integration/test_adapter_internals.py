"""Unit tests for adapter internal functions.

Tests parsing logic, strategy derivation, and helper functions
without running external tools (mypy, hypothesis, crosshair).
"""

from __future__ import annotations

import pytest

from serenecode.adapters.mypy_adapter import MypyTypeChecker, _MYPY_OUTPUT_PATTERN


class TestMypyOutputParsing:
    """Tests for mypy output line parsing."""

    def test_parses_error_with_code(self) -> None:
        line = "src/foo.py:42:5: error: Argument 1 has incompatible type [arg-type]"
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(1) == "src/foo.py"
        assert match.group(2) == "42"
        assert match.group(3) == "5"
        assert match.group(4) == "error"
        assert match.group(6) == "arg-type"

    def test_parses_error_without_code(self) -> None:
        line = "src/foo.py:10:1: error: Some error message"
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(6) is None

    def test_parses_warning(self) -> None:
        line = "src/foo.py:5:1: warning: Unused variable"
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(4) == "warning"

    def test_parses_note(self) -> None:
        line = "src/foo.py:5:1: note: See docs for help"
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is not None
        assert match.group(4) == "note"

    def test_no_match_on_summary(self) -> None:
        line = "Found 5 errors in 2 files (checked 3 source files)"
        match = _MYPY_OUTPUT_PATTERN.match(line)
        assert match is None

    def test_no_match_on_blank(self) -> None:
        match = _MYPY_OUTPUT_PATTERN.match("")
        assert match is None

    def test_parses_full_output(self) -> None:
        output = """\
src/a.py:10:5: error: Missing return [return]
src/a.py:15:1: error: Incompatible types [assignment]
src/b.py:3:1: note: See help
Found 2 errors in 2 files (checked 2 source files)
"""
        checker = MypyTypeChecker()
        issues = checker._parse_output(output)
        errors = [i for i in issues if i.severity == "error"]
        notes = [i for i in issues if i.severity == "note"]
        assert len(errors) == 2
        assert len(notes) == 1
        assert errors[0].file == "src/a.py"
        assert errors[0].line == 10
        assert errors[0].code == "return"

    def test_empty_input_returns_empty(self) -> None:
        checker = MypyTypeChecker()
        issues = checker._parse_output("")
        assert issues == []


class TestHypothesisAdapterInternals:
    """Tests for Hypothesis adapter internal functions."""

    def test_strategy_for_int(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(int)
        assert strategy is not None

    def test_strategy_for_float(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(float)
        assert strategy is not None

    def test_strategy_for_str(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(str)
        assert strategy is not None

    def test_strategy_for_bool(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(bool)
        assert strategy is not None

    def test_strategy_for_bytes(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(bytes)
        assert strategy is not None

    def test_no_strategy_for_none(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(None)
        assert strategy is None

    def test_strategy_for_list_int(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _get_strategy_for_annotation
        strategy = _get_strategy_for_annotation(list[int])
        assert strategy is not None

    def test_build_strategies_multiple_params(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _build_strategies_from_signature
        def func(x: int, y: str, z: float) -> bool:
            return True
        strategies = _build_strategies_from_signature(func)
        assert strategies is not None
        assert "x" in strategies
        assert "y" in strategies
        assert "z" in strategies

    def test_has_icontract_decorators_true(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _has_icontract_decorators
        import icontract
        @icontract.require(lambda x: x > 0, "positive")
        def func(x: int) -> int:
            return x
        assert _has_icontract_decorators(func) is True

    def test_has_icontract_decorators_false(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _has_icontract_decorators
        def func(x: int) -> int:
            return x
        assert _has_icontract_decorators(func) is False

    def test_check_preconditions_passing(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _check_preconditions
        import icontract
        @icontract.require(lambda x: x > 0, "positive")
        def func(x: int) -> int:
            return x
        assert _check_preconditions(func, {"x": 5}) is True

    def test_check_preconditions_failing(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _check_preconditions
        import icontract
        @icontract.require(lambda x: x > 0, "positive")
        def func(x: int) -> int:
            return x
        assert _check_preconditions(func, {"x": -1}) is False

    def test_find_nested_violation(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _find_nested_violation
        import icontract
        exc = icontract.ViolationError("test violation")
        result = _find_nested_violation(exc)
        assert result is exc

    def test_find_nested_violation_wrapped(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _find_nested_violation
        import icontract
        inner = icontract.ViolationError("inner")
        outer = RuntimeError("outer")
        outer.__cause__ = inner
        result = _find_nested_violation(outer)
        assert result is inner

    def test_find_nested_violation_none(self) -> None:
        from serenecode.adapters.hypothesis_adapter import _find_nested_violation
        exc = RuntimeError("no violation")
        result = _find_nested_violation(exc)
        assert result is None


@pytest.mark.slow
class TestCrossHairAdapterInternals:
    """Tests for CrossHair adapter internal functions.

    Marked as slow because importing CrossHair monkey-patches
    icontract internals, which breaks invariant checking in
    the same process.
    """

    def test_parse_counterexample_with_args(self) -> None:
        from serenecode.adapters.crosshair_adapter import _parse_counterexample
        msg = "when calling func(x=5, y=-1)"
        result = _parse_counterexample(msg)
        assert result is not None
        assert result["x"] == "5"
        assert result["y"] == "-1"

    def test_parse_counterexample_no_match(self) -> None:
        from serenecode.adapters.crosshair_adapter import _parse_counterexample
        msg = "Postcondition violated"
        result = _parse_counterexample(msg)
        assert result is None

    def test_parse_cli_output_empty(self) -> None:
        from serenecode.adapters.crosshair_adapter import _parse_cli_output
        findings = _parse_cli_output("test_module", "", "")
        assert len(findings) == 1
        assert findings[0].outcome == "verified"

    def test_parse_cli_output_error(self) -> None:
        from serenecode.adapters.crosshair_adapter import _parse_cli_output
        findings = _parse_cli_output(
            "test_module",
            "test.py:10: error: Postcondition failed\n",
            "",
        )
        assert len(findings) >= 1
        assert findings[0].outcome == "counterexample"

    def test_parse_cli_output_stderr(self) -> None:
        from serenecode.adapters.crosshair_adapter import _parse_cli_output
        findings = _parse_cli_output("test_module", "", "Some error occurred")
        assert len(findings) == 1
        assert findings[0].outcome == "error"
