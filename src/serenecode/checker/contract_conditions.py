"""Shape analysis of icontract decorator arguments (Level 1).

Answers three questions about a single contract decorator call, from its AST
alone: which expression is its condition, whether the contract is switched
off, and whether the condition can ever be false. A contract that is disabled
or that is always true satisfies the "has a contract" rule while constraining
nothing, so the structural checker reports it instead of counting it.

This is a core module — no I/O operations are permitted. Decorators are
received as AST nodes, not read from files.
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import cast

import icontract

_CONDITION_KEYWORD = "condition"
_ENABLED_KEYWORD = "enabled"

# Comparison operators that can be evaluated when both operands are literals.
# The cast widens the operand types: unorderable literals raise TypeError,
# which _comparison_truth treats as "no fixed result".
_LITERAL_COMPARISONS = cast("dict[type[ast.cmpop], Callable[[object, object], object]]", {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
})


@icontract.require(lambda decorator: isinstance(decorator, ast.expr), "decorator must be an AST expression")
@icontract.ensure(
    lambda decorator, result: result is None or isinstance(decorator, ast.Call),
    "only a decorator call carries a condition",
)
def contract_condition(decorator: ast.expr) -> ast.expr | None:
    """Return the condition expression of a contract decorator call.

    Implements: REQ-060

    icontract accepts the condition as the first positional argument or as
    the ``condition=`` keyword. Both spellings bind identically at runtime,
    so both must be visible to the checks that inspect the condition.

    Args:
        decorator: A decorator expression.

    Returns:
        The condition expression, or None when the decorator is not a call
        or passes no condition.
    """
    if not isinstance(decorator, ast.Call):
        return None
    if decorator.args:
        return decorator.args[0]
    # Loop invariant: no keyword in keywords[0..i] is the condition keyword
    for keyword in decorator.keywords:
        if keyword.arg == _CONDITION_KEYWORD:
            return keyword.value
    return None


@icontract.require(lambda decorator: isinstance(decorator, ast.expr), "decorator must be an AST expression")
@icontract.ensure(
    lambda decorator, result: not result or isinstance(decorator, ast.Call),
    "only a decorator call can be disabled",
)
def is_disabled_contract(decorator: ast.expr) -> bool:
    """Return True for a contract decorator carrying a literal ``enabled=False``.

    Implements: REQ-059

    icontract skips such a contract entirely, so it documents an intention
    and enforces nothing. A non-literal ``enabled=`` expression is left
    alone: its value is not knowable from the AST.

    Args:
        decorator: A decorator expression.
    """
    if not isinstance(decorator, ast.Call):
        return False
    # Loop invariant: no keyword in keywords[0..i] is a literal enabled=False
    for keyword in decorator.keywords:
        if (
            keyword.arg == _ENABLED_KEYWORD
            and isinstance(keyword.value, ast.Constant)
            and keyword.value.value is False
        ):
            return True
    return False


@icontract.require(lambda condition: isinstance(condition, ast.expr), "condition must be an AST expression")
@icontract.ensure(
    lambda condition, result: not result or isinstance(condition, ast.Lambda),
    "only a lambda condition can be judged from its AST",
)
def is_always_true(condition: ast.expr) -> bool:
    """Return True when a lambda condition cannot evaluate to a false value.

    Implements: REQ-061

    Named predicates and partials carry no body in the decorator's AST, so
    they are never reported here.

    Args:
        condition: A contract condition expression.
    """
    if not isinstance(condition, ast.Lambda):
        return False
    return _constant_truth(condition.body) is True


@icontract.require(lambda expr: isinstance(expr, ast.expr), "expr must be an AST expression")
@icontract.ensure(lambda result: result is None or isinstance(result, bool), "result must be a bool or None")
def _constant_truth(expr: ast.expr) -> bool | None:
    """Evaluate an expression's truth value when it does not depend on any input.

    Returns:
        True or False when the value is fixed, None when it depends on a name.
    """
    # Variant: the depth of the remaining expression tree decreases
    if isinstance(expr, ast.Constant):
        return bool(expr.value)
    if isinstance(expr, ast.UnaryOp) and isinstance(expr.op, ast.Not):
        operand = _constant_truth(expr.operand)
        return None if operand is None else not operand
    if isinstance(expr, ast.BoolOp):
        return _boolean_operation_truth(expr)
    if isinstance(expr, ast.Compare):
        return _comparison_truth(expr)
    if isinstance(expr, ast.Call):
        return True if _is_isinstance_of_object(expr) else None
    return None


@icontract.require(lambda expr: isinstance(expr, ast.BoolOp), "expr must be a boolean operation")
@icontract.ensure(lambda result: result is None or isinstance(result, bool), "result must be a bool or None")
def _boolean_operation_truth(expr: ast.BoolOp) -> bool | None:
    """Evaluate ``and`` / ``or`` when its outcome is fixed by constant operands.

    One constant-true operand decides an ``or``; one constant-false operand
    decides an ``and``. Otherwise every operand has to be constant.
    """
    truths = [_constant_truth(value) for value in expr.values]
    decisive = isinstance(expr.op, ast.Or)
    if any(truth is decisive for truth in truths):
        return decisive
    if all(truth is (not decisive) for truth in truths):
        return not decisive
    return None


@icontract.require(lambda expr: isinstance(expr, ast.Compare), "expr must be a comparison")
@icontract.ensure(lambda result: result is None or isinstance(result, bool), "result must be a bool or None")
def _comparison_truth(expr: ast.Compare) -> bool | None:
    """Evaluate a single comparison whose outcome does not depend on any input.

    ``x is x`` is true for every value. ``x == x`` is deliberately not
    treated as fixed: it is false for a float NaN, which makes it a real,
    if obscure, constraint.
    """
    if len(expr.ops) != 1:
        return None
    left, right, op = expr.left, expr.comparators[0], expr.ops[0]
    if isinstance(op, ast.Is) and ast.dump(left) == ast.dump(right):
        return True
    compare = _LITERAL_COMPARISONS.get(type(op))
    if compare is None or not isinstance(left, ast.Constant) or not isinstance(right, ast.Constant):
        return None
    # silent-except: literals of unorderable types have no fixed comparison result
    try:
        return bool(compare(left.value, right.value))
    except TypeError:
        return None


@icontract.require(lambda call: isinstance(call, ast.Call), "call must be a call expression")
@icontract.ensure(
    lambda call, result: not result or len(call.args) == 2,
    "isinstance takes exactly two arguments",
)
def _is_isinstance_of_object(call: ast.Call) -> bool:
    """Return True for ``isinstance(<anything>, object)``, which every value satisfies."""
    if not isinstance(call.func, ast.Name) or call.func.id != "isinstance":
        return False
    if len(call.args) != 2 or call.keywords:
        return False
    class_argument = call.args[1]
    return isinstance(class_argument, ast.Name) and class_argument.id == "object"
