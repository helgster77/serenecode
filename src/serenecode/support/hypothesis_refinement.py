"""Precondition refinement for Hypothesis strategy builders.

Inspects icontract precondition lambdas to derive tighter strategies
from common patterns (membership tests, numeric bounds, predicate functions).

This is a core module — it contains pure precondition analysis logic
with no serenecode imports and is subject to full structural verification.
"""

from __future__ import annotations

import ast
import inspect
import math
import re
from typing import Callable

import icontract

# silent-except: hypothesis is an optional dependency; graceful fallback when not installed
try:
    from hypothesis import strategies as st
    from hypothesis.strategies import SearchStrategy
except ImportError:
    pass

_KNOWN_PREDICATES: dict[str, Callable[[], SearchStrategy]] = {}


@icontract.ensure(
    lambda result: isinstance(result, dict) and len(result) > 0,
    "result must be a non-empty dict",
)
def _init_known_predicates() -> dict[str, Callable[[], SearchStrategy]]:
    """Lazily build the known-predicate lookup."""
    return {
        "is_valid_verification_level": lambda: st.integers(min_value=1, max_value=6),
        "is_non_negative_int": lambda: st.integers(min_value=0, max_value=1000),
        "is_positive_int": lambda: st.integers(min_value=1, max_value=1000),
        "is_non_empty_string": lambda: st.text(min_size=1, max_size=100).filter(
            lambda value: len(value.strip()) > 0
        ),
        "is_valid_template_name": lambda: st.sampled_from(["default", "strict", "minimal"]),
    }


@icontract.require(lambda func: callable(func), "func must be callable")
@icontract.require(lambda strategies: isinstance(strategies, dict), "strategies must be a dict")
@icontract.require(lambda annotations: isinstance(annotations, dict), "annotations must be a dict")
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dict")
def _refine_strategies_with_preconditions(
    func: Callable[..., object],
    strategies: dict[str, SearchStrategy],
    annotations: dict[str, object],
) -> dict[str, SearchStrategy]:
    """Refine strategies using icontract preconditions."""
    preconditions = getattr(func, "__preconditions__", None)
    if not preconditions:
        return strategies
    refined = dict(strategies)
    # Loop invariant: refined updated for groups[0..i]
    for group in preconditions:
        for contract in group:
            _try_refine_from_condition(contract.condition, refined, annotations)
    return refined


@icontract.require(lambda condition: callable(condition), "condition must be callable")
@icontract.require(lambda strategies: isinstance(strategies, dict), "strategies must be a dict")
@icontract.ensure(lambda result: result is None, "refinement happens in place")
def _try_refine_from_condition(
    condition: Callable[..., bool],
    strategies: dict[str, SearchStrategy],
    annotations: dict[str, object],
) -> None:
    """Try to refine strategies based on a single precondition."""
    # silent-except: signature introspection can fail for built-ins
    try:
        cond_sig = inspect.signature(condition)
        cond_params = [p for p in cond_sig.parameters if p not in ("self", "cls")]
    except (ValueError, TypeError):
        return
    if len(cond_params) != 1:
        return
    param_name = cond_params[0]
    if param_name not in strategies:
        return

    source = _get_lambda_source(condition)
    effective_source = _expand_predicate_source(condition, source, param_name)

    if _try_known_predicate(condition, source, param_name, strategies):
        return
    if _try_membership_pattern(effective_source, param_name, strategies):
        return
    if _try_numeric_bounds(source, param_name, strategies, annotations):
        return
    _try_filter_fallback(condition, param_name, strategies, annotations)


@icontract.require(lambda condition: callable(condition), "condition must be callable")
@icontract.require(lambda source: isinstance(source, str), "source must be a string")
@icontract.require(lambda param_name: isinstance(param_name, str), "param_name must be a string")
@icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
def _expand_predicate_source(
    condition: Callable[..., bool], source: str, param_name: str,
) -> str:
    """If the condition calls a predicate function, expand with that function's source."""
    effective = source
    func_call_match = re.search(r'(\w+)\s*\(\s*' + re.escape(param_name) + r'\s*\)', source)
    if func_call_match:
        called_name = func_call_match.group(1)
        called_func = getattr(condition, "__globals__", {}).get(called_name)
        if called_func and callable(called_func):
            # silent-except: source extraction may fail for built-in functions
            try:
                effective = source + "\n" + inspect.getsource(called_func)
            except (OSError, TypeError):
                pass
    return effective


@icontract.require(lambda condition: callable(condition), "condition must be callable")
@icontract.require(lambda source: isinstance(source, str), "source must be a string")
@icontract.require(lambda param_name: isinstance(param_name, str), "param_name must be a string")
@icontract.require(lambda strategies: isinstance(strategies, dict), "strategies must be a dict")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _try_known_predicate(
    condition: Callable[..., bool], source: str, param_name: str,
    strategies: dict[str, SearchStrategy],
) -> bool:
    """Match known SereneCode contract predicates."""
    func_call_match = re.search(r'(\w+)\s*\(\s*' + re.escape(param_name) + r'\s*\)', source)
    if not func_call_match:
        return False
    called_name = func_call_match.group(1)
    known = _init_known_predicates()
    if called_name in known:
        strategies[param_name] = known[called_name]()
        return True
    return False


@icontract.require(
    lambda effective_source: isinstance(effective_source, str),
    "effective_source must be a string",
)
@icontract.require(lambda param_name: isinstance(param_name, str), "param_name must be a string")
@icontract.require(lambda strategies: isinstance(strategies, dict), "strategies must be a dict")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _try_membership_pattern(
    effective_source: str, param_name: str, strategies: dict[str, SearchStrategy],
) -> bool:
    """Match `x in (...)` patterns -> st.sampled_from."""
    in_match = re.search(r'in\s*[\(\[\{]\s*(.+?)\s*[\)\]\}]', effective_source)
    if not in_match:
        return False
    items = _parse_literal_collection(in_match.group(1))
    if items:
        strategies[param_name] = st.sampled_from(items)
        return True
    return False


@icontract.require(lambda source: isinstance(source, str), "source must be a string")
@icontract.require(lambda param_name: isinstance(param_name, str), "param_name must be a string")
@icontract.require(lambda strategies: isinstance(strategies, dict), "strategies must be a dict")
@icontract.require(lambda annotations: isinstance(annotations, dict), "annotations must be a dict")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _try_numeric_bounds(
    source: str, param_name: str, strategies: dict[str, SearchStrategy],
    annotations: dict[str, object],
) -> bool:
    """Match comparisons against numeric literals -> bounded integers/floats.

    Implements: REQ-048

    Bounds are read from the condition's AST, so `x > 0`, `0 < x`, and the
    chained `0.0 < x < 1.0` are all understood, decimal literals keep their
    fractional part, and a strict inequality becomes an excluded endpoint
    rather than a shift by one — shifting by one is right for integers and
    turns a float bound into a domain that excludes the whole contract.
    """
    annotation = annotations.get(param_name)
    if annotation not in (int, float):
        return False
    bounds = _numeric_bounds_from_source(source, param_name)
    if bounds is None:
        return False
    lower, upper = bounds
    if not _bounds_are_satisfiable(lower, upper):
        return False

    if annotation is float:
        strategies[param_name] = st.floats(
            min_value=lower[0] if lower is not None else -1e6,
            max_value=upper[0] if upper is not None else 1e6,
            exclude_min=lower is not None and lower[1],
            exclude_max=upper is not None and upper[1],
            allow_nan=False, allow_infinity=False,
        )
    else:
        strategies[param_name] = st.integers(
            min_value=_integer_bound(lower, -1000.0, is_lower=True),
            max_value=_integer_bound(upper, 1000.0, is_lower=False),
        )
    return True


@icontract.require(lambda source: isinstance(source, str), "source must be a string")
@icontract.require(lambda param_name: isinstance(param_name, str), "param_name must be a string")
@icontract.ensure(
    lambda result: result is None or (isinstance(result, tuple) and len(result) == 2),
    "result must be a (lower, upper) pair or None",
)
def _numeric_bounds_from_source(
    source: str, param_name: str,
) -> tuple[tuple[float, bool] | None, tuple[float, bool] | None] | None:
    """Extract the tightest numeric bounds a condition places on a parameter.

    Implements: REQ-048

    Args:
        source: Source text of the condition lambda.
        param_name: The parameter the bounds must constrain.

    Returns:
        A `(lower, upper)` pair, each either None or `(value, exclusive)`, or
        None when the condition places no parsable numeric bound on the
        parameter.
    """
    # silent-except: a condition whose source fragment does not parse yields no bounds
    try:
        tree = ast.parse(source.strip(), mode="eval")
    except (SyntaxError, TypeError, ValueError):
        return None

    lower: tuple[float, bool] | None = None
    upper: tuple[float, bool] | None = None

    # Loop invariant: lower/upper hold the tightest bounds seen in nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left] + list(node.comparators)
        for index, operator in enumerate(node.ops):
            left, right = operands[index], operands[index + 1]
            side = _bound_from_operands(left, operator, right, param_name)
            if side is None:
                continue
            is_lower, bound = side
            if is_lower:
                lower = _tighter_bound(lower, bound, is_lower=True)
            else:
                upper = _tighter_bound(upper, bound, is_lower=False)

    if lower is None and upper is None:
        return None
    return (lower, upper)


@icontract.require(lambda param_name: isinstance(param_name, str), "param_name must be a string")
@icontract.ensure(
    lambda result: result is None or (isinstance(result, tuple) and len(result) == 2),
    "result must be an (is_lower, bound) pair or None",
)
def _bound_from_operands(
    left: ast.expr, operator: ast.cmpop, right: ast.expr, param_name: str,
) -> tuple[bool, tuple[float, bool]] | None:
    """Read one comparison as a bound on `param_name`, in either orientation."""
    exclusive = isinstance(operator, (ast.Lt, ast.Gt))
    if not isinstance(operator, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
        return None

    if _is_param(left, param_name):
        value = _numeric_literal(right)
        if value is None:
            return None
        # `param < N` bounds above; `param > N` bounds below.
        is_lower = isinstance(operator, (ast.Gt, ast.GtE))
        return (is_lower, (value, exclusive))

    if _is_param(right, param_name):
        value = _numeric_literal(left)
        if value is None:
            return None
        # `N < param` bounds below; `N > param` bounds above.
        is_lower = isinstance(operator, (ast.Lt, ast.LtE))
        return (is_lower, (value, exclusive))

    return None


@icontract.require(lambda param_name: isinstance(param_name, str), "param_name must be a string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _is_param(node: ast.expr, param_name: str) -> bool:
    """Return True when the node is a plain reference to the parameter."""
    return isinstance(node, ast.Name) and node.id == param_name


@icontract.require(lambda node: isinstance(node, ast.AST), "node must be an AST node")
@icontract.ensure(
    lambda result: result is None or isinstance(result, float),
    "result must be a float or None",
)
def _numeric_literal(node: ast.expr) -> float | None:
    """Return the value of an int/float literal, including a negated one."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _numeric_literal(node.operand)
        return None if inner is None else -inner
    if not isinstance(node, ast.Constant):
        return None
    # bool is a subclass of int, but `True` is not a numeric bound.
    if isinstance(node.value, bool):
        return None
    if isinstance(node.value, (int, float)):
        return float(node.value)
    return None


@icontract.require(lambda is_lower: isinstance(is_lower, bool), "is_lower must be a bool")
@icontract.ensure(
    lambda result: isinstance(result, tuple) and len(result) == 2,
    "result must be a (value, exclusive) pair",
)
def _tighter_bound(
    current: tuple[float, bool] | None,
    candidate: tuple[float, bool],
    is_lower: bool,
) -> tuple[float, bool]:
    """Keep whichever of two bounds constrains the domain more."""
    if current is None:
        return candidate
    if candidate[0] == current[0]:
        return (candidate[0], candidate[1] or current[1])
    if is_lower:
        return candidate if candidate[0] > current[0] else current
    return candidate if candidate[0] < current[0] else current


@icontract.require(
    lambda lower: lower is None or (isinstance(lower, tuple) and len(lower) == 2),
    "lower must be a (value, exclusive) pair or None",
)
@icontract.require(
    lambda upper: upper is None or (isinstance(upper, tuple) and len(upper) == 2),
    "upper must be a (value, exclusive) pair or None",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _bounds_are_satisfiable(
    lower: tuple[float, bool] | None,
    upper: tuple[float, bool] | None,
) -> bool:
    """Return True when some value lies between the bounds.

    Implements: REQ-048

    An empty range would otherwise be handed to Hypothesis as a strategy no
    draw can satisfy, so the caller falls back to filtering instead.
    """
    if lower is None or upper is None:
        return True
    if lower[0] < upper[0]:
        return True
    return lower[0] == upper[0] and not lower[1] and not upper[1]


@icontract.require(lambda fallback: isinstance(fallback, float), "fallback must be a float")
@icontract.require(lambda is_lower: isinstance(is_lower, bool), "is_lower must be a bool")
@icontract.ensure(lambda result: isinstance(result, int), "result must be an int")
def _integer_bound(
    bound: tuple[float, bool] | None, fallback: float, is_lower: bool,
) -> int:
    """Convert a bound to the nearest integer inside the allowed range."""
    if bound is None:
        return int(fallback)
    value, exclusive = bound
    if is_lower:
        inclusive = math.ceil(value)
        return inclusive + 1 if exclusive and inclusive == value else inclusive
    inclusive = math.floor(value)
    return inclusive - 1 if exclusive and inclusive == value else inclusive


@icontract.require(lambda condition: callable(condition), "condition must be callable")
@icontract.require(lambda param_name: isinstance(param_name, str), "param_name must be a string")
@icontract.require(lambda strategies: isinstance(strategies, dict), "strategies must be a dict")
@icontract.require(lambda annotations: isinstance(annotations, dict), "annotations must be a dict")
@icontract.ensure(lambda result: result is None, "refinement happens in place")
def _try_filter_fallback(
    condition: Callable[..., bool], param_name: str,
    strategies: dict[str, SearchStrategy], annotations: dict[str, object],
) -> None:
    """Apply condition as a filter on the existing strategy as last resort."""
    annotation = annotations.get(param_name)
    is_literal_like = annotation in (str, int, float)
    if not is_literal_like and not inspect.isclass(annotation):
        return
    # silent-except: filter may fail if strategy and condition are incompatible
    try:
        strategies[param_name] = strategies[param_name].filter(condition)
    except Exception:
        pass


@icontract.require(lambda condition: callable(condition), "condition must be callable")
@icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
def _get_lambda_source(condition: Callable[..., bool]) -> str:
    """Extract the source of a lambda condition only."""
    # silent-except: source extraction may fail for dynamically-created callables
    try:
        full_source = inspect.getsource(condition).strip()
    except (OSError, TypeError):
        return ""
    if condition.__name__ != "<lambda>":
        return full_source.split("\n")[0]
    # silent-except: signature introspection can fail
    try:
        cond_params = list(inspect.signature(condition).parameters.keys())
    except (ValueError, TypeError):
        cond_params = []
    # Loop invariant: checked lines[0..i] for matching lambda
    for line in full_source.split("\n"):
        line = line.strip()
        if "lambda" not in line:
            continue
        lambda_match = re.search(r"lambda\s+.+", line)
        if lambda_match:
            lambda_text = re.sub(r",\s*$", "", lambda_match.group(0)).strip()
            if cond_params and all(p in lambda_text for p in cond_params):
                return lambda_text
    return ""


@icontract.require(lambda items_str: isinstance(items_str, str), "items_str must be a string")
@icontract.ensure(lambda result: result is None or isinstance(result, list), "result must be a list or None")
def _parse_literal_collection(items_str: str) -> list[object] | None:
    """Parse a string of literal values from source code."""
    items: list[object] = []
    # Loop invariant: items contains parsed values for parts[0..i]
    for part in items_str.split(","):
        part = part.strip()
        if not part:
            continue
        if (part.startswith('"') and part.endswith('"')) or (part.startswith("'") and part.endswith("'")):
            items.append(part[1:-1])
        elif part.lstrip("-").isdigit():
            items.append(int(part))
        else:
            # silent-except: unparseable literal falls back to None
            try:
                items.append(float(part))
            except ValueError:
                return None
    return items if items else None
