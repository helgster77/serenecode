"""Hypothesis adapter for property-based testing (Level 3).

This adapter implements the PropertyTester protocol by running
Hypothesis tests against functions that have icontract decorators.
It uses icontract's runtime checking to detect postcondition violations.

Strategy-building helpers live in ``hypothesis_strategies`` and are
re-exported here so that existing imports continue to work.

This is an adapter module — it handles I/O (module importing, test
execution) and is exempt from full contract requirements.
"""

from __future__ import annotations

import inspect
import os
import pathlib
import re
import types
import typing
from typing import Callable

import icontract

from serenecode.adapters.hypothesis_strategies import (
    _build_strategies_from_signature,
)
from serenecode.adapters.module_loader import load_python_module
from serenecode.contracts.predicates import is_non_empty_string, is_positive_int
from serenecode.core.exceptions import ToolNotInstalledError, UnsafeCodeExecutionError
from serenecode.ports.property_tester import PropertyFinding

try:
    from hypothesis import given, settings, HealthCheck, Verbosity
    from hypothesis import strategies as st
    from hypothesis.strategies import SearchStrategy
    _HYPOTHESIS_AVAILABLE = True
except ImportError:
    _HYPOTHESIS_AVAILABLE = False


_TRUST_REQUIRED_MESSAGE = (
    "Level 3 property testing imports and executes project modules. "
    "Re-run with allow_code_execution=True only for trusted code."
)
_PATH_PARAMETER_NAMES = frozenset({
    "base_dir",
    "cwd",
    "directory",
    "dir",
    "file_name",
    "file_path",
    "filename",
    "filepath",
    "figures_dir",
    "module_path",
    "output_dir",
    "path",
    "paths",
    "project_root",
    "results_dir",
    "root_dir",
    "spec_path",
    "working_dir",
    "working_directory",
    "workspace",
    "workspace_dir",
    "workspace_root",
})
_PATH_PARAMETER_SUFFIXES = (
    "_cwd",
    "_dir",
    "_directory",
    "_file",
    "_filename",
    "_path",
    "_paths",
    "_root",
    "_workspace",
)
_PATHLIKE_ANNOTATION_NAMES = frozenset({
    "Path",
    "PathLike",
    "PosixPath",
    "PurePath",
    "PurePosixPath",
    "PureWindowsPath",
    "WindowsPath",
    "os.PathLike",
    "pathlib.Path",
    "pathlib.PathLike",
    "pathlib.PosixPath",
    "pathlib.PurePath",
    "pathlib.PurePosixPath",
    "pathlib.PureWindowsPath",
    "pathlib.WindowsPath",
})
_TEXTUAL_PATH_ANNOTATION_NAMES = frozenset({"bytes", "object", "str"})


@icontract.require(lambda func: callable(func), "func must be callable")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _has_icontract_decorators(func: Callable[..., object]) -> bool:
    """Check if a function has icontract decorators.

    Args:
        func: Function to check.

    Returns:
        True if the function has icontract require or ensure decorators.
    """
    return (
        hasattr(func, "__preconditions__")
        or hasattr(func, "__postconditions__")
    )


@icontract.require(lambda func: callable(func), "func must be callable")
@icontract.ensure(
    lambda result: result is None or is_non_empty_string(result),
    "result must be a non-empty string or None",
)
def _property_exclusion_reason(func: Callable[..., object]) -> str | None:
    """Return the first reason this function should not be property-fuzzed."""
    module_name = getattr(func, "__module__", "")
    if module_name in {"serenecode", "serenecode.cli", "serenecode.init"}:
        return "composition-root code"
    if module_name.startswith("serenecode.adapters"):
        return "adapter code"
    if module_name.startswith("serenecode.mcp"):
        # MCP composition root: tools delegate to existing pipeline functions
        # and run_stdio_server takes over stdin/stdout, so property-fuzzing
        # them produces no signal and breaks the test runner's stdio.
        return "MCP composition-root code"

    try:
        resolved_hints = typing.get_type_hints(func)
    except Exception:
        resolved_hints = {}

    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return "uninspectable signature"

    # Loop invariant: every checked parameter so far is safe for direct property fuzzing.
    for name, parameter in signature.parameters.items():
        if name in ("self", "cls"):
            continue

        annotation = resolved_hints.get(name, parameter.annotation)
        if _is_pathlike_annotation(annotation):
            return f"caller-supplied path-like parameter '{name}'"
        if _looks_like_path_parameter_name(name) and _annotation_may_represent_path_text(annotation):
            return f"caller-supplied path-like parameter '{name}'"

    return None


@icontract.require(lambda name: is_non_empty_string(name), "name must be a non-empty string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _looks_like_path_parameter_name(name: str) -> bool:
    """Heuristically detect parameter names that usually carry filesystem paths."""
    normalized = name.lower()
    return (
        normalized in _PATH_PARAMETER_NAMES
        or any(normalized.endswith(suffix) for suffix in _PATH_PARAMETER_SUFFIXES)
    )


@icontract.require(
    lambda annotation: annotation is inspect.Parameter.empty or isinstance(annotation, object),
    "annotation must be a Python annotation object",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _is_pathlike_annotation(annotation: object) -> bool:
    """Check whether an annotation clearly denotes a path/pathlike value."""
    # Variant: recursive calls peel off a forward ref or nested annotation arg.
    if annotation is inspect.Parameter.empty:
        return False
    if isinstance(annotation, typing.ForwardRef):
        return _is_pathlike_annotation(annotation.__forward_arg__)
    if isinstance(annotation, str):
        dotted_names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", annotation))
        return any(name in _PATHLIKE_ANNOTATION_NAMES for name in dotted_names)

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        return any(_is_pathlike_annotation(arg) for arg in args)
    if origin is not None:
        if _is_pathlike_annotation(origin):
            return True
        return any(_is_pathlike_annotation(arg) for arg in args if arg is not Ellipsis)

    if inspect.isclass(annotation):
        if annotation in {
            pathlib.Path,
            pathlib.PurePath,
            pathlib.PosixPath,
            pathlib.WindowsPath,
            pathlib.PurePosixPath,
            pathlib.PureWindowsPath,
        }:
            return True
        try:
            return issubclass(annotation, (os.PathLike, pathlib.PurePath))
        except TypeError:
            return False

    return False


@icontract.require(
    lambda annotation: annotation is inspect.Parameter.empty or isinstance(annotation, object),
    "annotation must be a Python annotation object",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _annotation_may_represent_path_text(annotation: object) -> bool:
    """Check whether an annotation can carry text/path values from Hypothesis."""
    # Variant: recursive calls peel off a forward ref or nested annotation arg.
    if annotation in {inspect.Parameter.empty, object, str, bytes}:
        return True
    if isinstance(annotation, typing.ForwardRef):
        return _annotation_may_represent_path_text(annotation.__forward_arg__)
    if isinstance(annotation, str):
        dotted_names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", annotation))
        return (
            any(name in _TEXTUAL_PATH_ANNOTATION_NAMES for name in dotted_names)
            or any(name in _PATHLIKE_ANNOTATION_NAMES for name in dotted_names)
        )
    if _is_pathlike_annotation(annotation):
        return True

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        return any(_annotation_may_represent_path_text(arg) for arg in args)
    if origin in (list, set, frozenset):
        return len(args) == 1 and _annotation_may_represent_path_text(args[0])
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return _annotation_may_represent_path_text(args[0])
        return any(_annotation_may_represent_path_text(arg) for arg in args if arg is not Ellipsis)
    if origin is dict:
        return len(args) == 2 and any(_annotation_may_represent_path_text(arg) for arg in args)

    return False


@icontract.require(
    lambda annotation: annotation is inspect.Parameter.empty or isinstance(annotation, object),
    "annotation must be a Python annotation object",
)
@icontract.require(
    lambda globalns: globalns is None or isinstance(globalns, dict),
    "globalns must be a globals dictionary when provided",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _uses_result_model_annotation(
    annotation: object,
    globalns: dict[str, object] | None = None,
) -> bool:
    """Check whether an annotation references Serenecode's result-model graph."""
    # Variant: the remaining annotation nesting decreases on each recursive call into args.
    if annotation is inspect.Parameter.empty:
        return False
    if isinstance(annotation, typing.ForwardRef):
        return _uses_result_model_annotation(annotation.__forward_arg__, globalns)
    if isinstance(annotation, str):
        return _string_annotation_uses_result_model(annotation, globalns)

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin in (typing.Union, types.UnionType):
        return any(_uses_result_model_annotation(arg, globalns) for arg in args)
    if origin is not None:
        return any(_uses_result_model_annotation(arg, globalns) for arg in args if arg is not Ellipsis)

    module_name = getattr(annotation, "__module__", "")
    return module_name == "serenecode.models"


@icontract.require(lambda annotation: isinstance(annotation, str), "annotation must be a string")
@icontract.require(
    lambda globalns: globalns is None or isinstance(globalns, dict),
    "globalns must be a globals dictionary when provided",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _string_annotation_uses_result_model(
    annotation: str,
    globalns: dict[str, object] | None = None,
) -> bool:
    """Check whether a raw string annotation clearly references serenecode.models."""
    result_model_names = _result_model_public_names()

    # Loop invariant: no previously-seen dotted name referenced Serenecode's result models.
    for dotted_name in re.findall(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", annotation):
        if dotted_name.startswith("serenecode.models."):
            if dotted_name.rsplit(".", 1)[-1] in result_model_names:
                return True
            continue

        if "." not in dotted_name:
            if globalns is not None and _is_result_model_object(globalns.get(dotted_name)):
                return True
            continue

        root_name, _, remainder = dotted_name.partition(".")
        if globalns is None or not _is_result_model_module(globalns.get(root_name)):
            continue
        remainder_parts = tuple(part for part in remainder.split(".") if part)
        if remainder_parts and remainder_parts[0] in result_model_names:
            return True

    return False


@icontract.ensure(lambda result: isinstance(result, frozenset), "result must be a frozenset")
def _result_model_public_names() -> frozenset[str]:
    """Return the public names exported from serenecode.models."""
    from serenecode import models as result_models

    return frozenset(
        name
        for name in dir(result_models)
        if not name.startswith("_")
    )


@icontract.require(
    lambda value: isinstance(value, object),
    "value must be a Python object",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _is_result_model_module(value: object) -> bool:
    """Check whether a runtime value is the serenecode.models module."""
    return isinstance(value, types.ModuleType) and value.__name__ == "serenecode.models"


@icontract.require(
    lambda value: isinstance(value, object),
    "value must be a Python object",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _is_result_model_object(value: object) -> bool:
    """Check whether a runtime value comes from serenecode.models."""
    return getattr(value, "__module__", "") == "serenecode.models"


@icontract.require(
    lambda module_path: is_non_empty_string(module_path),
    "module_path must be a non-empty string",
)
@icontract.require(
    lambda search_paths: isinstance(search_paths, tuple),
    "search_paths must be a tuple",
)
@icontract.ensure(lambda result: isinstance(result, tuple) and len(result) == 2, "result must be a 2-tuple")
def _get_contracted_functions(
    module_path: str,
    search_paths: tuple[str, ...] = (),
) -> tuple[list[tuple[str, Callable[..., object]]], list[tuple[str, str]]]:
    """Import a module and find all functions with icontract decorators.

    Args:
        module_path: Importable Python module path.

    Returns:
        A tuple of (testable_functions, excluded_function_names).
    """
    try:
        module = load_python_module(module_path, search_paths)
    except ImportError as exc:
        raise ToolNotInstalledError(
            f"Cannot import module '{module_path}': {exc}"
        ) from exc

    functions: list[tuple[str, Callable[..., object]]] = []
    excluded: list[tuple[str, str]] = []

    # Loop invariant: functions + excluded accounts for all contracted public functions in dir(module)[0..i]
    for name in dir(module):
        if name.startswith("_"):
            continue
        obj = getattr(module, name)
        if callable(obj) and inspect.isfunction(obj):
            if getattr(obj, "__module__", None) != module.__name__:
                continue
            if not _has_icontract_decorators(obj):
                continue
            exclusion_reason = _property_exclusion_reason(obj)
            if exclusion_reason is None:
                functions.append((name, obj))
            else:
                excluded.append((name, exclusion_reason))

    return (functions, excluded)


@icontract.invariant(
    lambda self: is_positive_int(self._max_examples),
    "max_examples must remain positive",
)
class HypothesisPropertyTester:
    """Property tester implementation using Hypothesis.

    Runs Hypothesis tests against functions with icontract decorators,
    using icontract's runtime contract checking to detect violations.
    """

    @icontract.require(
        lambda max_examples: is_positive_int(max_examples),
        "max_examples must be positive",
    )
    @icontract.ensure(lambda result: result is None, "initialization returns None")
    def __init__(
        self,
        max_examples: int = 100,
        allow_code_execution: bool = False,
    ) -> None:
        """Initialize the tester.

        Args:
            max_examples: Default maximum examples per function.
        """
        self._max_examples = max_examples
        self._allow_code_execution = allow_code_execution

    @icontract.require(
        lambda module_path: is_non_empty_string(module_path),
        "module_path must be a non-empty string",
    )
    @icontract.require(
        lambda max_examples: max_examples is None or is_positive_int(max_examples),
        "max_examples must be positive when provided",
    )
    @icontract.require(
        lambda search_paths: isinstance(search_paths, tuple),
        "search_paths must be a tuple",
    )
    @icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
    def test_module(
        self,
        module_path: str,
        max_examples: int | None = None,
        search_paths: tuple[str, ...] = (),
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
        if not self._allow_code_execution:
            raise UnsafeCodeExecutionError(_TRUST_REQUIRED_MESSAGE)

        effective_max = self._max_examples if max_examples is None else max_examples
        functions, excluded = _get_contracted_functions(module_path, search_paths)
        findings: list[PropertyFinding] = []

        # Report excluded functions so they are visible in the output
        # Loop invariant: findings contains exclusion records for excluded[0..i]
        for excluded_name, exclusion_reason in excluded:
            findings.append(PropertyFinding(
                function_name=excluded_name,
                module_path=module_path,
                passed=True,
                finding_type="excluded",
                message=f"Function '{excluded_name}' excluded from property testing ({exclusion_reason})",
            ))

        # Loop invariant: findings contains test results for functions[0..i]
        for func_name, func in functions:
            finding = self._test_single_function(func_name, func, module_path, effective_max)
            findings.append(finding)

        return findings

    @icontract.require(
        lambda func_name: is_non_empty_string(func_name),
        "func_name must be a non-empty string",
    )
    @icontract.require(lambda func: callable(func), "func must be callable")
    @icontract.require(
        lambda module_path: is_non_empty_string(module_path),
        "module_path must be a non-empty string",
    )
    @icontract.require(
        lambda max_examples: is_positive_int(max_examples),
        "max_examples must be positive",
    )
    @icontract.ensure(
        lambda result: isinstance(result, PropertyFinding),
        "result must be a PropertyFinding",
    )
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
                function_name=func_name, module_path=module_path,
                passed=True, finding_type="skipped",
                message=f"Cannot derive strategies for '{func_name}' — unsupported parameter types",
            )

        try:
            self._run_hypothesis_test(func, strategies, max_examples)
            return PropertyFinding(
                function_name=func_name, module_path=module_path,
                passed=True, finding_type="verified",
                message=f"Property tests passed for '{func_name}' ({max_examples} examples)",
            )
        except icontract.ViolationError as exc:
            return _handle_violation(func_name, module_path, exc)
        except Exception as exc:
            return _handle_generic_exception(func_name, module_path, exc)

    @icontract.require(lambda func: callable(func), "func must be callable")
    @icontract.require(lambda strategies: isinstance(strategies, dict), "strategies must be a dict")
    @icontract.require(
        lambda max_examples: is_positive_int(max_examples),
        "max_examples must be positive",
    )
    @icontract.ensure(lambda result: result is None, "test execution returns None")
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
        if not strategies:
            func()
            return

        test_settings = settings(
            max_examples=max_examples,
            deadline=None,
            suppress_health_check=[
                HealthCheck.too_slow,
                HealthCheck.filter_too_much,
            ],
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


@icontract.require(lambda exc: isinstance(exc, BaseException), "exc must be an exception")
def _handle_violation(
    func_name: str,
    module_path: str,
    exc: icontract.ViolationError,
) -> PropertyFinding:
    """Handle an icontract ViolationError from Hypothesis testing."""
    error_str = str(exc)
    if "Precondition" in error_str:
        return PropertyFinding(
            function_name=func_name, module_path=module_path,
            passed=True, finding_type="skipped",
            message=(
                f"Cannot generate valid inputs for '{func_name}' — "
                "preconditions too restrictive for derived strategies. "
                "Built-in sampling for domain models applies to types in "
                "`serenecode.models`, `core.models`, or `*.core.models` "
                "(for example `pkg.core.models`); other module layouts may "
                "need narrower contracts or a custom Hypothesis strategy."
            ),
        )
    return _build_postcondition_finding(func_name, module_path, exc)


def _handle_generic_exception(
    func_name: str,
    module_path: str,
    exc: Exception,
) -> PropertyFinding:
    """Handle a generic exception that may wrap a ViolationError."""
    violation = _find_nested_violation(exc)
    if violation is not None:
        return _build_postcondition_finding(func_name, module_path, violation)
    return PropertyFinding(
        function_name=func_name, module_path=module_path,
        passed=False, finding_type="crash",
        message=f"Function '{func_name}' crashed during testing: {exc}",
        exception_type=type(exc).__name__, exception_message=str(exc),
    )


def _build_postcondition_finding(
    func_name: str,
    module_path: str,
    exc: icontract.ViolationError,
) -> PropertyFinding:
    """Build a finding for a postcondition violation."""
    counterexample = _extract_counterexample(exc)
    error_str = str(exc)
    condition = _extract_violated_condition(error_str)
    inputs_str = (
        ", ".join(f"{k}={v}" for k, v in counterexample.items())
        if counterexample else "unknown"
    )
    message = (
        f"Postcondition violated for '{func_name}': "
        f"condition '{condition}' failed with inputs: {inputs_str}"
        if condition
        else f"Postcondition violated for '{func_name}': {error_str}"
    )
    return PropertyFinding(
        function_name=func_name, module_path=module_path,
        passed=False, finding_type="postcondition_violated",
        message=message, counterexample=counterexample,
    )


@icontract.ensure(
    lambda result: result is None or isinstance(result, icontract.ViolationError),
    "result must be a ViolationError or None",
)
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
    # Loop invariant: seen contains every exception object already traversed in the chain
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


@icontract.require(lambda func: callable(func), "func must be callable")
@icontract.require(lambda kwargs: isinstance(kwargs, dict), "kwargs must be a dict")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
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

    # Loop invariant: all precondition contracts in preconditions[0..i] are satisfied
    for group in preconditions:
        # Loop invariant: every contract in group[0..j] is satisfied
        for contract in group:
            condition = contract.condition
            try:
                # Get the parameters the condition expects
                sig = inspect.signature(condition)
                parameter_names = [
                    name
                    for name in sig.parameters
                    if name not in ("self", "cls", "result")
                ]
                if not parameter_names:
                    continue
                if any(name not in kwargs for name in parameter_names):
                    continue
                condition_kwargs = {
                    name: kwargs[name]
                    for name in parameter_names
                }
                if not condition(**condition_kwargs):
                    return False
            except (TypeError, ValueError, KeyError, AttributeError):
                # Precondition evaluation failed due to type mismatch or
                # missing attribute — treat as "inputs don't satisfy precondition"
                return False
            except Exception:
                # Unexpected error evaluating precondition — treat as unsatisfied
                # rather than crashing the property tester mid-run. This is a
                # safety net; the common cases are caught above.
                return False

    return True


@icontract.require(
    lambda error_str: isinstance(error_str, str),
    "error_str must be a string",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be a string or None",
)
def _extract_violated_condition(error_str: str) -> str | None:
    """Extract the violated condition text from an icontract error message.

    icontract formats the second line as "description: condition_expression:".

    Args:
        error_str: The full icontract error string.

    Returns:
        The condition expression, or None.
    """
    lines = error_str.split("\n")
    if len(lines) < 2:
        return None
    # Second line is typically "description: condition:"
    condition_line = lines[1].strip()
    if ": " in condition_line:
        # Extract everything after the description
        _, _, condition_part = condition_line.partition(": ")
        # Remove trailing colon
        return condition_part.rstrip(":")
    return None


@icontract.require(
    lambda exc: isinstance(exc, icontract.ViolationError),
    "exc must be an icontract violation",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, dict),
    "result must be a dictionary or None",
)
def _extract_counterexample(exc: icontract.ViolationError) -> dict[str, object] | None:
    """Extract counterexample data from an icontract violation.

    icontract formats variable values as "<name> was <value>" lines in the
    error message. This function parses those lines into a dict.

    Args:
        exc: The violation error to extract from.

    Returns:
        A dict mapping argument names to their values, or None.
    """
    error_str = str(exc)
    try:
        lines = error_str.split("\n")
        counterexample: dict[str, object] = {}
        # Loop invariant: counterexample contains parsed "X was Y" bindings from lines[0..i]
        for line in lines:
            stripped = line.strip()
            # icontract uses "<name> was <value>" format for variable bindings
            if " was " in stripped:
                name, _, value = stripped.partition(" was ")
                name = name.strip()
                value = value.strip()
                # Skip the condition description line (contains ":")
                if name and ":" not in name and not name.startswith("File "):
                    counterexample[name] = value
        return counterexample if counterexample else None
    except Exception:
        return None
