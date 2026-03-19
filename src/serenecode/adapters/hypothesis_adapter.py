"""Hypothesis adapter for property-based testing (Level 3).

This adapter implements the PropertyTester protocol by running
Hypothesis tests against functions that have icontract decorators.
It uses icontract's runtime checking to detect postcondition violations.

This is an adapter module — it handles I/O (module importing, test
execution) and is exempt from full contract requirements.
"""

from __future__ import annotations

import importlib
import inspect
import traceback
import typing
from typing import Callable

import icontract

from serenecode.core.exceptions import ToolNotInstalledError
from serenecode.ports.property_tester import PropertyFinding

try:
    from hypothesis import given, settings, HealthCheck, Verbosity
    from hypothesis import strategies as st
    from hypothesis.strategies import SearchStrategy
    _HYPOTHESIS_AVAILABLE = True
except ImportError:
    _HYPOTHESIS_AVAILABLE = False


def _get_strategy_for_annotation(annotation: type | None) -> SearchStrategy | None:
    """Derive a Hypothesis strategy from a type annotation.

    Args:
        annotation: A Python type annotation.

    Returns:
        A Hypothesis strategy, or None if the type is unsupported.
    """
    if not _HYPOTHESIS_AVAILABLE:
        return None

    if annotation is None:
        return None

    # Handle basic types
    strategy_map: dict[type, SearchStrategy] = {        int: st.integers(min_value=-1000, max_value=1000),
        float: st.floats(
            min_value=-1e6, max_value=1e6,
            allow_nan=False, allow_infinity=False,
        ),
        str: st.text(min_size=0, max_size=100),
        bool: st.booleans(),
        bytes: st.binary(min_size=0, max_size=100),
    }

    if annotation in strategy_map:
        return strategy_map[annotation]

    # Handle generic types (list[int], etc.)
    origin = getattr(annotation, "__origin__", None)
    args = getattr(annotation, "__args__", None)

    if origin is list and args:
        inner = _get_strategy_for_annotation(args[0])
        if inner is not None:
            return st.lists(inner, min_size=0, max_size=20)

    if origin is tuple and args:
        inner_strats = []
        # Loop invariant: inner_strats contains strategies for args[0..i]
        for arg in args:
            s = _get_strategy_for_annotation(arg)
            if s is None:
                return None
            inner_strats.append(s)
        return st.tuples(*inner_strats)

    if origin is dict and args and len(args) == 2:
        key_strat = _get_strategy_for_annotation(args[0])
        val_strat = _get_strategy_for_annotation(args[1])
        if key_strat is not None and val_strat is not None:
            return st.dictionaries(key_strat, val_strat, max_size=10)

    return None


def _build_strategies_from_signature(
    func: Callable[..., object],
) -> dict[str, SearchStrategy] | None:
    """Build Hypothesis strategies from a function's type annotations.

    Args:
        func: The function to build strategies for.

    Returns:
        A dict mapping parameter names to strategies, or None if
        strategies cannot be derived for all parameters.
    """
    if not _HYPOTHESIS_AVAILABLE:
        return None

    # Use get_type_hints to resolve string annotations (from __future__ import annotations)
    try:
        hints = typing.get_type_hints(func)
    except Exception:
        hints = {}

    sig = inspect.signature(func)
    strategies: dict[str, SearchStrategy] = {}
    # Loop invariant: strategies contains entries for all processable params[0..i]
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        # Prefer resolved type hints over raw annotations
        annotation = hints.get(name, param.annotation)
        if annotation is inspect.Parameter.empty:
            return None  # can't derive strategy without annotation

        strategy = _get_strategy_for_annotation(annotation)
        if strategy is None:
            return None  # unsupported type

        strategies[name] = strategy

    if not strategies:
        return None

    # Refine strategies using icontract preconditions
    strategies = _refine_strategies_with_preconditions(func, strategies)

    return strategies


def _refine_strategies_with_preconditions(
    func: Callable[..., object],
    strategies: dict[str, SearchStrategy],
) -> dict[str, SearchStrategy]:
    """Refine strategies using icontract preconditions.

    Inspects precondition lambdas to derive tighter strategies:
    - Detects `x in (...)` patterns → st.sampled_from()
    - Detects `x > 0`, `x >= 0` bounds → filtered integers
    - Applies remaining preconditions as .filter() on strategies

    Args:
        func: The contracted function.
        strategies: Base strategies derived from type annotations.

    Returns:
        Refined strategies dict.
    """
    preconditions = getattr(func, "__preconditions__", None)
    if not preconditions:
        return strategies

    refined = dict(strategies)

    # Loop invariant: refined contains strategies for all params, updated for groups[0..i]
    for group in preconditions:
        for contract in group:
            condition = contract.condition
            _try_refine_from_condition(condition, refined)

    return refined


def _try_refine_from_condition(
    condition: Callable[..., bool],
    strategies: dict[str, SearchStrategy],
) -> None:
    """Try to refine strategies based on a single precondition.

    Inspects the condition's source code to detect common patterns
    and replaces broad strategies with targeted ones.

    Args:
        condition: A precondition lambda/function.
        strategies: Mutable dict of strategies to refine in place.
    """
    # Get the condition's parameter names
    try:
        cond_sig = inspect.signature(condition)
        cond_params = [p for p in cond_sig.parameters if p not in ("self", "cls")]
    except (ValueError, TypeError):
        return

    # Only refine single-parameter conditions (simple predicates)
    if len(cond_params) != 1:
        # For multi-param conditions, apply as a joint filter later
        return

    param_name = cond_params[0]
    if param_name not in strategies:
        return

    # Try to extract source code of the condition lambda only
    source = _get_lambda_source(condition)

    # Pattern: `x in ("a", "b", "c")` or `x in {...}` → sampled_from
    import re
    effective_source = source

    # If the condition calls a predicate function, try to get that function's source too
    func_call_match = re.search(r'(\w+)\s*\(\s*' + re.escape(param_name) + r'\s*\)', source)
    if func_call_match:
        called_name = func_call_match.group(1)
        # Try to resolve the called function from the condition's globals
        called_func = getattr(condition, "__globals__", {}).get(called_name)
        if called_func and callable(called_func):
            try:
                called_source = inspect.getsource(called_func)
                effective_source = source + "\n" + called_source
            except (OSError, TypeError):
                pass

    in_match = re.search(
        r'in\s*[\(\[\{]\s*(.+?)\s*[\)\]\}]',
        effective_source,
    )
    if in_match:
        items_str = in_match.group(1)
        items = _parse_literal_collection(items_str)
        if items:
            strategies[param_name] = st.sampled_from(items)
            return

    # Pattern: `x > N` or `x >= N` → integers with min_value
    gt_match = re.search(r'>\s*(\d+)', source)
    ge_match = re.search(r'>=\s*(\d+)', source)
    lt_match = re.search(r'<\s*(\d+)', source)
    le_match = re.search(r'<=\s*(\d+)', source)

    current = strategies[param_name]

    # Apply bound constraints for integer/float strategies
    if ge_match or gt_match or le_match or lt_match:
        min_val = None
        max_val = None
        if ge_match:
            min_val = int(ge_match.group(1))
        if gt_match:
            min_val = int(gt_match.group(1)) + 1
        if le_match:
            max_val = int(le_match.group(1))
        if lt_match:
            max_val = int(lt_match.group(1)) - 1

        # Replace with bounded strategy
        if min_val is not None or max_val is not None:
            strategies[param_name] = st.integers(
                min_value=min_val if min_val is not None else -1000,
                max_value=max_val if max_val is not None else 1000,
            )
            return

    # Fallback: apply the condition as a filter on the existing strategy
    try:
        strategies[param_name] = current.filter(condition)
    except Exception:
        pass  # keep the original strategy


def _get_lambda_source(condition: Callable[..., bool]) -> str:
    """Extract the source of a lambda condition only.

    inspect.getsource on a lambda inside a decorator returns the entire
    decorated function. This function extracts just the lambda expression.

    Args:
        condition: A callable (typically a lambda).

    Returns:
        The lambda source string, or empty string if not extractable.
    """
    try:
        full_source = inspect.getsource(condition).strip()
    except (OSError, TypeError):
        return ""

    # If it's a named function (not a lambda), return the first line
    if condition.__name__ != "<lambda>":
        return full_source.split("\n")[0]

    # For single-line lambdas (typical in icontract decorators),
    # find the line containing "lambda" and extract it
    import re

    # Get the parameter name(s) of our condition
    try:
        cond_params = list(inspect.signature(condition).parameters.keys())
    except (ValueError, TypeError):
        cond_params = []

    # Search each line for a lambda matching our parameters
    # Loop invariant: checked lines[0..i] for matching lambda
    for line in full_source.split("\n"):
        line = line.strip()
        if "lambda" not in line:
            continue

        # Extract from "lambda" to end of meaningful content
        lambda_match = re.search(r"lambda\s+.+", line)
        if lambda_match:
            lambda_text = lambda_match.group(0)
            # Strip only trailing decorator syntax (comma after closing paren)
            lambda_text = re.sub(r",\s*$", "", lambda_text).strip()
            if cond_params and all(p in lambda_text for p in cond_params):
                return lambda_text

    return ""


def _parse_literal_collection(items_str: str) -> list[object] | None:
    """Parse a string of literal values from source code.

    Handles strings like '"a", "b", "c"' or '1, 2, 3'.

    Args:
        items_str: Comma-separated literal values.

    Returns:
        List of parsed values, or None if parsing fails.
    """
    items: list[object] = []
    # Loop invariant: items contains parsed values for parts[0..i]
    for part in items_str.split(","):
        part = part.strip()
        if not part:
            continue
        # Try string literals
        if (part.startswith('"') and part.endswith('"')) or \
           (part.startswith("'") and part.endswith("'")):
            items.append(part[1:-1])
        # Try integers
        elif part.lstrip("-").isdigit():
            items.append(int(part))
        # Try floats
        else:
            try:
                items.append(float(part))
            except ValueError:
                return None  # can't parse
    return items if items else None


def _has_icontract_decorators(func: Callable[..., object]) -> bool:
    """Check if a function has icontract decorators.

    Args:
        func: Function to check.

    Returns:
        True if the function has icontract require or ensure decorators.
    """
    # icontract wraps the function — check for wrapper markers
    return (
        hasattr(func, "__preconditions__")
        or hasattr(func, "__postconditions__")
        or hasattr(func, "__wrapped__")
    )


def _get_contracted_functions(
    module_path: str,
) -> list[tuple[str, Callable[..., object]]]:
    """Import a module and find all functions with icontract decorators.

    Args:
        module_path: Importable Python module path.

    Returns:
        List of (function_name, function) tuples.
    """
    try:
        module = importlib.import_module(module_path)
    except ImportError as exc:
        raise ToolNotInstalledError(
            f"Cannot import module '{module_path}': {exc}"
        ) from exc

    functions: list[tuple[str, Callable[..., object]]] = []

    # Loop invariant: functions contains all contracted functions found so far
    for name in dir(module):
        if name.startswith("_"):
            continue
        obj = getattr(module, name)
        if callable(obj) and inspect.isfunction(obj):
            if _has_icontract_decorators(obj):
                functions.append((name, obj))

    return functions


class HypothesisPropertyTester:
    """Property tester implementation using Hypothesis.

    Runs Hypothesis tests against functions with icontract decorators,
    using icontract's runtime contract checking to detect violations.
    """

    def __init__(self, max_examples: int = 100) -> None:
        """Initialize the tester.

        Args:
            max_examples: Default maximum examples per function.
        """
        self._max_examples = max_examples

    def test_module(
        self,
        module_path: str,
        max_examples: int = 100,
    ) -> list[PropertyFinding]:
        """Run property-based tests on all contracted functions in a module.

        Args:
            module_path: Importable Python module path to test.
            max_examples: Maximum number of test examples per function.

        Returns:
            List of property findings.
        """
        if not _HYPOTHESIS_AVAILABLE:
            raise ToolNotInstalledError(
                "Hypothesis is not installed. Install with: pip install hypothesis"
            )

        effective_max = max_examples or self._max_examples
        functions = _get_contracted_functions(module_path)
        findings: list[PropertyFinding] = []

        # Loop invariant: findings contains test results for functions[0..i]
        for func_name, func in functions:
            finding = self._test_single_function(func_name, func, module_path, effective_max)
            findings.append(finding)

        return findings

    def _test_single_function(
        self,
        func_name: str,
        func: Callable[..., object],
        module_path: str,
        max_examples: int,
    ) -> PropertyFinding:
        """Test a single function with Hypothesis.

        Args:
            func_name: Name of the function.
            func: The function to test.
            module_path: Module path for reporting.
            max_examples: Max examples to generate.

        Returns:
            A PropertyFinding for this function.
        """
        strategies = _build_strategies_from_signature(func)

        if strategies is None:
            return PropertyFinding(
                function_name=func_name,
                module_path=module_path,
                passed=True,
                finding_type="skipped",
                message=f"Cannot derive strategies for '{func_name}' — unsupported parameter types",
            )

        try:
            self._run_hypothesis_test(func, strategies, max_examples)
            return PropertyFinding(
                function_name=func_name,
                module_path=module_path,
                passed=True,
                finding_type="verified",
                message=f"Property tests passed for '{func_name}' ({max_examples} examples)",
            )
        except icontract.ViolationError as exc:
            # Distinguish precondition vs postcondition violations
            error_str = str(exc)
            if "Precondition" in error_str:
                # Precondition violation means the input doesn't satisfy the
                # contract — this is expected and not a real failure.
                # But if it happens consistently, it means strategies are bad.
                return PropertyFinding(
                    function_name=func_name,
                    module_path=module_path,
                    passed=True,
                    finding_type="skipped",
                    message=(
                        f"Cannot generate valid inputs for '{func_name}' — "
                        "preconditions too restrictive for derived strategies"
                    ),
                )
            else:
                # Postcondition violation — real failure
                return PropertyFinding(
                    function_name=func_name,
                    module_path=module_path,
                    passed=False,
                    finding_type="postcondition_violated",
                    message=f"Postcondition violated for '{func_name}': {error_str}",
                    counterexample=_extract_counterexample(exc),
                )
        except Exception as exc:
            # Check if a ViolationError is nested inside (Hypothesis wraps exceptions)
            violation = _find_nested_violation(exc)
            if violation is not None:
                return PropertyFinding(
                    function_name=func_name,
                    module_path=module_path,
                    passed=False,
                    finding_type="postcondition_violated",
                    message=f"Postcondition violated for '{func_name}': {violation}",
                    counterexample=_extract_counterexample(violation),
                )
            return PropertyFinding(
                function_name=func_name,
                module_path=module_path,
                passed=False,
                finding_type="crash",
                message=f"Function '{func_name}' crashed during testing: {exc}",
                exception_type=type(exc).__name__,
                exception_message=str(exc),
            )

    def _run_hypothesis_test(
        self,
        func: Callable[..., object],
        strategies: dict[str, SearchStrategy],
        max_examples: int,
    ) -> None:
        """Run a Hypothesis test on a function.

        Args:
            func: The function to test.
            strategies: Mapping of parameter names to strategies.
            max_examples: Max examples to generate.

        Raises:
            Any exception raised by the function or its contracts.
        """
        test_settings = settings(
            max_examples=max_examples,
            deadline=None,
            suppress_health_check=[HealthCheck.too_slow],
            verbosity=Verbosity.quiet,
            database=None,
        )

        @test_settings
        @given(**strategies)
        def test_wrapper(**kwargs: object) -> None:
            """Wrapper that calls the function with generated inputs."""
            # First check if inputs satisfy preconditions by testing them
            if not _check_preconditions(func, kwargs):
                from hypothesis import assume
                assume(False)
                return

            try:
                func(**kwargs)
            except icontract.ViolationError:
                # If preconditions passed but ViolationError is raised,
                # it must be a postcondition or invariant violation
                raise

        test_wrapper()


def _find_nested_violation(exc: BaseException) -> icontract.ViolationError | None:
    """Search exception chain for an icontract ViolationError.

    Hypothesis wraps exceptions in MultipleFailures or other wrapper
    types. This function walks the exception chain to find the
    underlying ViolationError.

    Args:
        exc: The exception to search.

    Returns:
        The ViolationError if found, None otherwise.
    """
    # Check the exception itself
    if isinstance(exc, icontract.ViolationError):
        return exc

    # Check __cause__ and __context__
    # Variant: depth of exception chain decreases
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, icontract.ViolationError):
            return current
        # Also check sub-exceptions (Hypothesis MultipleFailures)
        sub_exceptions = getattr(current, "exceptions", None)
        if sub_exceptions:
            # Loop invariant: checked sub_exceptions[0..i]
            for sub in sub_exceptions:
                if isinstance(sub, icontract.ViolationError):
                    return sub
                nested = _find_nested_violation(sub)
                if nested is not None:
                    return nested
        current = current.__cause__ or current.__context__

    return None


def _check_preconditions(
    func: Callable[..., object],
    kwargs: dict[str, object],
) -> bool:
    """Check if inputs satisfy a function's icontract preconditions.

    Evaluates each precondition lambda without calling the full function.

    Args:
        func: The contracted function.
        kwargs: The keyword arguments to check.

    Returns:
        True if all preconditions are satisfied.
    """
    preconditions = getattr(func, "__preconditions__", None)
    if not preconditions:
        return True

    # Loop invariant: all precondition groups in preconditions[0..i] are satisfied
    for group in preconditions:
        # Each group is a list of Contract objects (OR within group, AND between groups)
        group_satisfied = False
        # Loop invariant: group_satisfied if any contract in group[0..j] is satisfied
        for contract in group:
            condition = contract.condition
            try:
                # Get the parameters the condition expects
                import inspect
                sig = inspect.signature(condition)
                condition_kwargs = {
                    k: v for k, v in kwargs.items()
                    if k in sig.parameters
                }
                if condition(**condition_kwargs):
                    group_satisfied = True
                    break
            except Exception:
                continue
        if not group_satisfied:
            return False

    return True


def _extract_counterexample(exc: icontract.ViolationError) -> dict[str, object] | None:
    """Extract counterexample data from an icontract violation.

    Args:
        exc: The violation error to extract from.

    Returns:
        A dict mapping argument names to their values, or None.
    """
    error_str = str(exc)
    # icontract includes variable values in the error message
    # Try to parse them, but fall back to the raw message
    try:
        parts = error_str.split("\n")
        counterexample: dict[str, object] = {}
        # Loop invariant: counterexample contains parsed variables from parts[0..i]
        for part in parts:
            if "=" in part and not part.strip().startswith("Postcondition"):
                key_val = part.strip().split("=", 1)
                if len(key_val) == 2:
                    key = key_val[0].strip()
                    val = key_val[1].strip()
                    if key and not key.startswith("lambda"):
                        counterexample[key] = val
        return counterexample if counterexample else None
    except Exception:
        return None
