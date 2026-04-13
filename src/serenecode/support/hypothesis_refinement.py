"""Precondition refinement for Hypothesis strategy builders.

Inspects icontract precondition lambdas to derive tighter strategies
from common patterns (membership tests, numeric bounds, predicate functions).

This is a core module — it contains pure precondition analysis logic
with no serenecode imports and is subject to full structural verification.
"""

from __future__ import annotations

import inspect
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


def _try_numeric_bounds(
    source: str, param_name: str, strategies: dict[str, SearchStrategy],
    annotations: dict[str, object],
) -> bool:
    """Match `x > N`, `x >= N` etc -> bounded integers/floats."""
    annotation = annotations.get(param_name)
    if annotation not in (int, float):
        return False
    gt_match = re.search(r'>\s*(\d+)', source)
    ge_match = re.search(r'>=\s*(\d+)', source)
    lt_match = re.search(r'<\s*(\d+)', source)
    le_match = re.search(r'<=\s*(\d+)', source)
    if not (ge_match or gt_match or le_match or lt_match):
        return False
    min_val = int(ge_match.group(1)) if ge_match else (int(gt_match.group(1)) + 1 if gt_match else None)
    max_val = int(le_match.group(1)) if le_match else (int(lt_match.group(1)) - 1 if lt_match else None)
    if min_val is None and max_val is None:
        return False
    if annotation is float:
        strategies[param_name] = st.floats(
            min_value=float(min_val) if min_val is not None else -1e6,
            max_value=float(max_val) if max_val is not None else 1e6,
            allow_nan=False, allow_infinity=False,
        )
    else:
        strategies[param_name] = st.integers(
            min_value=min_val if min_val is not None else -1000,
            max_value=max_val if max_val is not None else 1000,
        )
    return True


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
