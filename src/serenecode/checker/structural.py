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
    lambda result: result.module_alias is None or any(name.startswith(result.module_alias + ".") for name in result.require_names | result.ensure_names | result.invariant_names),
    "when a module alias is recorded, at least one decorator name must reference it",
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
    lambda result: result == "" or result[0].isalpha() or result[0] == "_",
    "result must be empty or start with a valid Python identifier character",
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
    # silent-except: tokenization is a comment-extraction pre-pass; on tokenizer failure we skip loop-invariant checks
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
    lambda handler: isinstance(handler, ast.ExceptHandler),
    "handler must be an ast.ExceptHandler",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_silent_handler(handler: ast.ExceptHandler) -> bool:
    """Check if an exception handler silently swallows the exception.

    A handler is silent when its body contains only trivial statements
    (``pass``, ``...``, ``continue``, ``break``, or ``return`` with no
    value or a constant/empty-collection literal), no ``raise`` appears
    anywhere in the body, and the exception variable (if any) is unused.

    Args:
        handler: An AST exception handler node.

    Returns:
        True if the handler silently swallows the exception.
    """
    # Loop invariant: any raise found in body[0..i] sets has_raise True
    for child in ast.walk(handler):
        if isinstance(child, ast.Raise):
            return False

    # If the handler binds the exception (`except X as exc:`), check whether
    # the variable is referenced anywhere in the body — referencing it counts
    # as propagating information from the exception.
    exc_name = handler.name
    if exc_name is not None:
        # Loop invariant: any Name reference matching exc_name in body[0..i] returns False
        for stmt in handler.body:
            for child in ast.walk(stmt):
                if isinstance(child, ast.Name) and child.id == exc_name:
                    return False

    # Loop invariant: every statement in body[0..i] has been classified as trivial
    for stmt in handler.body:
        if not _is_trivial_handler_stmt(stmt):
            return False

    return True


@icontract.require(
    lambda stmt: isinstance(stmt, ast.stmt),
    "stmt must be an ast.stmt",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_trivial_handler_stmt(stmt: ast.stmt) -> bool:
    """Check if a single handler-body statement is a no-op silent fallback.

    Trivial statements are: ``pass``, an ellipsis expression statement,
    ``continue``, ``break``, and ``return`` with no value or a constant /
    empty-collection literal value.

    Args:
        stmt: An AST statement node from inside an exception handler body.

    Returns:
        True if the statement is a trivial silent-handler fallback.
    """
    if isinstance(stmt, ast.Pass):
        return True
    if isinstance(stmt, (ast.Continue, ast.Break)):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis:
        return True
    if isinstance(stmt, ast.Return):
        if stmt.value is None:
            return True
        if isinstance(stmt.value, ast.Constant):
            return True
        if isinstance(stmt.value, (ast.List, ast.Tuple, ast.Set)) and not stmt.value.elts:
            return True
        if isinstance(stmt.value, ast.Dict) and not stmt.value.keys:
            return True
    return False


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.require(
    lambda try_line: isinstance(try_line, int) and try_line >= 1,
    "try_line must be a positive line number",
)
@icontract.require(
    lambda except_line: isinstance(except_line, int) and except_line >= 1,
    "except_line must be a positive line number",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _has_silent_except_opt_out(
    source: str,
    try_line: int,
    except_line: int,
) -> bool:
    """Check whether a ``# silent-except: <reason>`` comment opts out the handler.

    The comment may appear on the line immediately above the ``try`` block
    or immediately above the specific ``except`` clause. The reason text
    after the colon is required and must be non-empty.

    Args:
        source: The full module source code.
        try_line: 1-based line number of the enclosing ``try`` statement.
        except_line: 1-based line number of the ``except`` clause.

    Returns:
        True if a valid opt-out comment is found.
    """
    if not source:
        return False
    lines = source.splitlines()
    candidate_indices = []
    # Loop invariant: candidate_indices contains valid in-bounds line indices for the line numbers checked so far
    for line_no in (try_line, except_line):
        index = line_no - 2  # one line above
        if 0 <= index < len(lines):
            candidate_indices.append(index)
    # Loop invariant: any candidate line containing a valid opt-out comment returns True
    for index in candidate_indices:
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            continue
        comment = stripped.lstrip("#").strip()
        if comment.lower().startswith("silent-except:"):
            reason = comment.split(":", 1)[1].strip()
            if reason:
                return True
    return False


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
def check_silent_exception_handling(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    file_path: str,
) -> list[FunctionResult]:
    """Check for exception handlers that silently swallow exceptions.

    Flags two patterns:

    - Bare ``except:`` clauses (with no exception type), which catch
      ``BaseException`` including ``SystemExit`` and ``KeyboardInterrupt``.
    - Handlers whose body is composed only of trivial fallbacks
      (``pass``, ``...``, ``continue``, ``break``, ``return`` with a constant
      or empty literal) and that neither re-raise nor reference the exception.

    Handlers explicitly marked with ``# silent-except: <reason>`` on the line
    above the ``try`` or ``except`` are exempt.

    Args:
        source: Raw source code string (used for opt-out comment lookup).
        tree: Parsed AST module.
        config: Active configuration.
        file_path: Path to the source file (for reporting).

    Returns:
        List of FunctionResult for each silent-handler violation.
    """
    if not config.error_handling_rules.forbid_silent_exception_handling:
        return []

    results: list[FunctionResult] = []

    # Loop invariant: results contains silent-handler findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        try_line = node.lineno
        # Loop invariant: results contains findings for handlers[0..j] of this try
        for handler in node.handlers:
            handler_line = handler.lineno
            is_bare = handler.type is None
            is_silent = _is_silent_handler(handler)
            if not (is_bare or is_silent):
                continue
            if _has_silent_except_opt_out(source, try_line, handler_line):
                continue
            if is_bare:
                message = (
                    f"Bare 'except:' at line {handler_line} catches BaseException "
                    f"(including SystemExit and KeyboardInterrupt)"
                )
                suggestion = (
                    "Catch a specific exception type, e.g. 'except Exception as exc:', "
                    "and either re-raise, log with the exception, or translate to a "
                    "domain exception. If silent fallback is intentional, add "
                    "'# silent-except: <reason>' above the try or except clause."
                )
            else:
                message = (
                    f"Silent exception handler at line {handler_line} swallows "
                    f"the exception without re-raising or using it"
                )
                suggestion = (
                    "Re-raise, raise a domain-specific exception, or capture and use "
                    "the exception value. If silent fallback is intentional, add "
                    "'# silent-except: <reason>' above the try or except clause."
                )
            results.append(FunctionResult(
                function="<except>",
                file=file_path,
                line=handler_line,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="structural",
                    finding_type="violation",
                    message=message,
                    suggestion=suggestion,
                ),),
            ))

    return results


# ---------------------------------------------------------------------------
# Code quality checks (AI failure-mode wave)
# ---------------------------------------------------------------------------


_TODO_PATTERN = re.compile(r"\b(?:TODO|FIXME|XXX|HACK)\b")
_DANGEROUS_NAME_FUNCS = frozenset({"eval", "exec"})
_DANGEROUS_ATTR_PATTERNS = frozenset({
    ("os", "system"),
    ("subprocess", "run"),
    ("subprocess", "Popen"),
    ("subprocess", "call"),
    ("subprocess", "check_call"),
    ("subprocess", "check_output"),
    ("pickle", "loads"),
    ("pickle", "load"),
})
_SUBPROCESS_FUNCS = frozenset({"run", "Popen", "call", "check_call", "check_output"})


@icontract.require(
    lambda module_path: isinstance(module_path, str),
    "module_path must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_test_module(module_path: str) -> bool:
    """Check whether a module path refers to a test file.

    A module is treated as a test if any path segment is ``tests`` or
    if the basename starts with ``test_``.
    """
    if not module_path:
        return False
    normalized = module_path.replace("\\", "/")
    segments = [s for s in normalized.split("/") if s and s != "."]
    if "tests" in segments:
        return True
    if not segments:
        return False
    basename = segments[-1]
    return basename.startswith("test_")


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.require(
    lambda target_lines: isinstance(target_lines, tuple),
    "target_lines must be a tuple",
)
@icontract.require(
    lambda keyword: is_non_empty_string(keyword),
    "keyword must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _has_opt_out_comment(
    source: str,
    target_lines: tuple[int, ...],
    keyword: str,
) -> bool:
    """Check whether a `# <keyword>: <reason>` comment opts out the line(s).

    The comment must appear on the line immediately above any line in
    ``target_lines``. The reason text after the colon is required.
    """
    if not source:
        return False
    lines = source.splitlines()
    prefix = keyword.lower() + ":"
    seen: set[int] = set()
    # Loop invariant: any candidate index already seen has been checked
    for target_line in target_lines:
        index = target_line - 2  # one line above (0-based)
        if not (0 <= index < len(lines)):
            continue
        if index in seen:
            continue
        seen.add(index)
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            continue
        comment = stripped.lstrip("#").strip()
        if not comment.lower().startswith(prefix):
            continue
        reason = comment.split(":", 1)[1].strip()
        if reason:
            return True
    return False


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple) and len(result) >= 1,
    "result must be a non-empty tuple",
)
def _function_opt_out_lines(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[int, ...]:
    """Return the candidate line numbers where a function's opt-out comment can sit.

    For a plain function this is just the def line. For a decorated function it
    also includes the first decorator's line, so users can place the opt-out
    above either the def or the topmost decorator.
    """
    if node.decorator_list:
        return (node.lineno, node.decorator_list[0].lineno)
    return (node.lineno,)


@icontract.require(
    lambda node: isinstance(node, ast.expr),
    "node must be an AST expression",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_mutable_literal(node: ast.expr) -> bool:
    """Check if an AST expression is a mutable default literal."""
    if isinstance(node, (ast.List, ast.Dict, ast.Set)):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {"list", "dict", "set"}:
            return True
    return False


@icontract.require(
    lambda call: isinstance(call, ast.Call),
    "call must be an ast.Call",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _has_shell_true_kwarg(call: ast.Call) -> bool:
    """Check whether a Call has shell=True among its keyword arguments."""
    # Loop invariant: no keyword in keywords[0..i] is shell=True so far
    for kw in call.keywords:
        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
            return True
    return False


@icontract.require(
    lambda body: isinstance(body, list),
    "body must be a list of statements",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_stub_body(body: list[ast.stmt]) -> bool:
    """Check if a function body consists only of stub statements (after optional docstring)."""
    statements = list(body)
    # Skip leading docstring expression
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements = statements[1:]
    if not statements:
        return True
    if len(statements) != 1:
        return False
    stmt = statements[0]
    if isinstance(stmt, ast.Pass):
        return True
    if (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    ):
        return True
    if isinstance(stmt, ast.Raise):
        exc = stmt.exc
        if isinstance(exc, ast.Name) and exc.id == "NotImplementedError":
            return True
        if (
            isinstance(exc, ast.Call)
            and isinstance(exc.func, ast.Name)
            and exc.func.id == "NotImplementedError"
        ):
            return True
    return False


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _has_abstractmethod_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function is decorated with @abstractmethod (any import style)."""
    # Loop invariant: no decorator in decorators[0..i] matches abstractmethod
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "abstractmethod":
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == "abstractmethod":
            return True
    return False


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_init_with_only_assignments(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Check if an __init__ body is only attribute assignments + optional docstring."""
    statements = list(node.body)
    if (
        statements
        and isinstance(statements[0], ast.Expr)
        and isinstance(statements[0].value, ast.Constant)
        and isinstance(statements[0].value.value, str)
    ):
        statements = statements[1:]
    if not statements:
        return False
    # Loop invariant: all statements in body[0..i] are assignments
    for stmt in statements:
        if not isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            return False
    return True


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _function_has_assertion(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function body contains any assertion-equivalent.

    Counts: ``assert``, ``pytest.raises(...)``, ``pytest.fail(...)``,
    ``with pytest.raises(...)``, and ``self.assertX(...)``.
    """
    # Loop invariant: any qualifying child found in walk[0..i] returns True
    for child in ast.walk(node):
        if isinstance(child, ast.Assert):
            return True
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            attr = child.func.attr
            if attr in {"raises", "fail"}:
                return True
            if attr.startswith("assert"):
                return True
        if isinstance(child, ast.With):
            # Loop invariant: no item in items[0..i] has yet matched a pytest.raises ctxmgr
            for item in child.items:
                ctx = item.context_expr
                if (
                    isinstance(ctx, ast.Call)
                    and isinstance(ctx.func, ast.Attribute)
                    and ctx.func.attr == "raises"
                ):
                    return True
    return False


@icontract.require(
    lambda node: isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)),
    "node must be a function definition",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _has_override_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function is decorated with @override (typing or typing_extensions)."""
    # Loop invariant: no decorator in decorators[0..i] is override
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "override":
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == "override":
            return True
    return False


@icontract.require(
    lambda cls: isinstance(cls, ast.ClassDef),
    "cls must be a ClassDef",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _has_non_object_base(cls: ast.ClassDef) -> bool:
    """Check whether a class inherits from anything other than object."""
    # Loop invariant: no base in bases[0..i] has been classified as non-object
    for base in cls.bases:
        if isinstance(base, ast.Name) and base.id != "object":
            return True
        if isinstance(base, ast.Attribute):
            return True
        if isinstance(base, ast.Subscript):
            return True
    return False


@icontract.require(
    lambda lam: isinstance(lam, ast.Lambda),
    "lam must be a Lambda",
)
@icontract.require(
    lambda return_type_str: isinstance(return_type_str, str),
    "return_type_str must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def _is_tautological_isinstance(lam: ast.Lambda, return_type_str: str) -> bool:
    """Check if a lambda body is `isinstance(result, T)` where T equals the return annotation."""
    body = lam.body
    if not isinstance(body, ast.Call):
        return False
    if not (isinstance(body.func, ast.Name) and body.func.id == "isinstance"):
        return False
    if len(body.args) != 2:
        return False
    type_arg_str = ast.unparse(body.args[1])
    return type_arg_str == return_type_str


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
def check_mutable_default_arguments(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Flag mutable default arguments ([], {}, set(), list(), dict())."""
    if not config.code_quality_rules.forbid_mutable_default_arguments:
        return []
    if is_exempt_module(module_path, config):
        return []

    results: list[FunctionResult] = []

    # Loop invariant: results contains findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defaults: list[ast.expr] = list(node.args.defaults)
        # Loop invariant: defaults includes all non-None kw_defaults from kw_defaults[0..j]
        for kw_default in node.args.kw_defaults:
            if kw_default is not None:
                defaults.append(kw_default)
        # Loop invariant: no default in defaults[0..k] has been flagged for this function
        for default in defaults:
            if not _is_mutable_literal(default):
                continue
            if _has_opt_out_comment(source, _function_opt_out_lines(node), "allow-mutable-default"):
                break
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
                    message=f"Function '{node.name}' has a mutable default argument",
                    suggestion=(
                        "Use None as the default and create the mutable value inside the function. "
                        "If intentional, add '# allow-mutable-default: <reason>' above the def."
                    ),
                ),),
            ))
            break  # one finding per function

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
def check_print_in_core(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Flag print() calls in core modules."""
    if not config.code_quality_rules.forbid_print_in_core:
        return []
    if not is_core_module(module_path, config):
        return []

    results: list[FunctionResult] = []

    # Loop invariant: results contains findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Name) and node.func.id == "print"):
            continue
        if _has_opt_out_comment(source, (node.lineno,), "allow-print"):
            continue
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
                message=f"print() call in core module at line {node.lineno}",
                suggestion=(
                    "Core modules must not perform I/O. Return data to the caller or use a "
                    "logger configured at the composition root. If intentional, add "
                    "'# allow-print: <reason>' above the call."
                ),
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
def check_dangerous_calls(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Flag eval/exec, pickle.loads, os.system, subprocess.* with shell=True."""
    if not config.code_quality_rules.forbid_dangerous_calls:
        return []
    if is_exempt_module(module_path, config):
        return []

    results: list[FunctionResult] = []

    # Loop invariant: results contains findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        finding_msg: str | None = None
        if isinstance(node.func, ast.Name) and node.func.id in _DANGEROUS_NAME_FUNCS:
            finding_msg = f"Call to '{node.func.id}()' at line {node.lineno}"
        elif isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
            mod_name = node.func.value.id
            attr_name = node.func.attr
            if (mod_name, attr_name) in _DANGEROUS_ATTR_PATTERNS:
                if mod_name == "subprocess" and attr_name in _SUBPROCESS_FUNCS:
                    if not _has_shell_true_kwarg(node):
                        continue
                    finding_msg = (
                        f"Call to 'subprocess.{attr_name}(..., shell=True)' at line {node.lineno}"
                    )
                else:
                    finding_msg = f"Call to '{mod_name}.{attr_name}()' at line {node.lineno}"

        if finding_msg is None:
            continue
        if _has_opt_out_comment(source, (node.lineno,), "allow-dangerous"):
            continue
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
                message=finding_msg,
                suggestion=(
                    "Avoid eval/exec, pickle.loads on untrusted data, os.system, and subprocess "
                    "with shell=True. Prefer ast.literal_eval, json, argument lists, or specific "
                    "library APIs. If genuinely necessary, add '# allow-dangerous: <reason>' "
                    "above the call."
                ),
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
def check_bare_asserts_outside_tests(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Flag `assert` statements in non-test source modules."""
    if not config.code_quality_rules.forbid_bare_asserts_outside_tests:
        return []
    if is_exempt_module(module_path, config):
        return []
    if _is_test_module(module_path):
        return []

    results: list[FunctionResult] = []

    # Loop invariant: results contains findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assert):
            continue
        if _has_opt_out_comment(source, (node.lineno,), "allow-assert"):
            continue
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
                message=(
                    f"`assert` at line {node.lineno} in non-test source disappears under "
                    f"`python -O` and must not be used as a runtime check"
                ),
                suggestion=(
                    "Replace with an explicit `raise` of a domain exception, or move the assertion "
                    "into a test. If used for type narrowing, add '# allow-assert: <reason>' "
                    "above the statement."
                ),
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
def check_stub_residue(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Flag functions whose body is only pass / ... / raise NotImplementedError."""
    if not config.code_quality_rules.forbid_stub_residue:
        return []
    if is_exempt_module(module_path, config):
        return []

    results: list[FunctionResult] = []

    # Map functions to their immediate enclosing class for Protocol detection
    class_map: dict[int, ast.ClassDef] = {}
    # Loop invariant: class_map contains entries for methods of classes walked so far
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef):
            for member in cls.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_map[id(member)] = cls

    # Loop invariant: results contains findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        parent = class_map.get(id(node))
        if parent is not None and _is_protocol_class(parent):
            continue
        if _has_abstractmethod_decorator(node):
            continue
        if node.name == "__init__" and _is_init_with_only_assignments(node):
            continue
        if not _is_stub_body(node.body):
            continue
        if _has_opt_out_comment(source, _function_opt_out_lines(node), "allow-stub"):
            continue
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
                message=(
                    f"Function '{node.name}' has a stub body (only pass, ..., or "
                    f"raise NotImplementedError)"
                ),
                suggestion=(
                    "Implement the function or remove it. If it is an interface placeholder, "
                    "make the class a Protocol or decorate with @abstractmethod. "
                    "If intentional, add '# allow-stub: <reason>' above the def."
                ),
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
def check_todo_comments(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Flag TODO/FIXME/XXX/HACK markers in tracked source files."""
    if not config.code_quality_rules.forbid_todo_comments:
        return []
    if is_exempt_module(module_path, config):
        return []
    if _is_test_module(module_path):
        return []
    del tree  # not needed; comment scan is purely lexical

    results: list[FunctionResult] = []
    # silent-except: tokenization is a comment-extraction pre-pass; on tokenizer failure we skip the marker scan
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, UnicodeDecodeError, UnicodeEncodeError, IndentationError):
        return []

    # Loop invariant: results contains findings for tokens[0..i]
    for tok_type, tok_string, tok_start, _, _ in tokens:
        if tok_type != tokenize.COMMENT:
            continue
        # Skip the opt-out comment itself so it doesn't self-trigger
        if "allow-todo:" in tok_string.lower():
            continue
        match = _TODO_PATTERN.search(tok_string)
        if match is None:
            continue
        line_no = tok_start[0]
        if _has_opt_out_comment(source, (line_no,), "allow-todo"):
            continue
        marker = match.group(0).upper()
        results.append(FunctionResult(
            function="<comment>",
            file=file_path,
            line=line_no,
            level_requested=1,
            level_achieved=0,
            status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="structural",
                finding_type="violation",
                message=f"{marker} marker in tracked source at line {line_no}",
                suggestion=(
                    f"Resolve the {marker}, file an issue and reference it, or delete the comment. "
                    f"If intentional, add '# allow-todo: <issue link or reason>' above the marker."
                ),
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
def check_no_assertions_in_tests(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Flag test_* functions in test modules with no assertion-equivalent."""
    if not config.code_quality_rules.require_test_assertions:
        return []
    if not _is_test_module(module_path):
        return []

    results: list[FunctionResult] = []

    # Loop invariant: results contains findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test_"):
            continue
        if _function_has_assertion(node):
            continue
        if _has_opt_out_comment(source, _function_opt_out_lines(node), "allow-no-assert"):
            continue
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
                message=f"Test function '{node.name}' has no assertion",
                suggestion=(
                    "Add an `assert`, `pytest.raises(...)`, `pytest.fail(...)`, or "
                    "`self.assertX(...)` call. If the test legitimately just checks that "
                    "code does not raise, add '# allow-no-assert: <reason>' above the def."
                ),
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
def check_tautological_isinstance_postcondition(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    aliases: IcontractNames,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Flag @ensure(lambda result: isinstance(result, T)) when T == return annotation.

    Scope: public functions only (excludes ``_``-prefixed helpers and
    ``is_``/``has_``-prefixed predicates). The check targets AI-shipped weak
    postconditions on the public API. Internal predicates exist to compose
    into other contracts and their type signature is the entire constraint;
    asking for a "stronger" postcondition would just restate their body.
    """
    if not config.code_quality_rules.forbid_isinstance_tautology:
        return []
    if is_exempt_module(module_path, config):
        return []

    results: list[FunctionResult] = []

    # Loop invariant: results contains findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.returns is None:
            continue
        # Skip private helpers — their job is to compose into the public API's
        # contracts, and asking for a "stronger" postcondition tends to just
        # restate the body.
        if node.name.startswith("_"):
            continue
        # Skip predicate-style functions (is_*/has_*) — they exist precisely
        # to be reused in *other* functions' contracts, and their own
        # postcondition is necessarily "result is what the body says it is".
        if node.name.startswith(("is_", "has_")):
            continue
        return_type_str = ast.unparse(node.returns)
        flagged = False
        # Loop invariant: no decorator in decorator_list[0..j] has been flagged
        for dec in node.decorator_list:
            if flagged:
                break
            if not (isinstance(dec, ast.Call) and dec.args):
                continue
            dec_name = get_decorator_name(dec)
            if dec_name not in aliases.ensure_names:
                continue
            lam = dec.args[0]
            if not isinstance(lam, ast.Lambda):
                continue
            if not _is_tautological_isinstance(lam, return_type_str):
                continue
            if _has_opt_out_comment(source, _function_opt_out_lines(node), "allow-isinstance-tautology"):
                flagged = True
                continue
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
                    message=(
                        f"Function '{node.name}' has tautological postcondition "
                        f"`isinstance(result, {return_type_str})` "
                        f"(return annotation already guarantees this)"
                    ),
                    suggestion=(
                        "Replace with a meaningful constraint on `result` (range, length, "
                        "relationship to inputs). The return annotation already enforces type."
                    ),
                ),),
            ))
            flagged = True

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
def check_unused_parameters(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Flag function parameters never referenced in the body (strict-only by default)."""
    if not config.code_quality_rules.forbid_unused_parameters:
        return []
    if is_exempt_module(module_path, config):
        return []

    results: list[FunctionResult] = []

    class_map: dict[int, ast.ClassDef] = {}
    # Loop invariant: class_map contains entries for methods of classes walked so far
    for cls in ast.walk(tree):
        if isinstance(cls, ast.ClassDef):
            for member in cls.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    class_map[id(member)] = cls

    # Loop invariant: results contains findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _has_override_decorator(node):
            continue
        parent = class_map.get(id(node))
        if parent is not None and _has_non_object_base(parent):
            # Conservative: assume method participates in interface conformance
            continue
        # Collect referenced names in body
        referenced: set[str] = set()
        # Loop invariant: referenced contains all Name ids walked so far
        for stmt in node.body:
            for body_node in ast.walk(stmt):
                if isinstance(body_node, ast.Name):
                    referenced.add(body_node.id)
        params = _non_receiver_parameters(node)
        # Loop invariant: results gained one entry per unused param in params[0..k]
        for param in params:
            if param is node.args.vararg or param is node.args.kwarg:
                continue
            if param.arg.startswith("_"):
                continue
            if param.arg in referenced:
                continue
            if _has_opt_out_comment(source, _function_opt_out_lines(node), "allow-unused-param"):
                break
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
                    message=f"Parameter '{param.arg}' of '{node.name}' is unused",
                    suggestion=(
                        f"Remove the parameter, rename it to '_{param.arg}' to mark it intentionally "
                        f"unused, or add '# allow-unused-param: <reason>' above the def."
                    ),
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
    lambda result: result.level_requested == 1,
    "structural check reports findings at the structural level",
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
    all_results.extend(check_silent_exception_handling(source, tree, config, file_path))
    all_results.extend(check_mutable_default_arguments(source, tree, config, module_path, file_path))
    all_results.extend(check_print_in_core(source, tree, config, module_path, file_path))
    all_results.extend(check_dangerous_calls(source, tree, config, module_path, file_path))
    all_results.extend(check_bare_asserts_outside_tests(source, tree, config, module_path, file_path))
    all_results.extend(check_stub_residue(source, tree, config, module_path, file_path))
    all_results.extend(check_todo_comments(source, tree, config, module_path, file_path))
    all_results.extend(check_no_assertions_in_tests(source, tree, config, module_path, file_path))
    all_results.extend(check_tautological_isinstance_postcondition(source, tree, config, aliases, module_path, file_path))
    all_results.extend(check_unused_parameters(source, tree, config, module_path, file_path))
    all_results.extend(check_naming_conventions(tree, config, file_path))

    elapsed = time.monotonic() - start_time
    return make_check_result(
        tuple(all_results),
        level_requested=1,
        duration_seconds=elapsed,
    )
