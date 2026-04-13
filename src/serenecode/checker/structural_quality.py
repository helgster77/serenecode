"""Code quality checks for the structural checker (AI failure-mode wave).

This module implements the code quality checks that detect common AI-generated
code issues: mutable defaults, dangerous calls, stub residue, TODO markers,
missing test assertions, tautological isinstance postconditions, unused
parameters, and naming convention violations.

This is a core module — no I/O operations are permitted. Source code is received
as strings, not read from files.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize

import icontract

from serenecode.config import SerenecodeConfig, is_core_module, is_exempt_module
from serenecode.contracts.predicates import is_non_empty_string, is_pascal_case, is_snake_case
from serenecode.models import (
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
)

from serenecode.checker.structural_helpers import (
    IcontractNames,
    _function_opt_out_lines,
    _has_abstractmethod_decorator,
    _has_override_decorator,
    _has_shell_true_kwarg,
    _is_mutable_literal,
    _is_protocol_class,
    _is_public_class,
    _is_public_function,
    _is_test_module,
    _non_receiver_parameters,
    get_decorator_name,
)


# ---------------------------------------------------------------------------
# Constants
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


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------


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
