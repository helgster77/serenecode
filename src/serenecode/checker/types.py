"""Type checking checker for Serenecode (Level 2).

This module implements Level 2 verification: it transforms results from
static type analysis backends (like mypy) into structured CheckResult
objects. The actual type checking is delegated to adapters.

This is a core module — no I/O operations are permitted. Type check
results are received as structured data, not generated here.
"""

from __future__ import annotations

import icontract

from serenecode.contracts.predicates import is_non_empty_string
from serenecode.models import (
    CheckResult,
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)
from serenecode.ports.type_checker import TypeIssue


@icontract.require(
    lambda issues: isinstance(issues, list),
    "issues must be a list",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
def transform_type_results(
    issues: list[TypeIssue],
    duration_seconds: float,
) -> CheckResult:
    """Transform mypy type issues into a CheckResult.

    Groups issues by file and line, creating FunctionResult entries
    for each location with type errors.

    Args:
        issues: List of type issues from a type checker adapter.
        duration_seconds: How long the type checking took.

    Returns:
        A CheckResult containing all type checking findings.
    """
    # Group issues by (file, line)
    grouped: dict[tuple[str, int], list[TypeIssue]] = {}
    # Loop invariant: grouped contains all issues from issues[0..i]
    for issue in issues:
        key = (issue.file, issue.line)
        grouped.setdefault(key, []).append(issue)

    func_results: list[FunctionResult] = []

    # Loop invariant: func_results contains results for all groups processed
    for (file_path, line), file_issues in sorted(grouped.items()):
        errors = [i for i in file_issues if i.severity == "error"]
        if not errors:
            continue

        details: list[Detail] = []
        # Loop invariant: details contains Detail for errors[0..j]
        for error in errors:
            suggestion = (
                _suggest_from_mypy_code(error.code, error.message)
                if error.code and is_non_empty_string(error.message)
                else None
            )
            details.append(Detail(
                level=VerificationLevel.TYPES,
                tool="mypy",
                finding_type="violation",
                message=f"{error.message}" + (f" [{error.code}]" if error.code else ""),
                suggestion=suggestion,
            ))

        func_results.append(FunctionResult(
            function=f"<line {line}>",
            file=file_path,
            line=line,
            level_requested=2,
            level_achieved=1,
            status=CheckStatus.FAILED,
            details=tuple(details),
        ))

    return make_check_result(
        tuple(func_results),
        level_requested=2,
        duration_seconds=duration_seconds,
    )


@icontract.require(
    lambda code: code is None or is_non_empty_string(code),
    "code must be a non-empty string when provided",
)
@icontract.require(
    lambda message: is_non_empty_string(message),
    "message must be a non-empty string",
)
@icontract.ensure(
    lambda result: result is None or isinstance(result, str),
    "result must be a string or None",
)
def _suggest_from_mypy_code(code: str | None, message: str) -> str | None:
    """Generate a fix suggestion from a mypy error code.

    Args:
        code: The mypy error code (e.g. "arg-type").
        message: The mypy error message.

    Returns:
        A suggestion string, or None.
    """
    suggestions: dict[str, str] = {
        "arg-type": "Change the argument to match the expected parameter type, or update the parameter annotation",
        "return-value": "Change the return expression to match the declared return type, or fix the return annotation",
        "assignment": "Change the assigned value to match the variable's type annotation, or fix the annotation",
        "attr-defined": "The attribute does not exist on this type — check for typos, add the attribute, or use hasattr/cast",
        "name-defined": "This name is not defined — add an import or fix the spelling",
        "override": "The method signature must match the parent class — update parameter or return types to be compatible",
        "misc": "Review the type annotation for correctness",
        "union-attr": "Not all union variants have this attribute — narrow the type with isinstance() or handle each variant",
        "no-untyped-def": "Add type annotations to all parameters and the return type",
        "type-arg": "Generic type needs explicit type parameters (e.g. list[str] not list)",
        "var-annotated": "Add a type annotation to this variable",
        "no-any-return": "The return type should not be Any — use a specific type",
        "import-untyped": "This module has no type stubs — add a # type: ignore[import-untyped] comment or install stubs",
        "call-overload": "No overload variant matches these argument types — check the function's overload signatures",
        "index": "Invalid index type — use the correct key/index type for this container",
        "operator": "This operator is not supported for these types — check operand types",
        "redundant-cast": "This cast is unnecessary — the expression already has the target type",
        "unreachable": "This code is unreachable — review the control flow above it",
        "truthy-bool": "This expression is always truthy/falsy — the condition may be wrong",
        "possibly-undefined": "This variable might not be defined on all code paths — initialize it or add a check",
    }
    if code and code in suggestions:
        return suggestions[code]
    # Fallback: include the error code so the agent can look it up
    if code:
        return f"Fix the type error (mypy code: {code}) — run 'mypy --show-error-codes' for details"
    return "Fix the type error — run 'mypy --strict' on this file for full details"
