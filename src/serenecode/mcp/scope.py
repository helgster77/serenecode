"""Resolve requested symbols and retain evidence relevant to scoped MCP checks."""

from __future__ import annotations

import ast
from dataclasses import dataclass

import icontract

from serenecode.core.pipeline import SourceFile
from serenecode.models import (
    CheckResult, CheckStatus, Detail, FunctionResult, VerificationLevel, make_check_result,
)


@icontract.invariant(lambda self: bool(self.name) and 1 <= self.line <= self.end_line,
                     "a target must identify a named source range")
@dataclass(frozen=True)
class FunctionTarget:
    """A uniquely resolved function, including its enclosing classes or functions."""

    file: str
    name: str
    line: int
    end_line: int


@icontract.require(lambda source_file, function: source_file is not None and bool(function),
                   "source and function name must be provided")
@icontract.ensure(lambda result: result.line <= result.end_line, "target range must be ordered")
def resolve_function_target(source_file: SourceFile, function: str) -> FunctionTarget:
    """Resolve a qualified name, or an unambiguous short name, without importing code."""
    candidates = [
        (name, node) for name, node in _function_definitions(ast.parse(source_file.source))
        if name == function or ("." not in function and node.name == function)
    ]
    if not candidates:
        raise ValueError(f"Function '{function}' was not found in '{source_file.file_path}'.")
    if len(candidates) != 1:
        names = ", ".join(name for name, _ in candidates)
        raise ValueError(f"Function '{function}' is ambiguous; use a qualified name: {names}.")
    name, node = candidates[0]
    return FunctionTarget(source_file.file_path, name, node.lineno, node.end_lineno or node.lineno)


@icontract.require(lambda node: isinstance(node, ast.AST), "node must be an AST node")
@icontract.ensure(lambda result: isinstance(result, list), "definitions must be a list")
def _function_definitions(
    node: ast.AST, prefix: str = "",
) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    """Collect lexical names, including definitions inside compound statements."""
    definitions: list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]] = []
    # Variant: each recursive call visits a child of the current AST node.
    for child in ast.iter_child_nodes(node):
        name = prefix
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = f"{prefix}.{child.name}" if prefix else child.name
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.append((name, child))
        definitions.extend(_function_definitions(child, name))
    return definitions


@icontract.require(lambda finding, target: finding is not None and target is not None,
                   "result and target must be provided")
@icontract.ensure(lambda result: isinstance(result, bool), "matching returns a boolean")
def _matches_target(finding: FunctionResult, target: FunctionTarget) -> bool:
    """Match qualified backend names or source locations from structural checks."""
    if finding.file != target.file:
        return False
    if finding.level_requested >= 3 and finding.function == target.name:
        return True
    return (
        target.line <= finding.line <= target.end_line
        and finding.function in (target.name, target.name.rsplit(".", 1)[-1])
    )


@icontract.require(lambda check_result, target: check_result is not None and target is not None,
                   "result and target must be provided")
@icontract.ensure(lambda check_result, result: result.level_requested == check_result.level_requested,
                 "requested level must be preserved")
def filter_function_result(check_result: CheckResult, target: FunctionTarget) -> CheckResult:
    """Keep target evidence and blockers, without inheriting another function's deep pass."""
    selected = [r for r in check_result.results if _matches_target(r, target)]
    blockers = [r for r in check_result.results if r.status in (CheckStatus.FAILED, CheckStatus.SKIPPED)]
    # Loop invariant: selected retains target evidence and applicable contextual blockers.
    for result in blockers:
        contextual = result.function.startswith("<") or any(
            d.tool in ("spec_validation", "spec_traceability") for d in result.details
        )
        if contextual and result not in selected:
            selected.append(result)
    if not any(r.status in (CheckStatus.FAILED, CheckStatus.SKIPPED) for r in selected):
        selected.extend(r for r in blockers if r not in selected)
    achieved = check_result.level_achieved
    # Loop invariant: earlier target-based stages have passing evidence for this symbol.
    for stage in (1, 3, 4, 5):
        if stage > achieved:
            break
        if not any(_matches_target(r, target) and r.level_requested == stage
                   and r.status == CheckStatus.PASSED for r in selected):
            achieved = stage - 1
            selected.append(FunctionResult(
                function=target.name, file=target.file, line=target.line,
                level_requested=stage, level_achieved=achieved, status=CheckStatus.SKIPPED,
                details=(Detail(
                    level=VerificationLevel(stage), tool="scope", finding_type="not_exercised",
                    message=f"Level {stage} did not exercise '{target.name}'.",
                    suggestion="Check the target's backend eligibility and reported exemptions.",
                ),),
            ))
            break
    return make_check_result(tuple(selected), check_result.level_requested,
                             check_result.summary.duration_seconds, level_achieved=achieved)


@icontract.require(lambda path, function, message: bool(path) and bool(function) and bool(message),
                   "scope errors require a path, function, and message")
@icontract.require(lambda level: 1 <= level <= 6, "level must be between 1 and 6")
@icontract.ensure(lambda result: not result.passed, "a scope error must never pass")
def scope_error(path: str, function: str, level: int, message: str) -> CheckResult:
    """Return a structured failure when the requested symbol cannot be resolved."""
    finding = FunctionResult(
        function=function, file=path, line=1, level_requested=level, level_achieved=0,
        status=CheckStatus.FAILED,
        details=(Detail(level=VerificationLevel.STRUCTURAL, tool="scope",
                        finding_type="invalid_target", message=message),),
    )
    return make_check_result((finding,), level, 0.0, level_achieved=0)
