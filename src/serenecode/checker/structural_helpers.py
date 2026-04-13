"""Helper utilities and data types for the structural checker.

This module contains AST inspection helpers, decorator matching functions,
and iteration utilities used by both the core structural checks and the
code quality checks. It is a core module — no I/O operations are permitted.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

import icontract

from serenecode.config import SerenecodeConfig
from serenecode.contracts.predicates import is_non_empty_string


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


# allow-unused: public API used by test infrastructure
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
def _has_override_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check if a function is decorated with @override (typing or typing_extensions)."""
    # Loop invariant: no decorator in decorators[0..i] is override
    for dec in node.decorator_list:
        if isinstance(dec, ast.Name) and dec.id == "override":
            return True
        if isinstance(dec, ast.Attribute) and dec.attr == "override":
            return True
    return False
