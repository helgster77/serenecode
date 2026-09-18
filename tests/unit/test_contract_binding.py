"""Tests for the Level 1 contract-binding check.

Every case here was measured against icontract 2.7.3 first: the comment on
each test records what the contract actually does at call time, which is what
the check exists to report statically.
"""

from __future__ import annotations

import ast
import textwrap

from serenecode.checker.contract_binding import (
    check_contract_bindings,
    check_function_contract_bindings,
)
from serenecode.checker.structural import check_structural
from serenecode.checker.structural_helpers import resolve_icontract_aliases
from serenecode.config import default_config
from serenecode.models import CheckStatus


def _findings(source: str) -> list[str]:
    """Return binding-check messages for a source snippet."""
    tree = ast.parse(textwrap.dedent(source))
    aliases = resolve_icontract_aliases(tree)
    results = check_contract_bindings(tree, aliases, "sample.py")
    return [detail.message for result in results for detail in result.details]


def _function_findings(source: str) -> list[str]:
    """Return binding-check messages for the first function in a snippet."""
    tree = ast.parse(textwrap.dedent(source))
    aliases = resolve_icontract_aliases(tree)
    node = tree.body[-1]
    assert isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    return [d.message for d in check_function_contract_bindings(node, aliases)]


def test_precondition_over_var_positional_is_reported() -> None:
    """A condition over *args is flagged.

    Verifies: REQ-038

    Measured: `key("a", "")` binds `parts` to the string `''`, not the tuple,
    so the condition passes vacuously; `key("a")` raises TypeError.
    """
    messages = _findings('''
        import icontract

        @icontract.require(lambda parts: all(len(p) > 0 for p in parts), "non-empty")
        def key(kind: str, *parts: str) -> str:
            return ":".join((kind, *parts))
    ''')
    assert len(messages) == 1
    assert "'*parts'" in messages[0]
    assert "_ARGS" not in messages[0]


def test_var_positional_suggestion_names_the_args_placeholder() -> None:
    """The suggestion points at icontract's supported spelling.

    Verifies: REQ-038
    """
    tree = ast.parse(textwrap.dedent('''
        import icontract

        @icontract.require(lambda parts: len(parts) > 0, "non-empty")
        def key(*parts: str) -> str:
            return ":".join(parts)
    '''))
    results = check_contract_bindings(tree, resolve_icontract_aliases(tree), "s.py")
    assert results[0].status == CheckStatus.FAILED
    assert "_ARGS" in results[0].details[0].suggestion


def test_precondition_over_var_keyword_is_reported() -> None:
    """A condition over **kwargs is flagged.

    Verifies: REQ-039

    Measured: every call raises TypeError, because icontract exposes the
    keyword mapping as `_KWARGS` and never under the parameter's own name.
    """
    messages = _function_findings('''
        import icontract

        @icontract.require(lambda extra: len(extra) > 0, "non-empty")
        def total(base: int, **extra: int) -> int:
            return base + sum(extra.values())
    ''')
    assert len(messages) == 1
    assert "'**extra'" in messages[0]


def test_condition_parameter_absent_from_signature_is_reported() -> None:
    """A misspelled condition parameter is flagged.

    Verifies: REQ-040

    Measured: every call raises TypeError naming the unset argument.
    """
    messages = _function_findings('''
        import icontract

        @icontract.require(lambda valu: valu > 0, "positive")
        def double(value: int) -> int:
            return value * 2
    ''')
    assert len(messages) == 1
    assert "'valu'" in messages[0]


def test_orphaned_decorator_stack_is_reported() -> None:
    """A contract stack left on the wrong function is flagged.

    Verifies: REQ-040

    This is the decorator/def orphaning case: an edit inserted `renamed`
    between the stack and the function the contract was written for.
    """
    messages = _findings('''
        import icontract

        @icontract.require(lambda items: len(items) > 0, "non-empty")
        @icontract.ensure(lambda result: result > 0, "positive")
        def renamed(count: int) -> int:
            return count
    ''')
    assert any("'items'" in message for message in messages)


def test_defaulted_condition_parameter_is_not_reported() -> None:
    """Condition parameters with defaults capture constants, not bindings.

    Verifies: REQ-040

    Measured: icontract leaves the parameter at its default and the
    condition evaluates normally, so there is nothing to report.
    """
    assert _function_findings('''
        import icontract

        @icontract.ensure(lambda result, eps=1e-9: abs(result) > eps, "non-zero")
        def ratio(value: float) -> float:
            return value + 1.0
    ''') == []


def test_icontract_placeholders_are_not_reported() -> None:
    """_ARGS, _KWARGS, result and OLD are supplied by icontract.

    Verifies: REQ-037
    """
    assert _function_findings('''
        import icontract

        @icontract.require(lambda _ARGS: all(_ARGS), "all truthy")
        @icontract.require(lambda _KWARGS: True is not False, "placeholder")
        @icontract.ensure(lambda result: result > 0, "positive")
        def total(*values: int, **extra: int) -> int:
            return sum(values) + sum(extra.values())
    ''') == []


def test_result_in_precondition_is_reported() -> None:
    """`result` is a postcondition-only name.

    Verifies: REQ-037
    """
    messages = _function_findings('''
        import icontract

        @icontract.require(lambda result: result > 0, "positive")
        def compute(value: int) -> int:
            return value
    ''')
    assert len(messages) == 1
    assert "'result'" in messages[0]


def test_postcondition_reading_result_on_none_is_reported() -> None:
    """A postcondition over `result` on `-> None` is flagged.

    Verifies: REQ-041

    Measured: the call raises `TypeError: object of type 'NoneType' has no
    len()`, because icontract binds `result` to None.
    """
    messages = _function_findings('''
        import icontract

        @icontract.ensure(lambda result: len(result) > 0, "non-empty")
        def returns_nothing(x: int) -> None:
            _ = x
    ''')
    assert len(messages) == 1
    assert "-> None" in messages[0]


def test_none_guard_postcondition_on_none_is_allowed() -> None:
    """`result is None` stays legal on a `-> None` function.

    Verifies: REQ-041

    Measured: the call succeeds; the condition is meaningful, if weak.
    """
    assert _function_findings('''
        import icontract

        @icontract.ensure(lambda result: result is None, "returns nothing")
        def returns_nothing(x: int) -> None:
            _ = x
    ''') == []


def test_result_on_annotated_return_is_allowed() -> None:
    """A real return type leaves `result` alone.

    Verifies: REQ-041
    """
    assert _function_findings('''
        import icontract

        @icontract.ensure(lambda result: len(result) > 0, "non-empty")
        def name(prefix: str) -> str:
            return prefix + "!"
    ''') == []


def test_non_lambda_conditions_are_skipped() -> None:
    """Named predicates carry no parameter names in the AST.

    Verifies: REQ-037
    """
    assert _function_findings('''
        import icontract

        from serenecode.contracts.predicates import is_non_empty_string

        @icontract.require(is_non_empty_string, "non-empty")
        def shout(text: str) -> str:
            return text.upper()
    ''') == []


def test_private_functions_are_checked() -> None:
    """Visibility does not excuse an unenforceable contract.

    Verifies: REQ-037
    """
    messages = _findings('''
        import icontract

        @icontract.require(lambda valu: valu > 0, "positive")
        def _double(value: int) -> int:
            return value * 2
    ''')
    assert len(messages) == 1


def test_methods_bind_self_normally() -> None:
    """`self` is an ordinary signature parameter for binding purposes.

    Verifies: REQ-037
    """
    assert _findings('''
        import icontract

        class Counter:
            """A counter."""

            @icontract.require(lambda self, step: step > 0, "positive step")
            @icontract.ensure(lambda result: result >= 0, "non-negative")
            def bump(self, step: int) -> int:
                """Bump the counter."""
                return step
    ''') == []


def test_import_alias_forms_are_recognized() -> None:
    """Aliased icontract imports are resolved like the dotted form.

    Verifies: REQ-037
    """
    messages = _findings('''
        from icontract import require

        @require(lambda valu: valu > 0, "positive")
        def double(value: int) -> int:
            return value * 2
    ''')
    assert len(messages) == 1


def test_binding_findings_run_inside_the_structural_check() -> None:
    """The binding check is wired into the Level 1 structural block.

    Verifies: REQ-042, INT-005
    """
    source = textwrap.dedent('''
        """Sample module."""

        from __future__ import annotations

        import icontract


        @icontract.require(lambda parts: all(parts), "all truthy")
        @icontract.ensure(lambda result: len(result) > 0, "non-empty")
        def build(kind: str, *parts: str) -> str:
            """Build a key.

            Args:
                kind: Leading segment.
                parts: Trailing segments.

            Returns:
                The joined key.
            """
            return ":".join((kind, *parts))
    ''')
    result = check_structural(source, default_config(), file_path="sample.py")
    assert result.passed is False
    messages = [
        detail.message
        for record in result.results
        for detail in record.details
    ]
    assert any("'*parts'" in message for message in messages)


def test_clean_contracts_produce_no_records() -> None:
    """The check adds findings only; it never emits passing records.

    Verifies: REQ-037
    """
    tree = ast.parse(textwrap.dedent('''
        import icontract

        @icontract.require(lambda value: value > 0, "positive")
        @icontract.ensure(lambda result: result > 0, "positive")
        def double(value: int) -> int:
            return value * 2
    '''))
    assert check_contract_bindings(tree, resolve_icontract_aliases(tree), "s.py") == []
