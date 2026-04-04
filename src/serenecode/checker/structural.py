"""Structural checker for Serenecode conventions (Level 1).

This module implements Level 1 verification: AST-based analysis that validates
Python source code follows the conventions defined in SERENECODE.md. It checks
for the presence of contracts, type annotations, and architectural compliance.

This is a core module — no I/O operations are permitted. Source code is received
as strings, not read from files.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
import time
from dataclasses import dataclass

import icontract

from serenecode.config import SerenecodeConfig, is_core_module, is_exempt_module
from serenecode.contracts.predicates import is_non_empty_string, is_pascal_case, is_snake_case
from serenecode.models import (
    CheckResult,
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)


# ---------------------------------------------------------------------------
# Import alias resolution
# ---------------------------------------------------------------------------


@icontract.invariant(
    lambda self: isinstance(self.require_names, frozenset) and isinstance(self.ensure_names, frozenset),
    "Decorator name sets must be frozensets",
)
@dataclass(frozen=True)
class IcontractNames:
    """Resolved icontract decorator names for a module.

    Tracks how icontract is imported so the checker can recognize
    decorators regardless of import style.
    """

    module_alias: str | None  # e.g. "icontract" or "ic"
    require_names: frozenset[str]  # e.g. {"require"} or {"ic.require"}
    ensure_names: frozenset[str]
    invariant_names: frozenset[str]


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, IcontractNames),
    "result must be an IcontractNames",
)
def resolve_icontract_aliases(tree: ast.Module) -> IcontractNames:
    """Scan imports to determine how icontract decorators are referenced.

    Handles:
    - import icontract
    - import icontract as ic
    - from icontract import require, ensure, invariant

    Args:
        tree: The parsed AST module.

    Returns:
        An IcontractNames with all recognized decorator names.
    """
    module_alias: str | None = None
    require_names: set[str] = set()
    ensure_names: set[str] = set()
    invariant_names: set[str] = set()

    # Loop invariant: sets contain all icontract names found in nodes[0..i]
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            # Loop invariant: aliases processed for all names in node.names[0..j]
            for alias in node.names:
                if alias.name == "icontract":
                    actual_alias = alias.asname if alias.asname else "icontract"
                    module_alias = actual_alias
                    require_names.add(f"{actual_alias}.require")
                    ensure_names.add(f"{actual_alias}.ensure")
                    invariant_names.add(f"{actual_alias}.invariant")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "icontract":
                # Loop invariant: icontract names resolved for node.names[0..j]
                for alias in node.names:
                    actual_name = alias.asname if alias.asname else alias.name
                    if alias.name == "require":
                        require_names.add(actual_name)
                    elif alias.name == "ensure":
                        ensure_names.add(actual_name)
                    elif alias.name == "invariant":
                        invariant_names.add(actual_name)

    return IcontractNames(
        module_alias=module_alias,
        require_names=frozenset(require_names),
        ensure_names=frozenset(ensure_names),
        invariant_names=frozenset(invariant_names),
    )


# ---------------------------------------------------------------------------
# Decorator matching helpers
# ---------------------------------------------------------------------------


@icontract.require(
    lambda decorator: isinstance(decorator, ast.AST),
    "decorator must be an AST node",
)
@icontract.ensure(
    lambda result: isinstance(result, str),
    "result must be a string",
)
def get_decorator_name(decorator: ast.expr) -> str:
    """Extract the full dotted name of a decorator.

    Args:
        decorator: An AST decorator expression.

    Returns:
        The decorator name string (e.g. "icontract.require" or "require").
    """
    # Variant: depth decreases as decorator nesting decreases
    if isinstance(decorator, ast.Call) and hasattr(decorator, "func"):
        return get_decorator_name(decorator.func)
    elif isinstance(decorator, ast.Attribute) and hasattr(decorator, "value") and hasattr(decorator, "attr"):
        value_name = get_decorator_name(decorator.value)
        attr = decorator.attr
        if not isinstance(attr, str):
            return ""
        return f"{value_name}.{attr}" if value_name else attr
    elif isinstance(decorator, ast.Name) and hasattr(decorator, "id"):
        node_id = decorator.id
        return node_id if isinstance(node_id, str) else ""
    return ""


@icontract.require(
    lambda names: isinstance(names, frozenset),
    "names must be a frozenset",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def has_decorator(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    names: frozenset[str],
) -> bool:
    """Check if a node has any decorator matching the given names.

    Args:
        node: An AST node with a decorator_list.
        names: Set of decorator name strings to match.

    Returns:
        True if any decorator matches.
    """
    # Loop invariant: result is True if any decorator in decorators[0..i] matches names
    for dec in node.decorator_list:
        if get_decorator_name(dec) in names:
            return True
    return False


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)),
    "node must be a function or class definition",
)
@icontract.require(
    lambda names: isinstance(names, frozenset),
    "names must be a frozenset",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _decorator_has_description(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    names: frozenset[str],
) -> bool:
    """Check if decorators matching names include a description string.

    icontract decorators should have at least 2 positional args:
    the lambda condition and a description string.

    Args:
        node: An AST node with a decorator_list.
        names: Set of decorator name strings to match.

    Returns:
        True if all matching decorators have description strings.
    """
    # Loop invariant: all_have_desc is True if all matched decorators in [0..i] have descriptions
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and get_decorator_name(dec) in names:
            if len(dec.args) < 2:
                # Check for description= keyword argument
                has_desc_kwarg = False
                # Loop invariant: has_desc_kwarg is True if any keyword in [0..j] is "description"
                for kw in dec.keywords:
                    if kw.arg == "description":
                        has_desc_kwarg = True
                        break
                if not has_desc_kwarg:
                    return False
    return True


@icontract.require(
    lambda node: isinstance(node, ast.expr),
    "node must be an AST expression",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_tautological_lambda(node: ast.expr) -> bool:
    """Check if an AST expression is a lambda that always returns True.

    Detects patterns like ``lambda result: True``, ``lambda self: True``,
    and ``lambda: True`` which provide no verification value.

    Args:
        node: An AST expression node (expected to be a Lambda).

    Returns:
        True if the expression is a tautological lambda.
    """
    if not isinstance(node, ast.Lambda):
        return False
    body = node.body
    # lambda ...: True
    if isinstance(body, ast.Constant) and body.value is True:
        return True
    # lambda ...: True and True, lambda ...: True or True (unlikely but possible)
    if isinstance(body, ast.BoolOp):
        # Loop invariant: all values checked so far in [0..i] are True constants
        for val in body.values:
            if not (isinstance(val, ast.Constant) and val.value is True):
                return False
        return True
    return False


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)),
    "node must be a function or class definition",
)
@icontract.require(
    lambda names: isinstance(names, frozenset),
    "names must be a frozenset",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list of decorator detail strings",
)
def _find_tautological_contracts(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    names: frozenset[str],
) -> list[str]:
    """Find contract decorators with tautological conditions.

    Returns a list of decorator names whose condition is always True,
    e.g. ``@icontract.ensure(lambda result: True, ...)``.

    Args:
        node: An AST node with a decorator_list.
        names: Set of decorator name strings to match.

    Returns:
        List of decorator name strings that have tautological conditions.
    """
    tautological: list[str] = []
    # Loop invariant: tautological contains names of tautological decorators in [0..i]
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and dec.args:
            dec_name = get_decorator_name(dec)
            if dec_name in names and _is_tautological_lambda(dec.args[0]):
                tautological.append(dec_name)
    return tautological


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)),
    "node must be a function or class definition",
)
@icontract.require(
    lambda names: isinstance(names, frozenset),
    "names must be a frozenset",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _decorator_descriptions_are_literals(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    names: frozenset[str],
) -> bool:
    """Check that all contract description arguments are string literals.

    A description passed as a variable (not a string constant) bypasses
    the intent of the convention and may be empty or misleading at runtime.

    Args:
        node: An AST node with a decorator_list.
        names: Set of decorator name strings to match.

    Returns:
        True if every matched decorator's description is a string literal.
    """
    # Loop invariant: all matched decorators in [0..i] have literal string descriptions
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call) and get_decorator_name(dec) in names:
            # Check positional description (second arg)
            if len(dec.args) >= 2:
                desc_arg = dec.args[1]
                if not (isinstance(desc_arg, ast.Constant) and isinstance(desc_arg.value, str)):
                    return False
            else:
                # Check description= keyword argument
                # Loop invariant: checked keywords [0..j] for "description"
                for kw in dec.keywords:
                    if kw.arg == "description":
                        if not (isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str)):
                            return False
                        break
    return True


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def _non_receiver_parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[ast.arg]:
    """Return all non-self/cls parameters from a function signature."""
    args = node.args
    params = list(args.posonlyargs) + list(args.args)
    if params and params[0].arg in ("self", "cls"):
        params = params[1:]
    params.extend(args.kwonlyargs)
    if args.vararg is not None:
        params.append(args.vararg)
    if args.kwarg is not None:
        params.append(args.kwarg)
    return params


@icontract.require(
    lambda node: isinstance(node, ast.ClassDef),
    "node must be a class definition",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def _extract_init_fields(node: ast.ClassDef) -> list[str]:
    """Extract field names assigned in __init__ (self.x = ...) or class-level annotations."""
    fields: list[str] = []
    # Check class-level annotated fields (dataclass-style)
    # Loop invariant: fields contains annotated names from body[0..i]
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            fields.append(item.target.id)
    if fields:
        return fields
    # Check __init__ for self.x = ... assignments
    # Loop invariant: checked body items [0..i] for __init__
    for item in node.body:
        if isinstance(item, ast.FunctionDef) and item.name == "__init__":
            # Loop invariant: fields contains self.attr names from init body[0..j]
            for stmt in ast.walk(item):
                if (
                    isinstance(stmt, ast.Assign)
                    and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Attribute)
                    and isinstance(stmt.targets[0].value, ast.Name)
                    and stmt.targets[0].value.id == "self"
                ):
                    fields.append(stmt.targets[0].attr)
            break
    return fields


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be None or a string",
)
def _get_return_annotation_str(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str | None:
    """Extract the return type annotation as a string, or None if absent."""
    if node.returns is None:
        return None
    return ast.unparse(node.returns)


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _has_meaningful_params(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function has parameters beyond self/cls.

    Functions with no input parameters have no preconditions to check.

    Args:
        node: A function definition AST node.

    Returns:
        True if the function has at least one non-self/cls parameter.
    """
    return bool(_non_receiver_parameters(node))


@icontract.require(
    lambda name: is_non_empty_string(name),
    "name must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_public_function(name: str) -> bool:
    """Check if a function name indicates a public function.

    Args:
        name: Function name.

    Returns:
        True if the function is public (not private, not dunder except __init__).
    """
    if name.startswith("_") and not name.startswith("__"):
        return False
    if name.startswith("__") and name.endswith("__") and name != "__init__":
        return False
    return True


@icontract.require(
    lambda node: isinstance(node, ast.ClassDef),
    "node must be a class definition",
)
@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _has_no_invariant_comment(node: ast.ClassDef, source: str) -> bool:
    """Check if the class is preceded by a '# no-invariant:' comment.

    This allows explicitly documented stateless classes to opt out of
    the invariant requirement.

    Args:
        node: A class definition AST node.
        source: The full module source code.

    Returns:
        True if a '# no-invariant:' comment is found on the line before the class.
    """
    if not source:
        return False
    lines = source.splitlines()
    class_line_index = node.lineno - 1
    # Check the line immediately before the class definition
    # Loop invariant: checking lines above the class for no-invariant comment
    for offset in range(1, min(6, class_line_index + 1)):
        prev_line = lines[class_line_index - offset].strip()
        if prev_line.startswith("# no-invariant:"):
            return True
        if prev_line.startswith("#"):
            continue
        # Stop at non-comment, non-decorator lines
        if not prev_line.startswith("@"):
            break
    return False


@icontract.require(
    lambda name: is_non_empty_string(name),
    "name must be a non-empty string",
)
@icontract.require(
    lambda config: isinstance(config, SerenecodeConfig),
    "config must be a SerenecodeConfig",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _should_check_function_contracts(
    name: str,
    config: SerenecodeConfig,
) -> bool:
    """Check whether contract requirements apply to a function name."""
    if config.contract_requirements.require_on_private:
        return not (name.startswith("__") and name.endswith("__") and name != "__init__")
    return _is_public_function(name)


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _has_property_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function is decorated with @property.

    @property methods are incompatible with icontract decorators
    due to decorator ordering constraints.

    Args:
        node: A function definition AST node.

    Returns:
        True if the function has a @property decorator.
    """
    # Loop invariant: checked decorators[0..i] for property name
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "property":
            return True
    return False


@icontract.require(
    lambda node: isinstance(node, ast.ClassDef),
    "node must be a class definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_enum_class(node: ast.ClassDef) -> bool:
    """Check if a class inherits from Enum or IntEnum.

    Enum classes use metaclasses incompatible with icontract invariants.

    Args:
        node: A class definition AST node.

    Returns:
        True if the class inherits from Enum, IntEnum, or similar.
    """
    _ENUM_BASES = {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}
    # Loop invariant: checked bases[0..i] for enum names
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id in _ENUM_BASES:
            return True
        if isinstance(base, ast.Attribute) and base.attr in _ENUM_BASES:
            return True
    return False


@icontract.require(
    lambda node: isinstance(node, ast.ClassDef),
    "node must be a class definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_exception_class(node: ast.ClassDef) -> bool:
    """Check if a class participates in an exception hierarchy."""
    # Loop invariant: checked bases[0..i] for exception-like base classes
    for base in node.bases:
        base_name = ""
        if isinstance(base, ast.Name):
            base_name = base.id
        elif isinstance(base, ast.Attribute):
            base_name = base.attr

        if base_name in {"Exception", "BaseException"}:
            return True
        if base_name.endswith(("Error", "Exception")):
            return True
    return False


@icontract.require(
    lambda node: isinstance(node, ast.ClassDef),
    "node must be a class definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_protocol_class(node: ast.ClassDef) -> bool:
    """Check if a class inherits from Protocol.

    Protocol classes are abstract interfaces — icontract invariants on
    Protocols are not inherited by implementors and would only verify
    the Protocol itself (which is never instantiated).

    Args:
        node: A class definition AST node.

    Returns:
        True if the class inherits from Protocol.
    """
    # Loop invariant: checked bases[0..i] for Protocol name
    for base in node.bases:
        if isinstance(base, ast.Name) and base.id == "Protocol":
            return True
        if isinstance(base, ast.Attribute) and base.attr == "Protocol":
            return True
    return False


@icontract.require(
    lambda name: is_non_empty_string(name),
    "name must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_public_class(name: str) -> bool:
    """Check if a class name indicates a public class.

    Args:
        name: Class name.

    Returns:
        True if the class name doesn't start with underscore.
    """
    return not name.startswith("_")


@icontract.require(
    lambda name: is_non_empty_string(name),
    "name must be a non-empty string",
)
@icontract.require(
    lambda config: isinstance(config, SerenecodeConfig),
    "config must be a SerenecodeConfig",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _should_check_class_invariant(
    name: str,
    config: SerenecodeConfig,
) -> bool:
    """Check whether invariant requirements apply to a class name."""
    if config.contract_requirements.require_on_private:
        return True
    return _is_public_class(name)


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def _iter_checked_functions(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    """Return module-level functions and class methods to check.

    Local closures defined inside function bodies are implementation details,
    so structural contract/docstring rules apply to top-level functions and
    methods rather than nested helper closures.
    """
    functions: list[ast.FunctionDef | ast.AsyncFunctionDef] = []

    # Loop invariant: functions contains checkable defs from top-level nodes[0..i]
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(node)
        elif isinstance(node, ast.ClassDef):
            # Loop invariant: functions contains methods from class body[0..j]
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(child)

    return functions


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def _iter_checked_classes(tree: ast.Module) -> list[ast.ClassDef]:
    """Return the top-level classes that participate in structural checks."""
    classes: list[ast.ClassDef] = []

    # Loop invariant: classes contains top-level class defs from nodes[0..i]
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            classes.append(node)

    return classes


# ---------------------------------------------------------------------------
# Individual check functions
# ---------------------------------------------------------------------------


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_contracts(
    tree: ast.Module,
    config: SerenecodeConfig,
    aliases: IcontractNames,
    file_path: str,
) -> list[FunctionResult]:
    """Check that public functions have icontract require/ensure decorators.

    Args:
        tree: Parsed AST module.
        config: Active configuration.
        aliases: Resolved icontract import names.
        file_path: Path to the source file (for reporting).

    Returns:
        List of FunctionResult for each function checked.
    """
    results: list[FunctionResult] = []

    checkable_functions = _iter_checked_functions(tree)
    # Loop invariant: results contains check outcomes for checkable_functions[0..i]
    for node in checkable_functions:
        if not _should_check_function_contracts(node.name, config):
            continue

        # Skip @property-decorated methods (incompatible with icontract decorators)
        if _has_property_decorator(node):
            continue

        details: list[Detail] = []

        # Skip require check for functions with no meaningful parameters
        # (zero params after excluding self/cls)
        params = _non_receiver_parameters(node)
        param_names = [p.arg for p in params]
        has_params = bool(params)
        if has_params and not has_decorator(node, aliases.require_names):
            param_list = ", ".join(param_names)
            example_param = param_names[0]
            details.append(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
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
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
                finding_type="violation",
                message=f"Function '{node.name}' missing @icontract.ensure (postcondition)",
                suggestion=(
                    f"Add postcondition. "
                    f"Example: @icontract.ensure(lambda result: "
                    f"result is not None, \"result must not be None\")"
                    if return_hint is None
                    else f"Add postcondition for return type '{return_hint}'. "
                    f"Example: @icontract.ensure(lambda result: "
                    f"isinstance(result, {return_hint}), "
                    f"\"result must be {return_hint}\")"
                ),
            ))

        if (
            config.contract_requirements.require_description_strings
            and not details  # only check descriptions if decorators present
        ):
            all_names = aliases.require_names | aliases.ensure_names
            if not _decorator_has_description(node, all_names):
                details.append(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="violation",
                    message=f"Function '{node.name}' has contract without description string",
                    suggestion="Add a description string as second argument to contract decorator",
                ))
            elif not _decorator_descriptions_are_literals(node, all_names):
                details.append(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="violation",
                    message=f"Function '{node.name}' has contract description that is not a string literal",
                    suggestion="Contract descriptions must be string literals, not variables or expressions",
                ))

        # Tautological contract check (always runs when decorators are present)
        if not details:
            all_contract_names = aliases.require_names | aliases.ensure_names
            tautological = _find_tautological_contracts(node, all_contract_names)
            # Loop invariant: details contains one finding per tautological decorator in [0..i]
            for taut_name in tautological:
                details.append(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="violation",
                    message=f"Function '{node.name}' has tautological contract '{taut_name}' (condition is always True)",
                    suggestion="Replace with a meaningful condition that constrains behavior",
                ))

        status = CheckStatus.PASSED if not details else CheckStatus.FAILED
        results.append(FunctionResult(
            function=node.name,
            file=file_path,
            line=node.lineno,
            level_requested=1,
            level_achieved=1 if not details else 0,
            status=status,
            details=tuple(details),
        ))

    return results


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_class_invariants(
    tree: ast.Module,
    config: SerenecodeConfig,
    aliases: IcontractNames,
    file_path: str,
    source: str = "",
) -> list[FunctionResult]:
    """Check that classes have @icontract.invariant decorators.

    Args:
        tree: Parsed AST module.
        config: Active configuration.
        aliases: Resolved icontract import names.
        file_path: Path to the source file.
        source: Original source code for comment checking.

    Returns:
        List of FunctionResult for each class checked.
    """
    if not config.contract_requirements.require_on_classes:
        return []

    results: list[FunctionResult] = []

    checkable_classes = _iter_checked_classes(tree)
    # Loop invariant: results contains check outcomes for checkable_classes[0..i]
    for node in checkable_classes:
        if not _should_check_class_invariant(node.name, config):
            continue

        # Skip Enum/exception/Protocol classes because icontract invariants do
        # not compose safely with their runtime mechanics. Protocol invariants
        # are never inherited by implementors and would only verify nothing.
        # Skip classes with a "# no-invariant:" comment documenting why they
        # have no meaningful state to constrain.
        if _is_enum_class(node) or _is_exception_class(node) or _is_protocol_class(node):
            continue
        if _has_no_invariant_comment(node, source):
            continue

        details: list[Detail] = []

        if not has_decorator(node, aliases.invariant_names):
            fields = _extract_init_fields(node)
            if fields:
                field_list = ", ".join(f"self.{f}" for f in fields)
                example_field = fields[0]
                suggestion = (
                    f"Add invariant constraining instance state ({field_list}). "
                    f"Example: @icontract.invariant(lambda self: "
                    f"self.{example_field} is not None, "
                    f"\"{example_field} must not be None\")"
                )
            else:
                suggestion = (
                    "Add @icontract.invariant(lambda self: ..., 'description') "
                    "or add '# no-invariant: <reason>' if the class is stateless"
                )
            details.append(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
                finding_type="violation",
                message=f"Class '{node.name}' missing @icontract.invariant",
                suggestion=suggestion,
            ))
        else:
            # Check for tautological invariants (e.g. lambda self: True)
            tautological = _find_tautological_contracts(node, aliases.invariant_names)
            # Loop invariant: details contains one finding per tautological invariant in [0..i]
            for taut_name in tautological:
                details.append(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="violation",
                    message=f"Class '{node.name}' has tautological invariant (condition is always True)",
                    suggestion="Replace with a meaningful invariant that constrains instance state",
                ))

        status = CheckStatus.PASSED if not details else CheckStatus.FAILED
        results.append(FunctionResult(
            function=node.name,
            file=file_path,
            line=node.lineno,
            level_requested=1,
            level_achieved=1 if not details else 0,
            status=status,
            details=tuple(details),
        ))

    return results


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_type_annotations(
    tree: ast.Module,
    config: SerenecodeConfig,
    file_path: str,
) -> list[FunctionResult]:
    """Check that all function signatures have complete type annotations.

    Args:
        tree: Parsed AST module.
        config: Active configuration.
        file_path: Path to the source file.

    Returns:
        List of FunctionResult for functions with missing annotations.
    """
    if not config.type_requirements.require_annotations:
        return []

    results: list[FunctionResult] = []

    checkable_functions = _iter_checked_functions(tree)
    # Loop invariant: results contains annotation findings for checkable_functions[0..i]
    for node in checkable_functions:
        details: list[Detail] = []
        args = node.args
        params_to_check = _non_receiver_parameters(node)

        # Loop invariant: details contains missing annotations for params[0..j]
        for arg in params_to_check:
            if arg.annotation is None:
                details.append(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="violation",
                    message=f"Parameter '{arg.arg}' in '{node.name}' missing type annotation",
                    suggestion=f"Add type annotation: {arg.arg}: <type>",
                ))

        # Check return type
        if node.returns is None:
            details.append(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
                finding_type="violation",
                message=f"Function '{node.name}' missing return type annotation",
                suggestion="Add return type: def func(...) -> <type>:",
            ))

        if details:
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
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_no_any_in_core(
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Check that core modules don't use Any type.

    Args:
        tree: Parsed AST module.
        config: Active configuration.
        module_path: Module path for core detection.
        file_path: Path to the source file.

    Returns:
        List of FunctionResult for Any usage violations.
    """
    if not config.type_requirements.forbid_any_in_core:
        return []

    if not is_core_module(module_path, config):
        return []

    results: list[FunctionResult] = []

    # Loop invariant: results contains Any-usage findings for nodes[0..i]
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "Any":
            results.append(FunctionResult(
                function="<module>",
                file=file_path,
                line=node.lineno,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="violation",
                    message=f"Use of 'Any' type at line {node.lineno} in core module",
                    suggestion="Replace 'Any' with a specific type, Union, or Protocol",
                ),),
            ))

    return results


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_imports(
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Check that core modules don't import forbidden I/O libraries.

    Args:
        tree: Parsed AST module.
        config: Active configuration.
        module_path: Module path for core detection.
        file_path: Path to the source file.

    Returns:
        List of FunctionResult for import violations.
    """
    if not is_core_module(module_path, config):
        return []

    forbidden = set(config.architecture_rules.forbidden_imports_in_core)
    results: list[FunctionResult] = []

    # Loop invariant: results contains import violations found in nodes[0..i]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            # Loop invariant: violations checked for all names in node.names[0..j]
            for alias in node.names:
                top_module = alias.name.split(".")[0]
                if top_module in forbidden:
                    results.append(FunctionResult(
                        function="<module>",
                        file=file_path,
                        line=node.lineno,
                        level_requested=1,
                        level_achieved=0,
                        status=CheckStatus.FAILED,
                        details=(Detail(
                            level=VerificationLevel.STRUCTURAL,
                            tool="structural",
                            finding_type="violation",
                            message=f"Forbidden import '{alias.name}' in core module",
                            suggestion="Move I/O operations to an adapter module",
                        ),),
                    ))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top_module = node.module.split(".")[0]
                if top_module in forbidden:
                    results.append(FunctionResult(
                        function="<module>",
                        file=file_path,
                        line=node.lineno,
                        level_requested=1,
                        level_achieved=0,
                        status=CheckStatus.FAILED,
                        details=(Detail(
                            level=VerificationLevel.STRUCTURAL,
                            tool="structural",
                            finding_type="violation",
                            message=f"Forbidden import from '{node.module}' in core module",
                            suggestion="Move I/O operations to an adapter module",
                        ),),
                    ))

    return results


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_docstrings(
    tree: ast.Module,
    config: SerenecodeConfig,
    file_path: str,
) -> list[FunctionResult]:
    """Check that public functions, classes, and the module have docstrings.

    Args:
        tree: Parsed AST module.
        config: Active configuration.
        file_path: Path to the source file.

    Returns:
        List of FunctionResult for missing docstrings.
    """
    results: list[FunctionResult] = []

    # Check module docstring
    if not ast.get_docstring(tree):
        results.append(FunctionResult(
            function="<module>",
            file=file_path,
            line=1,
            level_requested=1,
            level_achieved=0,
            status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
                finding_type="violation",
                message="Module missing docstring",
                suggestion="Add a module-level docstring describing its role",
            ),),
        ))

    checkable_classes = _iter_checked_classes(tree)
    # Loop invariant: results contains class docstring findings for checkable_classes[0..i]
    for node in checkable_classes:
        if _is_public_class(node.name):
            if not ast.get_docstring(node):
                results.append(FunctionResult(
                    function=node.name,
                    file=file_path,
                    line=node.lineno,
                    level_requested=1,
                    level_achieved=0,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="structural",
                        finding_type="violation",
                        message=f"Class '{node.name}' missing docstring",
                        suggestion="Add a docstring describing the class",
                    ),),
                ))
    checkable_functions = _iter_checked_functions(tree)
    # Loop invariant: results contains function docstring findings for checkable_functions[0..i]
    for func_node in checkable_functions:
        if _is_public_function(func_node.name) and not ast.get_docstring(func_node):
            results.append(FunctionResult(
                function=func_node.name,
                file=file_path,
                line=func_node.lineno,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="violation",
                    message=f"Function '{func_node.name}' missing docstring",
                    suggestion="Add a docstring describing what the function does",
                ),),
            ))

    return results


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_loop_invariants(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    file_path: str,
) -> list[FunctionResult]:
    """Check that loops have invariant comments and recursive functions have variant docs.

    Args:
        source: Raw source code string.
        tree: Parsed AST module.
        config: Active configuration.
        file_path: Path to the source file.

    Returns:
        List of FunctionResult for missing loop invariant documentation.
    """
    if not config.loop_recursion_rules.require_loop_invariant_comments:
        return []

    # Extract comment line numbers and their content
    comments: dict[int, str] = {}
    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        # Loop invariant: comments dict contains all COMMENT tokens seen so far
        for tok_type, tok_string, tok_start, _, _ in tokens:
            if tok_type == tokenize.COMMENT:
                comments[tok_start[0]] = tok_string.lower()
    except (tokenize.TokenError, UnicodeDecodeError, UnicodeEncodeError):
        return []

    results: list[FunctionResult] = []
    invariant_keywords = ("invariant", "loop invariant")
    variant_keywords = ("variant", "decreasing", "termination")

    # Loop invariant: results contains loop/recursion findings for nodes[0..i]
    for node in ast.walk(tree):
        if isinstance(node, (ast.While, ast.For)):
            loop_line = node.lineno
            has_invariant_comment = False

            # Check lines around the loop for invariant comments
            # Loop invariant: has_invariant_comment is True if any checked line has invariant keyword
            for check_line in range(max(1, loop_line - 3), loop_line + 3):
                if check_line in comments:
                    comment = comments[check_line]
                    if any(kw in comment for kw in invariant_keywords):
                        has_invariant_comment = True
                        break

            # Also check first few lines inside the loop body
            if not has_invariant_comment and node.body:
                first_body_line = node.body[0].lineno
                # Loop invariant: has_invariant_comment is True if any body line checked has invariant keyword
                for check_line in range(first_body_line, first_body_line + 2):
                    if check_line in comments:
                        comment = comments[check_line]
                        if any(kw in comment for kw in invariant_keywords):
                            has_invariant_comment = True
                            break

            if not has_invariant_comment:
                results.append(FunctionResult(
                    function="<loop>",
                    file=file_path,
                    line=loop_line,
                    level_requested=1,
                    level_achieved=0,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="structural",
                        finding_type="violation",
                        message=f"Loop at line {loop_line} missing invariant comment",
                        suggestion="Add a comment: # Loop invariant: <property>",
                    ),),
                ))

    # Check recursive functions for variant documentation
    if config.loop_recursion_rules.require_recursion_variant_comments:
        # Loop invariant: results contains variant findings for recursive functions in nodes[0..i]
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            is_recursive = _is_recursive_function(node)
            if not is_recursive:
                continue

            func_start = node.lineno
            func_end = node.end_lineno or func_start + 1
            has_variant_comment = False

            # Loop invariant: has_variant_comment is True if any line in range has variant keyword
            for check_line in range(func_start, func_end + 1):
                if check_line in comments:
                    comment = comments[check_line]
                    if any(kw in comment for kw in variant_keywords):
                        has_variant_comment = True
                        break

            if not has_variant_comment:
                results.append(FunctionResult(
                    function=node.name,
                    file=file_path,
                    line=func_start,
                    level_requested=1,
                    level_achieved=0,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="structural",
                        finding_type="violation",
                        message=f"Recursive function '{node.name}' missing variant documentation",
                        suggestion="Add a comment: # Variant: <decreasing measure>",
                    ),),
                ))

    return results


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_recursive_function(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function calls itself (direct recursion).

    Args:
        node: A function definition AST node.

    Returns:
        True if the function contains a call to itself.
    """
    # Loop invariant: result is True if any child in children[0..i] is a self-call
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            if isinstance(child.func, ast.Name) and child.func.id == node.name:
                return True
    return False


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_exception_types(
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Check that core modules don't raise forbidden exception types.

    Args:
        tree: Parsed AST module.
        config: Active configuration.
        module_path: Module path for core detection.
        file_path: Path to the source file.

    Returns:
        List of FunctionResult for exception type violations.
    """
    if not config.error_handling_rules.require_domain_exceptions:
        return []

    if not is_core_module(module_path, config):
        return []

    forbidden = set(config.error_handling_rules.forbidden_exception_types)
    results: list[FunctionResult] = []

    # Loop invariant: results contains exception findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Raise):
            continue

        if node.exc is None:
            continue

        exc_name: str | None = None
        if isinstance(node.exc, ast.Call):
            if isinstance(node.exc.func, ast.Name):
                exc_name = node.exc.func.id
            elif isinstance(node.exc.func, ast.Attribute):
                exc_name = node.exc.func.attr
        elif isinstance(node.exc, ast.Name):
            exc_name = node.exc.id

        if exc_name and exc_name in forbidden:
            results.append(FunctionResult(
                function="<module>",
                file=file_path,
                line=node.lineno,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="violation",
                    message=f"Raising '{exc_name}' in core module — use domain-specific exception",
                    suggestion=f"Define a custom exception inheriting from SerenecodeError",
                ),),
            ))

    return results


@icontract.require(
    lambda tree: isinstance(tree, ast.Module),
    "tree must be an ast.Module",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def check_naming_conventions(
    tree: ast.Module,
    config: SerenecodeConfig,
    file_path: str,
) -> list[FunctionResult]:
    """Check naming conventions for classes, functions, and constants.

    Args:
        tree: Parsed AST module.
        config: Active configuration.
        file_path: Path to the source file.

    Returns:
        List of FunctionResult for naming convention violations.
    """
    results: list[FunctionResult] = []

    # Loop invariant: results contains naming findings for top-level nodes[0..i]
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            if not is_pascal_case(node.name) and _is_public_class(node.name):
                results.append(FunctionResult(
                    function=node.name,
                    file=file_path,
                    line=node.lineno,
                    level_requested=1,
                    level_achieved=0,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="structural",
                        finding_type="violation",
                        message=f"Class '{node.name}' does not follow PascalCase convention",
                    ),),
                ))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if (
                _is_public_function(node.name)
                and not is_snake_case(node.name)
                and not node.name.startswith("__")
            ):
                results.append(FunctionResult(
                    function=node.name,
                    file=file_path,
                    line=node.lineno,
                    level_requested=1,
                    level_achieved=0,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="structural",
                        finding_type="violation",
                        message=f"Function '{node.name}' does not follow snake_case convention",
                    ),),
                ))

    return results


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
def check_structural(
    source: str,
    config: SerenecodeConfig,
    module_path: str = "",
    file_path: str = "<unknown>",
) -> CheckResult:
    """Run the full Level 1 structural check on a source string.

    This is the main entry point for the structural checker. It parses
    the source code, resolves icontract import aliases, runs all individual
    check functions, and returns an aggregated CheckResult.

    Args:
        source: Python source code as a string.
        config: Active Serenecode configuration.
        module_path: Module path for architecture checks (e.g. "core/engine.py").
        file_path: File path for reporting (e.g. "src/serenecode/core/engine.py").

    Returns:
        A CheckResult containing all structural findings.
    """
    start_time = time.monotonic()

    # Parse the source
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        elapsed = time.monotonic() - start_time
        error_result = FunctionResult(
            function="<module>",
            file=file_path,
            line=max(1, exc.lineno or 1),
            level_requested=1,
            level_achieved=0,
            status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
                finding_type="error",
                message=f"Syntax error: {exc.msg}",
            ),),
        )
        return make_check_result(
            (error_result,),
            level_requested=1,
            duration_seconds=elapsed,
        )

    # Exempt modules still need to parse successfully, but skip structural policy checks.
    # Report them as EXEMPT so the verification scope is transparent.
    if is_exempt_module(module_path, config):
        elapsed = time.monotonic() - start_time
        exempt_result = FunctionResult(
            function="<module>",
            file=file_path,
            line=1,
            level_requested=1,
            level_achieved=0,
            status=CheckStatus.EXEMPT,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
                finding_type="exempt",
                message=f"Module '{module_path}' is exempt from structural checks",
            ),),
        )
        return make_check_result(
            (exempt_result,),
            level_requested=1,
            duration_seconds=elapsed,
        )

    # Resolve icontract aliases
    aliases = resolve_icontract_aliases(tree)

    # Run all check functions
    all_results: list[FunctionResult] = []
    all_results.extend(check_contracts(tree, config, aliases, file_path))
    all_results.extend(check_class_invariants(tree, config, aliases, file_path, source))
    all_results.extend(check_type_annotations(tree, config, file_path))
    all_results.extend(check_no_any_in_core(tree, config, module_path, file_path))
    all_results.extend(check_imports(tree, config, module_path, file_path))
    all_results.extend(check_docstrings(tree, config, file_path))
    all_results.extend(check_loop_invariants(source, tree, config, file_path))
    all_results.extend(check_exception_types(tree, config, module_path, file_path))
    all_results.extend(check_naming_conventions(tree, config, file_path))

    elapsed = time.monotonic() - start_time
    return make_check_result(
        tuple(all_results),
        level_requested=1,
        duration_seconds=elapsed,
    )
