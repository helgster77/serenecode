"""Hypothesis adapter for property-based testing (Level 3).

This adapter implements the PropertyTester protocol by running
Hypothesis tests against functions that have icontract decorators.
It uses icontract's runtime checking to detect postcondition violations.

This is an adapter module — it handles I/O (module importing, test
execution) and is exempt from full contract requirements.
"""

from __future__ import annotations

import ast
import collections.abc
import enum
import inspect
import os
import pathlib
import re
import traceback
import types
import typing
from typing import Callable, cast

import icontract

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


@icontract.require(
    lambda annotation: annotation is None or isinstance(annotation, object),
    "annotation must be a Python object or None",
)
@icontract.ensure(
    lambda result: result is None or hasattr(result, "map"),
    "result must be a Hypothesis strategy or None",
)
def _get_strategy_for_annotation(annotation: type | None) -> SearchStrategy | None:
    return _get_strategy_for_annotation_with_seen(annotation, frozenset())


@icontract.require(
    lambda seen_classes: isinstance(seen_classes, frozenset),
    "seen_classes must be a frozenset",
)
@icontract.ensure(
    lambda result: result is None or hasattr(result, "map"),
    "result must be a Hypothesis strategy or None",
)
def _get_strategy_for_annotation_with_seen(
    annotation: type | None,
    seen_classes: frozenset[type],
) -> SearchStrategy | None:
    """Derive a Hypothesis strategy from a type annotation.

    Args:
        annotation: A Python type annotation.
        seen_classes: Classes already visited while deriving nested strategies.

    Returns:
        A Hypothesis strategy, or None if the type is unsupported.
    """
    if not _HYPOTHESIS_AVAILABLE:
        return None

    if annotation is None:
        return None

    if annotation is type(None):
        return st.none()

    known_strategy = _strategy_for_known_annotation(annotation, seen_classes)
    if known_strategy is not None:
        return known_strategy

    # Handle basic types
    strategy_map: dict[type, SearchStrategy] = {
        int: st.integers(min_value=-1000, max_value=1000),
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

    if annotation is object:
        return st.one_of(
            st.none(),
            st.booleans(),
            st.integers(min_value=-10, max_value=10),
            st.text(min_size=0, max_size=20),
        )

    # Handle generic types (list[int], etc.)
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (typing.Union, types.UnionType):
        strategies = [
            strategy
            for arg in args
            if (strategy := _get_strategy_for_annotation_with_seen(arg, seen_classes)) is not None
        ]
        if strategies:
            return st.one_of(*strategies)
        return None

    if origin is typing.Literal:
        return st.sampled_from(args)

    if origin is list and args:
        inner = _get_strategy_for_annotation_with_seen(args[0], seen_classes)
        if inner is not None:
            return st.lists(inner, min_size=0, max_size=20)

    if origin is set and args:
        inner = _get_strategy_for_annotation_with_seen(args[0], seen_classes)
        if inner is not None:
            return st.sets(inner, max_size=20)

    if origin is frozenset and args:
        inner = _get_strategy_for_annotation_with_seen(args[0], seen_classes)
        if inner is not None:
            return st.frozensets(inner, max_size=20)

    if origin is tuple and args:
        if len(args) == 2 and args[1] is Ellipsis:
            inner = _get_strategy_for_annotation_with_seen(args[0], seen_classes)
            if inner is not None:
                return st.lists(inner, min_size=0, max_size=8).map(tuple)

        inner_strats = []
        # Loop invariant: inner_strats contains strategies for args[0..i]
        for arg in args:
            s = _get_strategy_for_annotation_with_seen(arg, seen_classes)
            if s is None:
                return None
            inner_strats.append(s)
        return st.tuples(*inner_strats)

    if origin is dict and args and len(args) == 2:
        key_strat = _get_strategy_for_annotation_with_seen(args[0], seen_classes)
        val_strat = _get_strategy_for_annotation_with_seen(args[1], seen_classes)
        if key_strat is not None and val_strat is not None:
            return st.dictionaries(key_strat, val_strat, max_size=10)

    if origin in (Callable, collections.abc.Callable):
        return st.just(_make_callable_stub(annotation))

    if inspect.isclass(annotation):
        if _is_protocol_class(annotation):
            return _strategy_for_protocol(annotation)
        if issubclass(annotation, enum.Enum):
            members = list(annotation)
            if members:
                return st.sampled_from(members)
            return None
        if issubclass(annotation, ast.AST):
            return _strategy_for_ast(annotation)
        if annotation in seen_classes:
            return None
        return _strategy_for_class(annotation, seen_classes | frozenset({annotation}))

    return None


@icontract.require(lambda func: callable(func), "func must be callable")
@icontract.require(
    lambda seen_classes: isinstance(seen_classes, frozenset),
    "seen_classes must be a frozenset",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, dict),
    "result must be a strategy dictionary or None",
)
def _build_strategies_from_signature(
    func: Callable[..., object],
    seen_classes: frozenset[type] = frozenset(),
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
    annotations: dict[str, object] = {}
    # Loop invariant: strategies contains entries for all processable params[0..i]
    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue

        # Prefer resolved type hints over raw annotations
        annotation = hints.get(name, param.annotation)
        if annotation is inspect.Parameter.empty:
            return None  # can't derive strategy without annotation

        strategy = _get_strategy_for_annotation_with_seen(annotation, seen_classes)
        if strategy is None:
            return None  # unsupported type

        strategies[name] = strategy
        annotations[name] = annotation

    if not strategies:
        return {}

    # Refine strategies using icontract preconditions
    strategies = _refine_strategies_with_preconditions(
        func,
        strategies,
        annotations,
    )

    return strategies


@icontract.require(lambda annotation: inspect.isclass(annotation), "annotation must be a class")
@icontract.require(
    lambda seen_classes: isinstance(seen_classes, frozenset),
    "seen_classes must be a frozenset",
)
@icontract.ensure(
    lambda result: result is None or hasattr(result, "map"),
    "result must be a Hypothesis strategy or None",
)
def _strategy_for_class(
    annotation: type,
    seen_classes: frozenset[type],
) -> SearchStrategy | None:
    """Derive a strategy for a user-defined class from its constructor."""
    init = getattr(annotation, "__init__", None)
    if init is None or init is object.__init__:
        return None

    constructor_strategies = _build_strategies_from_signature(
        init,
        seen_classes=seen_classes,
    )
    if constructor_strategies is None:
        return None

    kwargs_strategy = st.fixed_dictionaries(constructor_strategies)
    kwargs_strategy = kwargs_strategy.filter(
        lambda kwargs: _check_preconditions(init, dict(kwargs))
    )
    kwargs_strategy = kwargs_strategy.filter(
        lambda kwargs: _can_construct_class(annotation, kwargs)
    )

    return kwargs_strategy.map(lambda kwargs: annotation(**kwargs))


@icontract.require(lambda module_name: isinstance(module_name, str), "module_name must be a string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def _is_example_models_module(module_name: str) -> bool:
    """Return True if Hypothesis should use the built-in ``*.core.models`` strategies.

    Besides the standalone layout ``core.models``, the same strategies apply to
    package-qualified paths such as ``myproj.core.models`` so Level 4 does not
    depend on importing models under an exact top-level module name.
    """
    return module_name == "core.models" or module_name.endswith(".core.models")


@icontract.require(
    lambda seen_classes: isinstance(seen_classes, frozenset),
    "seen_classes must be a frozenset",
)
@icontract.ensure(
    lambda result: result is None or hasattr(result, "map"),
    "result must be a Hypothesis strategy or None",
)
def _strategy_for_known_annotation(
    annotation: type | object,
    seen_classes: frozenset[type],
) -> SearchStrategy | None:
    """Return tailored strategies for Serenecode's own domain types."""
    module_name = getattr(annotation, "__module__", "")
    type_name = getattr(annotation, "__name__", "")

    if module_name == "serenecode.config" and type_name == "SerenecodeConfig":
        from serenecode.config import default_config, minimal_config, strict_config

        return st.sampled_from([
            default_config(),
            strict_config(),
            minimal_config(),
        ])

    if module_name == "serenecode.core.pipeline" and type_name == "SourceFile":
        return _strategy_for_source_file()

    if module_name == "serenecode.models":
        return _strategy_for_model_type(annotation, type_name)

    if _is_example_models_module(module_name):
        return _strategy_for_example_model_type(annotation, type_name)

    if module_name == "serenecode.checker.structural" and type_name == "IcontractNames":
        return _strategy_for_icontract_names()

    if module_name == "serenecode.checker.compositional":
        return _strategy_for_compositional_type(type_name, seen_classes)

    return None


@icontract.require(
    lambda annotation: annotation is not None,
    "annotation must be provided",
)
@icontract.require(
    lambda type_name: is_non_empty_string(type_name),
    "type_name must be a non-empty string",
)
@icontract.ensure(
    lambda result: result is None or hasattr(result, "map"),
    "result must be a Hypothesis strategy or None",
)
def _strategy_for_example_model_type(
    annotation: type | object,
    type_name: str,
) -> SearchStrategy | None:
    """Return efficient strategies for Patient/Drug-style models in ``*.core.models``."""
    if type_name == "Patient" and inspect.isclass(annotation):
        return st.builds(
            annotation,
            weight_kg=st.floats(min_value=0.1, max_value=300.0, allow_nan=False, allow_infinity=False),
            age_years=st.floats(min_value=0.0, max_value=150.0, allow_nan=False, allow_infinity=False),
            creatinine_clearance=st.floats(min_value=1.0, max_value=200.0, allow_nan=False, allow_infinity=False),
            current_medications=st.lists(st.text(min_size=1, max_size=20), max_size=5),
        )

    if type_name == "Drug" and inspect.isclass(annotation):
        def _build_drug(
            drug_cls: type,
            drug_id: str,
            dose_per_kg: float,
            concentration_mg_per_ml: float,
            max_single_dose_mg: float,
            extra_daily_mg: float,
            doses_per_day: int,
            contraindicated_with: set[str],
        ) -> object:
            return drug_cls(
                drug_id=drug_id,
                dose_per_kg=dose_per_kg,
                concentration_mg_per_ml=concentration_mg_per_ml,
                max_single_dose_mg=max_single_dose_mg,
                max_daily_dose_mg=max_single_dose_mg + extra_daily_mg,
                doses_per_day=doses_per_day,
                contraindicated_with=contraindicated_with,
            )

        return st.builds(
            _build_drug,
            drug_cls=st.just(annotation),
            drug_id=st.text(min_size=1, max_size=20),
            dose_per_kg=st.floats(min_value=0.001, max_value=100.0, allow_nan=False, allow_infinity=False),
            concentration_mg_per_ml=st.floats(min_value=0.001, max_value=1000.0, allow_nan=False, allow_infinity=False),
            max_single_dose_mg=st.floats(min_value=0.001, max_value=10000.0, allow_nan=False, allow_infinity=False),
            extra_daily_mg=st.floats(min_value=0.0, max_value=40000.0, allow_nan=False, allow_infinity=False),
            doses_per_day=st.integers(min_value=1, max_value=24),
            contraindicated_with=st.sets(st.text(min_size=1, max_size=20), max_size=5),
        )

    return None


@icontract.require(
    lambda annotation: annotation is not None,
    "annotation must be provided",
)
@icontract.require(
    lambda type_name: is_non_empty_string(type_name),
    "type_name must be a non-empty string",
)
@icontract.ensure(
    lambda result: result is None or hasattr(result, "map"),
    "result must be a Hypothesis strategy or None",
)
def _strategy_for_model_type(
    annotation: type | object,
    type_name: str,
) -> SearchStrategy | None:
    """Return strategies for result models that have cross-field invariants."""
    from serenecode.models import (
        CheckStatus as CanonicalCheckStatus,
        CheckSummary as CanonicalCheckSummary,
        Detail as CanonicalDetail,
        FunctionResult as CanonicalFunctionResult,
        VerificationLevel as CanonicalVerificationLevel,
    )

    module_globals: dict[str, object] = {}
    annotation_globals = getattr(getattr(annotation, "to_dict", None), "__globals__", None)
    if isinstance(annotation_globals, dict):
        module_globals = annotation_globals

    check_status_values = list(cast(type[enum.Enum], module_globals.get("CheckStatus", CanonicalCheckStatus)))
    detail_factory = cast(
        Callable[..., object],
        annotation if type_name == "Detail" else module_globals.get("Detail", CanonicalDetail),
    )
    function_result_factory = cast(
        Callable[..., object],
        annotation if type_name == "FunctionResult" else module_globals.get("FunctionResult", CanonicalFunctionResult),
    )
    verification_level_values = list(cast(
        type[enum.Enum],
        module_globals.get("VerificationLevel", CanonicalVerificationLevel),
    ))
    non_empty_text = st.text(
        alphabet=st.characters(blacklist_categories=("C", "Z")),
        min_size=1,
        max_size=120,
    )
    path_text = st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-",
        min_size=1,
        max_size=80,
    )
    name_text = st.text(
        alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_",
        min_size=1,
        max_size=40,
    )

    detail_strategy = st.builds(
        detail_factory,
        level=st.sampled_from(verification_level_values),
        tool=st.sampled_from(["structural", "mypy", "hypothesis", "crosshair", "compositional"]),
        finding_type=st.sampled_from(["verified", "violation", "timeout", "error", "unavailable"]),
        message=non_empty_text,
        counterexample=st.one_of(
            st.none(),
            st.dictionaries(
                name_text,
                st.one_of(
                    st.integers(min_value=-10, max_value=10),
                    st.text(min_size=0, max_size=20),
                    st.booleans(),
                ),
                max_size=3,
            ),
        ),
        suggestion=st.one_of(st.none(), non_empty_text.map(lambda value: value[:80])),
    )

    @st.composite
    def _function_result_strategy(draw: st.DrawFn) -> object:
        """Build valid FunctionResult objects whose achieved level does not exceed the request."""
        level_requested = draw(st.integers(min_value=1, max_value=5))
        level_achieved = draw(st.integers(min_value=0, max_value=level_requested))
        return function_result_factory(
            function=draw(name_text),
            file=draw(path_text),
            line=draw(st.integers(min_value=1, max_value=1000)),
            level_requested=level_requested,
            level_achieved=level_achieved,
            status=draw(st.sampled_from(check_status_values)),
            details=draw(st.lists(detail_strategy, max_size=3).map(tuple)),
        )
    function_result_strategy = _function_result_strategy()

    if type_name == "Detail":
        return detail_strategy

    if type_name == "FunctionResult":
        return function_result_strategy

    if type_name == "CheckResult":
        check_result_factory = cast(Callable[..., object], annotation)
        check_result_hints = typing.get_type_hints(annotation)
        summary_factory = cast(
            Callable[..., object],
            check_result_hints.get("summary", CanonicalCheckSummary),
        )

        def _build_check_result(
            results: list[object],
            duration_seconds: float,
        ) -> object:
            level_requested = max(
                (int(getattr(r, "level_requested", 1)) for r in results),
                default=1,
            )
            level_achieved = min(
                (int(getattr(r, "level_achieved", level_requested)) for r in results),
                default=level_requested,
            )
            passed_count = 0
            failed_count = 0
            skipped_count = 0
            # Loop invariant: counts reflect the statuses in results[0..i].
            for result in results:
                status_value = getattr(getattr(result, "status", None), "value", None)
                if status_value == "passed":
                    passed_count += 1
                elif status_value == "failed":
                    failed_count += 1
                else:
                    skipped_count += 1

            summary = summary_factory(
                total_functions=len(results),
                passed_count=passed_count,
                failed_count=failed_count,
                skipped_count=skipped_count,
                duration_seconds=duration_seconds,
            )
            passed = (
                failed_count == 0
                and skipped_count == 0
                and level_achieved == level_requested
            )
            return check_result_factory(
                passed=passed,
                level_requested=level_requested,
                level_achieved=level_achieved,
                results=tuple(results),
                summary=summary,
            )

        return st.builds(
            _build_check_result,
            results=st.lists(function_result_strategy, max_size=6),
            duration_seconds=st.floats(
                min_value=0.0,
                max_value=10.0,
                allow_nan=False,
                allow_infinity=False,
            ),
        )

    return None


@icontract.ensure(
    lambda result: hasattr(result, "map"),
    "result must be a Hypothesis strategy",
)
def _strategy_for_source_file() -> SearchStrategy:
    """Build valid SourceFile instances for pipeline property tests."""
    from serenecode.core.pipeline import SourceFile

    return st.builds(
        SourceFile,
        file_path=st.text(min_size=1, max_size=80),
        module_path=st.text(min_size=1, max_size=80),
        source=st.text(min_size=0, max_size=200),
        importable_module=st.one_of(
            st.none(),
            st.text(
                alphabet="abcdefghijklmnopqrstuvwxyz._",
                min_size=1,
                max_size=40,
            ).filter(lambda value: not value.startswith(".")),
        ),
        import_search_paths=st.lists(
            st.text(min_size=1, max_size=60),
            max_size=4,
        ).map(tuple),
    )


@icontract.ensure(
    lambda result: hasattr(result, "map"),
    "result must be a Hypothesis strategy",
)
def _strategy_for_icontract_names() -> SearchStrategy:
    """Build valid icontract alias sets for structural helper tests."""
    from serenecode.checker.structural import IcontractNames

    names = st.lists(
        st.sampled_from([
            "require",
            "ensure",
            "invariant",
            "icontract.require",
            "icontract.ensure",
            "icontract.invariant",
        ]),
        unique=True,
        max_size=3,
    ).map(frozenset)

    return st.builds(
        IcontractNames,
        module_alias=st.one_of(st.none(), st.text(min_size=1, max_size=12)),
        require_names=names,
        ensure_names=names,
        invariant_names=names,
    )


@icontract.require(
    lambda type_name: is_non_empty_string(type_name),
    "type_name must be a non-empty string",
)
@icontract.require(
    lambda seen_classes: isinstance(seen_classes, frozenset),
    "seen_classes must be a frozenset",
)
@icontract.ensure(
    lambda result: result is None or hasattr(result, "map"),
    "result must be a Hypothesis strategy or None",
)
def _strategy_for_compositional_type(
    type_name: str,
    seen_classes: frozenset[type],
) -> SearchStrategy | None:
    """Return strategies for compositional-analysis dataclasses."""
    from serenecode.checker.compositional import (
        ClassInfo,
        FunctionInfo,
        MethodSignature,
        ModuleInfo,
        ParameterInfo,
        ProtocolInfo,
    )

    method_signature = st.builds(
        MethodSignature,
        name=st.text(min_size=1, max_size=20),
        parameters=st.lists(st.text(min_size=1, max_size=16), max_size=4).map(tuple),
        has_return_annotation=st.booleans(),
    )
    parameter_info = st.builds(
        ParameterInfo,
        name=st.text(min_size=1, max_size=20),
        annotation=st.one_of(st.none(), st.text(min_size=1, max_size=20)),
    )
    function_info = st.builds(
        FunctionInfo,
        name=st.text(min_size=1, max_size=20),
        line=st.integers(min_value=1, max_value=500),
        is_public=st.booleans(),
        parameters=st.lists(parameter_info, max_size=4).map(tuple),
        return_annotation=st.one_of(st.none(), st.text(min_size=1, max_size=20)),
        has_require=st.booleans(),
        has_ensure=st.booleans(),
        calls=st.lists(st.text(min_size=1, max_size=20), max_size=4).map(tuple),
    )
    class_info = st.builds(
        ClassInfo,
        name=st.text(min_size=1, max_size=20),
        line=st.integers(min_value=1, max_value=500),
        bases=st.lists(st.text(min_size=1, max_size=20), max_size=3).map(tuple),
        methods=st.lists(st.text(min_size=1, max_size=20), max_size=4).map(tuple),
        is_protocol=st.booleans(),
        method_signatures=st.lists(method_signature, max_size=4).map(tuple),
        has_invariant=st.booleans(),
    )
    protocol_info = st.builds(
        ProtocolInfo,
        name=st.text(min_size=1, max_size=20),
        line=st.integers(min_value=1, max_value=500),
        methods=st.lists(method_signature, max_size=4).map(tuple),
    )
    module_info = st.builds(
        ModuleInfo,
        file_path=st.text(min_size=1, max_size=60),
        module_path=st.text(min_size=1, max_size=60),
        imports=st.lists(st.text(min_size=1, max_size=20), max_size=4).map(tuple),
        from_imports=st.lists(
            st.tuples(
                st.text(min_size=1, max_size=20),
                st.text(min_size=1, max_size=20),
            ),
            max_size=4,
        ).map(tuple),
        classes=st.lists(class_info, max_size=3).map(tuple),
        functions=st.lists(st.text(min_size=1, max_size=20), max_size=4).map(tuple),
        protocols=st.lists(protocol_info, max_size=3).map(tuple),
        function_infos=st.lists(function_info, max_size=4).map(tuple),
    )

    strategies = {
        "MethodSignature": method_signature,
        "ParameterInfo": parameter_info,
        "FunctionInfo": function_info,
        "ClassInfo": class_info,
        "ProtocolInfo": protocol_info,
        "ModuleInfo": module_info,
    }
    return strategies.get(type_name)


@icontract.require(lambda annotation: annotation is not None, "annotation must be provided")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _is_protocol_class(annotation: type) -> bool:
    """Check whether an annotation is a typing.Protocol-derived class."""
    return bool(getattr(annotation, "_is_protocol", False))


@icontract.require(lambda annotation: annotation is not None, "annotation must be provided")
@icontract.ensure(
    lambda result: result is None or hasattr(result, "map"),
    "result must be a Hypothesis strategy or None",
)
def _strategy_for_protocol(annotation: type) -> SearchStrategy | None:
    """Build stubs for protocol-typed dependencies used in Serenecode."""
    module_name = getattr(annotation, "__module__", "")
    type_name = getattr(annotation, "__name__", "")

    if module_name == "serenecode.ports.file_system" and type_name == "FileReader":
        class _ReaderStub:
            def read_file(self, path: str) -> str:
                return "def generated() -> int:\n    return 1\n"

            def file_exists(self, path: str) -> bool:
                return False

            def list_python_files(self, directory: str) -> list[str]:
                return []

        return st.just(_ReaderStub())

    if module_name == "serenecode.ports.file_system" and type_name == "FileWriter":
        class _WriterStub:
            def write_file(self, path: str, content: str) -> None:
                return None

            def ensure_directory(self, path: str) -> None:
                return None

        return st.just(_WriterStub())

    if module_name == "serenecode.ports.type_checker" and type_name == "TypeChecker":
        class _TypeCheckerStub:
            def check(
                self,
                file_paths: list[str],
                strict: bool = True,
                search_paths: tuple[str, ...] = (),
            ) -> list[object]:
                return []

        return st.just(_TypeCheckerStub())

    if module_name == "serenecode.ports.property_tester" and type_name == "PropertyTester":
        class _PropertyTesterStub:
            def test_module(
                self,
                module_path: str,
                max_examples: int | None = None,
                search_paths: tuple[str, ...] = (),
            ) -> list[object]:
                return []

        return st.just(_PropertyTesterStub())

    if module_name == "serenecode.ports.symbolic_checker" and type_name == "SymbolicChecker":
        class _SymbolicCheckerStub:
            def verify_module(
                self,
                module_path: str,
                per_condition_timeout: int | None = None,
                per_path_timeout: int | None = None,
                search_paths: tuple[str, ...] = (),
            ) -> list[object]:
                return []

        return st.just(_SymbolicCheckerStub())

    return None


@icontract.require(lambda annotation: annotation is not None, "annotation must be provided")
@icontract.ensure(
    lambda result: result is None or hasattr(result, "map"),
    "result must be a Hypothesis strategy or None",
)
def _strategy_for_ast(annotation: type[ast.AST]) -> SearchStrategy | None:
    """Build simple AST nodes for structural helper property tests."""
    module_samples = [
        ast.parse("import icontract"),
        ast.parse(
            "from icontract import require, ensure\n"
            "@require(lambda x: x > 0, 'positive')\n"
            "@ensure(lambda result: result >= 0, 'non-negative')\n"
            "def square(x: int) -> int:\n"
            "    return x * x\n"
        ),
        ast.parse(
            "@decorator\n"
            "class Demo:\n"
            "    pass\n"
        ),
    ]
    expr_samples = [
        ast.parse("require", mode="eval").body,
        ast.parse("icontract.require", mode="eval").body,
        ast.parse("require(x)", mode="eval").body,
    ]
    stmt_samples: list[ast.AST] = []
    # Loop invariant: stmt_samples contains all statement nodes from module_samples[0..i]
    for module in module_samples:
        stmt_samples.extend(module.body)

    all_samples: list[ast.AST] = []
    all_samples.extend(module_samples)
    all_samples.extend(expr_samples)
    all_samples.extend(stmt_samples)

    matching = [sample for sample in all_samples if isinstance(sample, annotation)]
    if not matching:
        return None
    return st.sampled_from(matching)


@icontract.require(lambda annotation: annotation is not None, "annotation must be provided")
@icontract.ensure(lambda result: callable(result), "result must be callable")
def _make_callable_stub(annotation: object) -> Callable[..., object]:
    """Build a simple callable returning a valid value for the annotation."""
    args = typing.get_args(annotation)
    return_annotation = args[1] if len(args) == 2 else None
    return_value = _sample_value_for_annotation(return_annotation)

    def _stub(*_args: object, **_kwargs: object) -> object:
        return return_value

    return _stub


@icontract.require(
    lambda value: isinstance(value, object),
    "value must be a Python object",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_placeholder_value(value: object) -> bool:
    """Check whether a sampled placeholder value is deterministic and safe."""
    if value is None:
        return True
    if isinstance(value, (bool, int, float, str, bytes, list, tuple, dict, set, frozenset)):
        return True
    return (
        value.__class__.__module__ == "serenecode.models"
        and value.__class__.__name__ == "CheckResult"
    )


@icontract.require(
    lambda annotation: annotation is None or isinstance(annotation, object),
    "annotation must be a Python object or None",
)
@icontract.ensure(
    lambda result: _is_placeholder_value(result),
    "result must be a deterministic placeholder value",
)
def _sample_value_for_annotation(annotation: object) -> object:
    """Return a deterministic placeholder value for a type annotation."""
    # Variant: recursive calls peel off a union branch or container wrapper.
    if annotation in (None, type(None)):
        return None
    if annotation is bool:
        return True
    if annotation is int:
        return 1
    if annotation is float:
        return 1.0
    if annotation is str:
        return "x"
    if annotation is bytes:
        return b"x"

    module_name = getattr(annotation, "__module__", "")
    type_name = getattr(annotation, "__name__", "")
    if module_name == "serenecode.models" and type_name == "CheckResult":
        from serenecode.models import make_check_result

        return make_check_result((), level_requested=1, duration_seconds=0.0)

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (typing.Union, types.UnionType) and args:
        if type(None) in args:
            return None
        return _sample_value_for_annotation(args[0])
    if origin is list:
        return []
    if origin is tuple:
        return ()
    if origin is dict:
        return {}
    if origin is set:
        return set()
    if origin is frozenset:
        return frozenset()

    return None


@icontract.require(lambda annotation: inspect.isclass(annotation), "annotation must be a class")
@icontract.require(lambda kwargs: isinstance(kwargs, dict), "kwargs must be a dict")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _can_construct_class(annotation: type, kwargs: dict[str, object]) -> bool:
    """Check whether a class constructor accepts the given keyword arguments."""
    try:
        annotation(**kwargs)
    except Exception:
        return False
    return True


@icontract.require(lambda func: callable(func), "func must be callable")
@icontract.require(lambda strategies: isinstance(strategies, dict), "strategies must be a dict")
@icontract.require(lambda annotations: isinstance(annotations, dict), "annotations must be a dict")
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dict")
def _refine_strategies_with_preconditions(
    func: Callable[..., object],
    strategies: dict[str, SearchStrategy],
    annotations: dict[str, object],
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
            _try_refine_from_condition(condition, refined, annotations)

    return refined


@icontract.require(lambda condition: callable(condition), "condition must be callable")
@icontract.require(lambda strategies: isinstance(strategies, dict), "strategies must be a dict")
@icontract.require(lambda annotations: isinstance(annotations, dict), "annotations must be a dict")
@icontract.ensure(lambda result: result is None, "refinement happens in place")
def _try_refine_from_condition(
    condition: Callable[..., bool],
    strategies: dict[str, SearchStrategy],
    annotations: dict[str, object],
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

        # Recognize common Serenecode contract predicates and synthesize
        # directly bounded strategies instead of filtering broad primitives.
        if called_name == "is_valid_verification_level":
            strategies[param_name] = st.integers(min_value=1, max_value=6)
            return
        if called_name == "is_non_negative_int":
            strategies[param_name] = st.integers(min_value=0, max_value=1000)
            return
        if called_name == "is_positive_int":
            strategies[param_name] = st.integers(min_value=1, max_value=1000)
            return
        if called_name == "is_non_empty_string":
            strategies[param_name] = st.text(min_size=1, max_size=100).filter(
                lambda value: len(value.strip()) > 0
            )
            return
        if called_name == "is_valid_template_name":
            strategies[param_name] = st.sampled_from(["default", "strict", "minimal"])
            return

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
    annotation = annotations.get(param_name)
    is_numeric = annotation in (int, float)
    is_literal_like = annotation in (str, int, float)

    # Apply bound constraints for integer/float strategies
    if is_numeric and (ge_match or gt_match or le_match or lt_match):
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
            if annotation is float:
                strategies[param_name] = st.floats(
                    min_value=float(min_val) if min_val is not None else -1e6,
                    max_value=float(max_val) if max_val is not None else 1e6,
                    allow_nan=False,
                    allow_infinity=False,
                )
            else:
                strategies[param_name] = st.integers(
                    min_value=min_val if min_val is not None else -1000,
                    max_value=max_val if max_val is not None else 1000,
                )
            return

    if not is_literal_like and not inspect.isclass(annotation):
        return

    # Fallback: apply the condition as a filter on the existing strategy
    try:
        strategies[param_name] = current.filter(condition)
    except Exception:
        pass  # keep the original strategy


@icontract.require(lambda condition: callable(condition), "condition must be callable")
@icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
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


@icontract.require(lambda items_str: isinstance(items_str, str), "items_str must be a string")
@icontract.ensure(
    lambda result: result is None or isinstance(result, list),
    "result must be a list or None",
)
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


@icontract.require(lambda func: callable(func), "func must be callable")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _is_property_friendly_function(func: Callable[..., object]) -> bool:
    """Check whether a function is a good fit for property-based fuzzing."""
    return _property_exclusion_reason(func) is None


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
                        "preconditions too restrictive for derived strategies. "
                        "Built-in sampling for domain models applies to types in "
                        "`serenecode.models`, `core.models`, or `*.core.models` "
                        "(for example `pkg.core.models`); other module layouts may "
                        "need narrower contracts or a custom Hypothesis strategy."
                    ),
                )
            else:
                # Postcondition violation — real failure
                counterexample = _extract_counterexample(exc)
                condition = _extract_violated_condition(error_str)
                inputs_str = (
                    ", ".join(f"{k}={v}" for k, v in counterexample.items())
                    if counterexample
                    else "unknown"
                )
                message = (
                    f"Postcondition violated for '{func_name}': "
                    f"condition '{condition}' failed with inputs: {inputs_str}"
                    if condition
                    else f"Postcondition violated for '{func_name}': {error_str}"
                )
                return PropertyFinding(
                    function_name=func_name,
                    module_path=module_path,
                    passed=False,
                    finding_type="postcondition_violated",
                    message=message,
                    counterexample=counterexample,
                )
        except Exception as exc:
            # Check if a ViolationError is nested inside (Hypothesis wraps exceptions)
            violation = _find_nested_violation(exc)
            if violation is not None:
                counterexample = _extract_counterexample(violation)
                violation_str = str(violation)
                condition = _extract_violated_condition(violation_str)
                inputs_str = (
                    ", ".join(f"{k}={v}" for k, v in counterexample.items())
                    if counterexample
                    else "unknown"
                )
                message = (
                    f"Postcondition violated for '{func_name}': "
                    f"condition '{condition}' failed with inputs: {inputs_str}"
                    if condition
                    else f"Postcondition violated for '{func_name}': {violation_str}"
                )
                return PropertyFinding(
                    function_name=func_name,
                    module_path=module_path,
                    passed=False,
                    finding_type="postcondition_violated",
                    message=message,
                    counterexample=counterexample,
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
                import inspect
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
