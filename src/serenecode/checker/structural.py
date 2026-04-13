"""Structural checker for Serenecode conventions (Level 1).

AST-based analysis that validates Python source code follows the conventions
defined in SERENECODE.md: contracts, type annotations, and architecture.

Core module — no I/O. Source code is received as strings, not read from files.
"""

from __future__ import annotations

import ast
import io
import tokenize
import time

import icontract

from serenecode.config import SerenecodeConfig, is_core_module, is_exempt_module
from serenecode.models import (
    CheckResult,
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)

from serenecode.checker.structural_helpers import (
    IcontractNames,
    resolve_icontract_aliases,
    has_decorator,
    _decorator_has_description,
    _find_tautological_contracts,
    _decorator_descriptions_are_literals,
    _non_receiver_parameters,
    _extract_init_fields,
    _get_return_annotation_str,
    _is_public_function,
    _has_no_invariant_comment,
    _should_check_function_contracts,
    _has_property_decorator,
    _is_enum_class,
    _is_exception_class,
    _is_protocol_class,
    _is_public_class,
    _should_check_class_invariant,
    _iter_checked_functions,
    _iter_checked_classes,
    _is_recursive_function,
    _is_silent_handler,
    _has_silent_except_opt_out,
)

from serenecode.checker.structural_quality import (
    check_mutable_default_arguments,
    check_print_in_core,
    check_dangerous_calls,
    check_bare_asserts_outside_tests,
    check_stub_residue,
    check_todo_comments,
    check_no_assertions_in_tests,
    check_tautological_isinstance_postcondition,
    check_unused_parameters,
    check_naming_conventions,
)

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
        if _has_property_decorator(node):
            continue

        details = _check_single_function_contracts(node, config, aliases)
        status = CheckStatus.PASSED if not details else CheckStatus.FAILED
        results.append(FunctionResult(
            function=node.name, file=file_path, line=node.lineno,
            level_requested=1, level_achieved=1 if not details else 0,
            status=status, details=tuple(details),
        ))

    return results

def _check_single_function_contracts(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    config: SerenecodeConfig,
    aliases: IcontractNames,
) -> list[Detail]:
    """Check contracts on a single function node."""
    details: list[Detail] = []
    params = _non_receiver_parameters(node)
    param_names = [p.arg for p in params]
    has_params = bool(params)

    if has_params and not has_decorator(node, aliases.require_names):
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
    results.extend(_check_loop_invariant_comments(tree, comments, file_path))
    if config.loop_recursion_rules.require_recursion_variant_comments:
        results.extend(_check_recursion_variant_comments(tree, comments, file_path))
    return results


def _check_loop_invariant_comments(
    tree: ast.Module,
    comments: dict[int, str],
    file_path: str,
) -> list[FunctionResult]:
    """Check that loops have invariant comments."""
    invariant_keywords = ("invariant", "loop invariant")
    results: list[FunctionResult] = []

    # Loop invariant: results contains loop findings for nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.While, ast.For)):
            continue
        loop_line = node.lineno
        has_invariant = _has_nearby_comment(comments, loop_line, invariant_keywords)
        if not has_invariant and node.body:
            first_body_line = node.body[0].lineno
            has_invariant = _has_nearby_comment(
                comments, first_body_line, invariant_keywords, window=2,
            )
        if not has_invariant:
            results.append(FunctionResult(
                function="<loop>", file=file_path, line=loop_line,
                level_requested=1, level_achieved=0, status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL, tool="structural",
                    finding_type="violation",
                    message=f"Loop at line {loop_line} missing invariant comment",
                    suggestion="Add a comment: # Loop invariant: <property>",
                ),),
            ))
    return results


def _check_recursion_variant_comments(
    tree: ast.Module,
    comments: dict[int, str],
    file_path: str,
) -> list[FunctionResult]:
    """Check that recursive functions have variant documentation."""
    variant_keywords = ("variant", "decreasing", "termination")
    results: list[FunctionResult] = []

    # Loop invariant: results contains variant findings for recursive functions in nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_recursive_function(node):
            continue
        func_start = node.lineno
        func_end = node.end_lineno or func_start + 1
        has_variant = False
        # Loop invariant: has_variant is True if any line in range has variant keyword
        for check_line in range(func_start, func_end + 1):
            if check_line in comments:
                if any(kw in comments[check_line] for kw in variant_keywords):
                    has_variant = True
                    break
        if not has_variant:
            results.append(FunctionResult(
                function=node.name, file=file_path, line=func_start,
                level_requested=1, level_achieved=0, status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL, tool="structural",
                    finding_type="violation",
                    message=f"Recursive function '{node.name}' missing variant documentation",
                    suggestion="Add a comment: # Variant: <decreasing measure>",
                ),),
            ))
    return results


def _has_nearby_comment(
    comments: dict[int, str],
    target_line: int,
    keywords: tuple[str, ...],
    window: int = 3,
) -> bool:
    """Check if any comment near target_line contains a keyword."""
    # Loop invariant: no matching comment found in checked lines so far
    for check_line in range(max(1, target_line - window), target_line + window):
        if check_line in comments:
            if any(kw in comments[check_line] for kw in keywords):
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

    aliases = resolve_icontract_aliases(tree)

    all_results = _run_all_structural_checks(
        source, tree, config, aliases, module_path, file_path,
    )

    elapsed = time.monotonic() - start_time
    return make_check_result(
        tuple(all_results), level_requested=1, duration_seconds=elapsed,
    )


def _run_all_structural_checks(
    source: str,
    tree: ast.Module,
    config: SerenecodeConfig,
    aliases: IcontractNames,
    module_path: str,
    file_path: str,
) -> list[FunctionResult]:
    """Run all structural sub-checks and return aggregated results."""
    results: list[FunctionResult] = []
    results.extend(check_contracts(tree, config, aliases, file_path))
    results.extend(check_class_invariants(tree, config, aliases, file_path, source))
    results.extend(check_type_annotations(tree, config, file_path))
    results.extend(check_no_any_in_core(tree, config, module_path, file_path))
    results.extend(check_imports(tree, config, module_path, file_path))
    results.extend(check_docstrings(tree, config, file_path))
    results.extend(check_loop_invariants(source, tree, config, file_path))
    results.extend(check_exception_types(tree, config, module_path, file_path))
    results.extend(check_silent_exception_handling(source, tree, config, file_path))
    results.extend(check_mutable_default_arguments(source, tree, config, module_path, file_path))
    results.extend(check_print_in_core(source, tree, config, module_path, file_path))
    results.extend(check_dangerous_calls(source, tree, config, module_path, file_path))
    results.extend(check_bare_asserts_outside_tests(source, tree, config, module_path, file_path))
    results.extend(check_stub_residue(source, tree, config, module_path, file_path))
    results.extend(check_todo_comments(source, tree, config, module_path, file_path))
    results.extend(check_no_assertions_in_tests(source, tree, config, module_path, file_path))
    results.extend(check_tautological_isinstance_postcondition(source, tree, config, aliases, module_path, file_path))
    results.extend(check_unused_parameters(source, tree, config, module_path, file_path))
    results.extend(check_naming_conventions(tree, config, file_path))
    return results
