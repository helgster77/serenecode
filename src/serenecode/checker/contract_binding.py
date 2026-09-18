"""Contract binding validation for the structural checker (Level 1).

Resolves every icontract ``require``/``ensure`` condition parameter against the
decorated function's signature. icontract binds condition parameters by name at
call time, so a condition naming something the signature does not supply is
either silently evaluated against the wrong value or raises ``TypeError`` on the
first call. Neither failure mode is visible at import time, which makes it the
static checker's job.

This module also owns the per-function contract presence checks so that all
contract findings for a single function are produced in one place.

This is a core module — no I/O operations are permitted. Source code is
received as an AST, not read from files.
"""

from __future__ import annotations

import ast

import icontract

from serenecode.config import SerenecodeConfig
from serenecode.contracts.predicates import is_non_empty_string
from serenecode.models import (
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
)

from serenecode.checker.structural_quality import _has_opt_out_comment

from serenecode.checker.structural_helpers import (
    IcontractNames,
    _function_opt_out_lines,
    _decorator_descriptions_are_literals,
    _decorator_has_description,
    _find_tautological_contracts,
    _get_return_annotation_str,
    _iter_checked_functions,
    _non_receiver_parameters,
    get_decorator_name,
    has_decorator,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Names icontract always injects into a condition's namespace.
_ALWAYS_BOUND = frozenset({"_ARGS", "_KWARGS"})

# Names icontract injects into postcondition namespaces only.
_POSTCONDITION_ONLY = frozenset({"result", "OLD"})

# Signature parameter kinds, keyed by how icontract can bind them.
_KIND_POSITIONAL = "positional"
_KIND_VAR_POSITIONAL = "var_positional"
_KIND_VAR_KEYWORD = "var_keyword"
_KIND_UNBOUND = "unbound"

# Opt-out marker that waives the precondition requirement for one function.
# `_has_opt_out_comment` requires a non-empty reason after the colon, so a bare
# marker waives nothing.
_PRECONDITION_OPT_OUT = "no-precondition"

# Comparison operators that make `result is None`-style guards safe on a
# function annotated ``-> None``.
_NONE_COMPARISON_OPS = (ast.Is, ast.IsNot, ast.Eq, ast.NotEq)


# ---------------------------------------------------------------------------
# Binding check
# ---------------------------------------------------------------------------


@icontract.require(lambda tree: isinstance(tree, ast.Module), "tree must be an ast.Module")
@icontract.require(lambda aliases: isinstance(aliases, IcontractNames), "aliases must be resolved icontract names")
@icontract.require(lambda file_path: isinstance(file_path, str), "file_path must be a string")
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def check_contract_bindings(
    tree: ast.Module,
    aliases: IcontractNames,
    file_path: str,
) -> list[FunctionResult]:
    """Check that contract conditions bind to parameters icontract can supply.

    Implements: REQ-037

    Unlike the contract presence checks, this runs on every function that
    carries a contract — private helpers and properties included — because a
    contract that exists and cannot be enforced is a defect regardless of the
    function's visibility.

    Args:
        tree: Parsed AST module.
        aliases: Resolved icontract import names.
        file_path: Path to the source file (for reporting).

    Returns:
        List of FunctionResult for each function carrying a broken contract.
    """
    results: list[FunctionResult] = []

    # Loop invariant: results holds binding findings for checked functions[0..i]
    for node in _iter_checked_functions(tree):
        details = check_function_contract_bindings(node, aliases)
        if not details:
            continue
        results.append(FunctionResult(
            function=node.name,
            file=file_path,
            line=node.lineno,
            level_requested=1,
            level_achieved=0,
            status=CheckStatus.FAILED,
            details=tuple(details),
        ))

    return results


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.require(lambda aliases: isinstance(aliases, IcontractNames), "aliases must be resolved icontract names")
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def check_function_contract_bindings(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: IcontractNames,
) -> list[Detail]:
    """Return findings for contract conditions that cannot bind as written.

    Implements: REQ-038, REQ-039, REQ-040, REQ-041

    Args:
        node: A function definition AST node.
        aliases: Resolved icontract import names.

    Returns:
        List of Detail findings, empty when every condition binds correctly.
    """
    signature_kinds = _signature_parameter_kinds(node)
    returns_none = _returns_none(node)
    details: list[Detail] = []

    # Loop invariant: details holds findings for decorators[0..i]
    for decorator in node.decorator_list:
        condition = _contract_condition(decorator, aliases)
        if condition is None:
            continue
        is_postcondition = get_decorator_name(decorator) in aliases.ensure_names
        details.extend(_check_condition_parameters(
            node.name, condition, signature_kinds, is_postcondition,
        ))
        if is_postcondition and returns_none:
            details.extend(_check_result_on_none(node.name, condition))

    return details


@icontract.require(
    lambda decorator: isinstance(decorator, ast.expr),
    "decorator must be an AST expression",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, ast.Lambda),
    "result must be a lambda or None",
)
def _contract_condition(
    decorator: ast.expr,
    aliases: IcontractNames,
) -> ast.Lambda | None:
    """Return the lambda condition of a require/ensure decorator, if any.

    Conditions that are not lambdas (named predicates, partials) carry no
    parameter names in the AST, so they are out of scope for this check.
    """
    if not isinstance(decorator, ast.Call) or not decorator.args:
        return None
    if get_decorator_name(decorator) not in (aliases.require_names | aliases.ensure_names):
        return None
    condition = decorator.args[0]
    if not isinstance(condition, ast.Lambda):
        return None
    return condition


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dict")
def _signature_parameter_kinds(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, str]:
    """Map each signature parameter name to how icontract can bind it."""
    args = node.args
    kinds: dict[str, str] = {}

    # Loop invariant: kinds holds by-name bindable parameters seen so far
    for arg in list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs):
        kinds[arg.arg] = _KIND_POSITIONAL
    if args.vararg is not None:
        kinds[args.vararg.arg] = _KIND_VAR_POSITIONAL
    if args.kwarg is not None:
        kinds[args.kwarg.arg] = _KIND_VAR_KEYWORD

    return kinds


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _returns_none(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Return True when the function is annotated to return None."""
    annotation = _get_return_annotation_str(node)
    if annotation is None:
        return False
    return annotation.strip("'\" ") == "None"


@icontract.require(
    lambda condition: isinstance(condition, ast.Lambda),
    "condition must be a lambda",
)
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _mandatory_condition_parameters(condition: ast.Lambda) -> list[str]:
    """Return condition parameter names icontract must bind for every call.

    Parameters carrying a default are excluded: icontract leaves them at their
    default when the signature supplies nothing, which is the established
    idiom for capturing constants inside a condition lambda. ``*args`` and
    ``**kwargs`` on the condition itself are also excluded — icontract never
    populates them.
    """
    args = condition.args
    positional = list(args.posonlyargs) + list(args.args)
    defaulted_positional = len(args.defaults)
    mandatory = [
        arg.arg
        for arg in positional[: len(positional) - defaulted_positional]
    ]
    mandatory.extend(
        arg.arg
        for arg, default in zip(args.kwonlyargs, args.kw_defaults)
        if default is None
    )
    return mandatory


@icontract.require(
    lambda condition: isinstance(condition, ast.Lambda),
    "condition must be a lambda",
)
@icontract.require(
    lambda signature_kinds: isinstance(signature_kinds, dict),
    "signature_kinds must be a dict",
)
@icontract.require(
    lambda function_name: is_non_empty_string(function_name),
    "function_name must be a non-empty string",
)
@icontract.require(
    lambda is_postcondition: isinstance(is_postcondition, bool),
    "is_postcondition must be a bool",
)
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _check_condition_parameters(
    function_name: str,
    condition: ast.Lambda,
    signature_kinds: dict[str, str],
    is_postcondition: bool,
) -> list[Detail]:
    """Classify every mandatory condition parameter against the signature."""
    details: list[Detail] = []

    # Loop invariant: details holds findings for mandatory parameters[0..i]
    for name in _mandatory_condition_parameters(condition):
        if name in _ALWAYS_BOUND:
            continue
        if is_postcondition and name in _POSTCONDITION_ONLY:
            continue
        kind = signature_kinds.get(name)
        if kind == _KIND_POSITIONAL:
            continue
        details.append(_binding_detail(
            function_name, name, kind if kind is not None else _KIND_UNBOUND,
        ))

    return details


@icontract.require(
    lambda condition: isinstance(condition, ast.Lambda),
    "condition must be a lambda",
)
@icontract.require(
    lambda function_name: is_non_empty_string(function_name),
    "function_name must be a non-empty string",
)
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _check_result_on_none(
    function_name: str,
    condition: ast.Lambda,
) -> list[Detail]:
    """Flag postconditions that use `result` on a function returning None."""
    references = [
        node
        for node in ast.walk(condition.body)
        if isinstance(node, ast.Name) and node.id == "result"
    ]
    if not references:
        return []
    safe = _none_guarded_result_nodes(condition.body)
    if all(id(node) in safe for node in references):
        return []
    return [Detail(
        level=VerificationLevel.STRUCTURAL,
        tool="structural",
        finding_type="violation",
        message=(
            f"Function '{function_name}' has an @icontract.ensure that uses "
            f"'result' but is annotated '-> None'; icontract binds result to "
            f"None, so the condition raises TypeError when it runs"
        ),
        suggestion=(
            "Give the function a real return type, or restate the "
            "postcondition over the parameters and 'self' instead of 'result'. "
            "Only 'result is None' guards are meaningful on a '-> None' "
            "function."
        ),
    )]


@icontract.require(lambda body: isinstance(body, ast.expr), "body must be an AST expression")
@icontract.ensure(lambda result: isinstance(result, set), "result must be a set")
def _none_guarded_result_nodes(body: ast.expr) -> set[int]:
    """Return ids of `result` nodes used only in a comparison against None."""
    safe: set[int] = set()

    # Loop invariant: safe holds result nodes compared to None in nodes[0..i]
    for node in ast.walk(body):
        if not isinstance(node, ast.Compare):
            continue
        if not all(isinstance(op, _NONE_COMPARISON_OPS) for op in node.ops):
            continue
        operands = [node.left] + list(node.comparators)
        if not any(
            isinstance(operand, ast.Constant) and operand.value is None
            for operand in operands
        ):
            continue
        for operand in operands:
            if isinstance(operand, ast.Name) and operand.id == "result":
                safe.add(id(operand))

    return safe


# ---------------------------------------------------------------------------
# Finding builders
# ---------------------------------------------------------------------------


# What each unbindable condition parameter does at call time, keyed by the
# signature kind it resolved to. `None` covers names absent from the signature.
_BINDING_MESSAGES = {
    _KIND_VAR_POSITIONAL: (
        "has a contract over '*{name}'; icontract never binds the variadic "
        "tuple, so '{name}' receives the first extra positional argument and "
        "the condition raises TypeError when none is passed",
        "Use icontract's '_ARGS' placeholder to constrain variadic arguments "
        "(e.g. lambda _ARGS: all(...) over _ARGS), or replace '*{name}' with "
        "an explicit parameter that accepts a tuple.",
    ),
    _KIND_VAR_KEYWORD: (
        "has a contract over '**{name}'; icontract never binds the keyword "
        "mapping, so the condition raises TypeError on every call",
        "Use icontract's '_KWARGS' placeholder to constrain keyword arguments "
        "(e.g. lambda _KWARGS: ...), or replace '**{name}' with explicit "
        "keyword parameters.",
    ),
    _KIND_UNBOUND: (
        "has a contract over '{name}', which is not a parameter of the "
        "function; icontract raises TypeError when the condition runs",
        "Rename '{name}' to a parameter of '{function}', or move the "
        "decorator to the function it was meant to constrain — a decorator "
        "stack separated from its 'def' by an inserted function produces "
        "exactly this finding.",
    ),
}


@icontract.require(
    lambda function_name: is_non_empty_string(function_name),
    "function_name must be a non-empty string",
)
@icontract.require(lambda name: is_non_empty_string(name), "name must be a non-empty string")
@icontract.require(lambda kind: kind in _BINDING_MESSAGES, "kind must be a known binding kind")
@icontract.ensure(lambda result: isinstance(result, Detail), "result must be a Detail")
def _binding_detail(function_name: str, name: str, kind: str) -> Detail:
    """Build the finding for a condition parameter icontract cannot bind."""
    message_template, suggestion_template = _BINDING_MESSAGES[kind]
    return Detail(
        level=VerificationLevel.STRUCTURAL,
        tool="structural",
        finding_type="violation",
        message=(
            f"Function '{function_name}' "
            + message_template.format(name=name, function=function_name)
        ),
        suggestion=suggestion_template.format(name=name, function=function_name),
    )


# ---------------------------------------------------------------------------
# Contract presence checks
# ---------------------------------------------------------------------------


@icontract.require(lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)), "node must be a function definition")
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _check_single_function_contracts(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    config: SerenecodeConfig,
    aliases: IcontractNames,
    source: str = "",
) -> list[Detail]:
    """Check contracts on a single function node.

    Implements: REQ-050
    """
    details: list[Detail] = []
    params = _non_receiver_parameters(node)
    param_names = [p.arg for p in params]
    has_params = bool(params)
    precondition_waived = _has_opt_out_comment(
        source, _function_opt_out_lines(node), _PRECONDITION_OPT_OUT,
    )

    if has_params and not precondition_waived and not has_decorator(node, aliases.require_names):
        param_list = ", ".join(param_names)
        example_param = param_names[0]
        details.append(Detail(
            level=VerificationLevel.STRUCTURAL, tool="structural",
            finding_type="violation",
            message=f"Function '{node.name}' missing @icontract.require (precondition)",
            suggestion=(
                f"Add precondition for parameters ({param_list}). "
                f"Example: @icontract.require(lambda {example_param}: "
                f"{example_param} is not None, \"{example_param} must not be None\")"
            ),
        ))

    if not has_decorator(node, aliases.ensure_names):
        return_hint = _get_return_annotation_str(node)
        details.append(Detail(
            level=VerificationLevel.STRUCTURAL, tool="structural",
            finding_type="violation",
            message=f"Function '{node.name}' missing @icontract.ensure (postcondition)",
            suggestion=(
                f"Add postcondition. Example: @icontract.ensure(lambda result: "
                f"result is not None, \"result must not be None\")"
                if return_hint is None
                else f"Add postcondition for return type '{return_hint}'. "
                f"Example: @icontract.ensure(lambda result: isinstance(result, {return_hint}), "
                f"\"result must be {return_hint}\")"
            ),
        ))

    if config.contract_requirements.require_description_strings and not details:
        all_names = aliases.require_names | aliases.ensure_names
        if not _decorator_has_description(node, all_names):
            details.append(Detail(
                level=VerificationLevel.STRUCTURAL, tool="structural",
                finding_type="violation",
                message=f"Function '{node.name}' has contract without description string",
                suggestion="Add a description string as second argument to contract decorator",
            ))
        elif not _decorator_descriptions_are_literals(node, all_names):
            details.append(Detail(
                level=VerificationLevel.STRUCTURAL, tool="structural",
                finding_type="violation",
                message=f"Function '{node.name}' has contract description that is not a string literal",
                suggestion="Contract descriptions must be string literals, not variables or expressions",
            ))

    if not details:
        all_contract_names = aliases.require_names | aliases.ensure_names
        tautological = _find_tautological_contracts(node, all_contract_names)
        # Loop invariant: details contains one finding per tautological decorator in [0..i]
        for taut_name in tautological:
            details.append(Detail(
                level=VerificationLevel.STRUCTURAL, tool="structural",
                finding_type="violation",
                message=f"Function '{node.name}' has tautological contract '{taut_name}' (condition is always True)",
                suggestion="Replace with a meaningful condition that constrains behavior",
            ))

    return details
