"""Hypothesis strategy builders for property-based testing.

This module contains all functions that build Hypothesis strategies from
type annotations and icontract conditions.  It is extracted from
``hypothesis_adapter`` to keep both modules under ~1000 lines.

This is an adapter-support module — it handles strategy derivation for
external test execution and is exempt from full contract requirements.
"""

from __future__ import annotations

import ast
import collections.abc
import enum
import inspect
import re
import types
import typing
from typing import Callable, cast

import icontract

from serenecode.contracts.predicates import is_non_empty_string

try:
    from hypothesis import strategies as st
    from hypothesis.strategies import SearchStrategy
    _HYPOTHESIS_AVAILABLE = True
except ImportError:
    _HYPOTHESIS_AVAILABLE = False


# allow-unused: public API used by test infrastructure
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
    if not _HYPOTHESIS_AVAILABLE or annotation is None:
        return None
    if annotation is type(None):
        return st.none()

    known_strategy = _strategy_for_known_annotation(annotation, seen_classes)
    if known_strategy is not None:
        return known_strategy

    basic = _strategy_for_basic_type(annotation)
    if basic is not None:
        return basic

    generic = _strategy_for_generic_type(annotation, seen_classes)
    if generic is not None:
        return generic

    return _strategy_for_class_type(annotation, seen_classes)


def _strategy_for_basic_type(annotation: type | None) -> SearchStrategy | None:
    """Return strategy for basic scalar types, or None."""
    strategy_map: dict[type, SearchStrategy] = {
        int: st.integers(min_value=-1000, max_value=1000),
        float: st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False),
        str: st.text(min_size=0, max_size=100),
        bool: st.booleans(),
        bytes: st.binary(min_size=0, max_size=100),
    }
    if annotation in strategy_map:
        return strategy_map[annotation]
    if annotation is object:
        return st.one_of(
            st.none(), st.booleans(),
            st.integers(min_value=-10, max_value=10),
            st.text(min_size=0, max_size=20),
        )
    return None


def _strategy_for_generic_type(
    annotation: type | object,
    seen_classes: frozenset[type],
) -> SearchStrategy | None:
    """Return strategy for generic types (list[int], Union, etc.), or None."""
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in (typing.Union, types.UnionType):
        strategies = [
            s for arg in args
            if (s := _get_strategy_for_annotation_with_seen(arg, seen_classes)) is not None
        ]
        return st.one_of(*strategies) if strategies else None
    if origin is typing.Literal:
        return st.sampled_from(args)
    if origin is list and args:
        inner = _get_strategy_for_annotation_with_seen(args[0], seen_classes)
        return st.lists(inner, min_size=0, max_size=20) if inner else None
    if origin is set and args:
        inner = _get_strategy_for_annotation_with_seen(args[0], seen_classes)
        return st.sets(inner, max_size=20) if inner else None
    if origin is frozenset and args:
        inner = _get_strategy_for_annotation_with_seen(args[0], seen_classes)
        return st.frozensets(inner, max_size=20) if inner else None
    if origin is tuple and args:
        return _strategy_for_tuple_type(args, seen_classes)
    if origin is dict and args and len(args) == 2:
        key_strat = _get_strategy_for_annotation_with_seen(args[0], seen_classes)
        val_strat = _get_strategy_for_annotation_with_seen(args[1], seen_classes)
        if key_strat is not None and val_strat is not None:
            return st.dictionaries(key_strat, val_strat, max_size=10)
    if origin in (Callable, collections.abc.Callable):
        return st.just(_make_callable_stub(annotation))
    return None


def _strategy_for_tuple_type(
    args: tuple[type, ...],
    seen_classes: frozenset[type],
) -> SearchStrategy | None:
    """Return strategy for tuple types."""
    if len(args) == 2 and args[1] is Ellipsis:
        inner = _get_strategy_for_annotation_with_seen(args[0], seen_classes)
        return st.lists(inner, min_size=0, max_size=8).map(tuple) if inner else None
    inner_strats = []
    # Loop invariant: inner_strats contains strategies for args[0..i]
    for arg in args:
        s = _get_strategy_for_annotation_with_seen(arg, seen_classes)
        if s is None:
            return None
        inner_strats.append(s)
    return st.tuples(*inner_strats)


def _strategy_for_class_type(
    annotation: type | object,
    seen_classes: frozenset[type],
) -> SearchStrategy | None:
    """Return strategy for class types (enums, AST, protocols, user classes)."""
    if not inspect.isclass(annotation):
        return None
    if _is_protocol_class(annotation):
        return _strategy_for_protocol(annotation)
    if issubclass(annotation, enum.Enum):
        members = list(annotation)
        return st.sampled_from(members) if members else None
    if issubclass(annotation, ast.AST):
        return _strategy_for_ast(annotation)
    if annotation in seen_classes:
        return None
    return _strategy_for_class(annotation, seen_classes | frozenset({annotation}))


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
    # Late import to avoid circular dependency with hypothesis_adapter.
    from serenecode.adapters.hypothesis_adapter import _check_preconditions

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

    text_strats = _model_text_strategies()
    detail_strategy = _build_detail_strategy(
        detail_factory, verification_level_values, text_strats,
    )
    function_result_strategy = _build_function_result_strategy(
        function_result_factory, check_status_values, detail_strategy, text_strats,
    )

    if type_name == "Detail":
        return detail_strategy
    if type_name == "FunctionResult":
        return function_result_strategy
    if type_name == "CheckResult":
        return _build_check_result_strategy(
            annotation, module_globals, CanonicalCheckSummary,
            function_result_strategy,
        )
    return None


def _model_text_strategies() -> dict[str, SearchStrategy]:
    """Return reusable text strategies for model types."""
    return {
        "non_empty": st.text(
            alphabet=st.characters(blacklist_categories=("C", "Z")),
            min_size=1, max_size=120,
        ),
        "path": st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_./-",
            min_size=1, max_size=80,
        ),
        "name": st.text(
            alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_",
            min_size=1, max_size=40,
        ),
    }


def _build_detail_strategy(
    detail_factory: Callable[..., object],
    verification_level_values: list,
    text_strats: dict[str, SearchStrategy],
) -> SearchStrategy:
    """Build a Hypothesis strategy for Detail objects."""
    non_empty_text = text_strats["non_empty"]
    name_text = text_strats["name"]
    return st.builds(
        detail_factory,
        level=st.sampled_from(verification_level_values),
        tool=st.sampled_from(["structural", "mypy", "hypothesis", "crosshair", "compositional"]),
        finding_type=st.sampled_from(["verified", "violation", "timeout", "error", "unavailable"]),
        message=non_empty_text,
        counterexample=st.one_of(
            st.none(),
            st.dictionaries(
                name_text,
                st.one_of(st.integers(min_value=-10, max_value=10), st.text(min_size=0, max_size=20), st.booleans()),
                max_size=3,
            ),
        ),
        suggestion=st.one_of(st.none(), non_empty_text.map(lambda value: value[:80])),
    )


def _build_function_result_strategy(
    function_result_factory: Callable[..., object],
    check_status_values: list,
    detail_strategy: SearchStrategy,
    text_strats: dict[str, SearchStrategy],
) -> SearchStrategy:
    """Build a Hypothesis strategy for FunctionResult objects."""
    name_text = text_strats["name"]
    path_text = text_strats["path"]

    @st.composite
    def _function_result_strategy(draw: st.DrawFn) -> object:
        level_requested = draw(st.integers(min_value=1, max_value=5))
        level_achieved = draw(st.integers(min_value=0, max_value=level_requested))
        return function_result_factory(
            function=draw(name_text), file=draw(path_text),
            line=draw(st.integers(min_value=1, max_value=1000)),
            level_requested=level_requested, level_achieved=level_achieved,
            status=draw(st.sampled_from(check_status_values)),
            details=draw(st.lists(detail_strategy, max_size=3).map(tuple)),
        )
    return _function_result_strategy()


def _build_check_result_strategy(
    annotation: type | object,
    module_globals: dict[str, object],
    canonical_summary: type,
    function_result_strategy: SearchStrategy,
) -> SearchStrategy:
    """Build a Hypothesis strategy for CheckResult objects."""
    check_result_factory = cast(Callable[..., object], annotation)
    check_result_hints = typing.get_type_hints(annotation)
    summary_factory = cast(
        Callable[..., object],
        check_result_hints.get("summary", canonical_summary),
    )

    def _build_check_result(results: list[object], duration_seconds: float) -> object:
        level_requested = max((int(getattr(r, "level_requested", 1)) for r in results), default=1)
        level_achieved = min((int(getattr(r, "level_achieved", level_requested)) for r in results), default=level_requested)
        passed_count = failed_count = skipped_count = 0
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
            total_functions=len(results), passed_count=passed_count,
            failed_count=failed_count, skipped_count=skipped_count,
            duration_seconds=duration_seconds,
        )
        passed = failed_count == 0 and skipped_count == 0 and level_achieved == level_requested
        return check_result_factory(
            passed=passed, level_requested=level_requested,
            level_achieved=level_achieved, results=tuple(results), summary=summary,
        )

    return st.builds(
        _build_check_result,
        results=st.lists(function_result_strategy, max_size=6),
        duration_seconds=st.floats(min_value=0.0, max_value=10.0, allow_nan=False, allow_infinity=False),
    )


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
    from serenecode.checker.structural_helpers import IcontractNames

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

            def ensure_directory(self, path: str) -> None:  # allow-unused: implements FileWriter Protocol
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


# Re-export refinement functions from hypothesis_refinement
from serenecode.support.hypothesis_refinement import (  # noqa: E402
    _get_lambda_source,
    _parse_literal_collection,
    _refine_strategies_with_preconditions,
    _try_refine_from_condition,
)

__all_refinement = [  # allow-unused: keep references alive for re-export
    _refine_strategies_with_preconditions,
    _try_refine_from_condition,
    _get_lambda_source,
    _parse_literal_collection,
]
# --- END OF MODULE (refinement functions live in hypothesis_refinement.py) ---
