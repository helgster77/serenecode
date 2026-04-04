"""Spec traceability checker for Serenecode.

This module verifies that every requirement in a SPEC.md file (identified
by REQ-xxx tags) has corresponding implementation and test references in
the source code.

This is a core module — no I/O operations are permitted. All content is
received as strings.
"""

from __future__ import annotations

import ast
import re

import icontract

from serenecode.contracts.predicates import is_non_empty_string
from serenecode.core.pipeline import SourceFile
from serenecode.models import (
    CheckResult,
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)

_REQ_PATTERN = re.compile(r"\bREQ-\d{3,4}\b")
_REQ_HEADING_PATTERN = re.compile(
    r"^#{1,6}\s+(REQ-\d{3,4})(?::\s*(.+))?$", re.MULTILINE,
)
_IMPLEMENTS_PATTERN = re.compile(r"Implements:\s*((?:REQ-\d{3,4})(?:\s*,\s*REQ-\d{3,4})*)")
_VERIFIES_PATTERN = re.compile(r"Verifies:\s*((?:REQ-\d{3,4})(?:\s*,\s*REQ-\d{3,4})*)")


@icontract.require(
    lambda spec_content: isinstance(spec_content, str),
    "spec_content must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, frozenset),
    "result must be a frozenset",
)
def extract_spec_requirements(spec_content: str) -> frozenset[str]:
    """Extract all REQ-xxx identifiers from spec content.

    Args:
        spec_content: The full text of SPEC.md.

    Returns:
        Frozenset of unique requirement IDs found in the spec.
    """
    return frozenset(_REQ_PATTERN.findall(spec_content))


@icontract.require(
    lambda spec_content: is_non_empty_string(spec_content),
    "spec_content must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
def validate_spec(spec_content: str) -> CheckResult:
    """Validate that a SPEC.md is well-formed and ready for SereneCode.

    Checks:
    1. At least one REQ-xxx identifier exists.
    2. No duplicate REQ IDs.
    3. REQ IDs are sequential with no gaps.
    4. Each REQ heading has a description.

    Args:
        spec_content: The full text of SPEC.md.

    Returns:
        A CheckResult with validation findings.
    """
    func_results: list[FunctionResult] = []
    lines = spec_content.splitlines()

    # Parse all REQ headings with their line numbers and descriptions
    headings: list[tuple[str, str | None, int]] = []
    # Loop invariant: headings contains parsed REQ headings from lines[0..i]
    for line_idx, line in enumerate(lines, start=1):
        match = _REQ_HEADING_PATTERN.match(line.strip())
        if match:
            req_id = match.group(1)
            description = match.group(2)
            headings.append((req_id, description, line_idx))

    # Also find REQ references that aren't in headings (inline mentions)
    all_req_ids = _REQ_PATTERN.findall(spec_content)
    heading_ids = [h[0] for h in headings]

    # Check 1: At least one REQ exists
    if not all_req_ids:
        func_results.append(FunctionResult(
            function="SPEC.md",
            file="SPEC.md",
            line=1,
            level_requested=1,
            level_achieved=0,
            status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="spec_validation",
                finding_type="no_requirements",
                message="No REQ-xxx identifiers found in spec",
                suggestion=(
                    "Add requirements with headings like "
                    "'### REQ-001: Description of requirement'."
                ),
            ),),
        ))
        return make_check_result(
            tuple(func_results), level_requested=1, duration_seconds=0.0,
        )

    # Check 2: No duplicate REQ IDs in headings
    seen: dict[str, int] = {}
    # Loop invariant: seen maps req_ids to first line for headings[0..i]
    for req_id, _desc, line_no in headings:
        if req_id in seen:
            func_results.append(FunctionResult(
                function=req_id,
                file="SPEC.md",
                line=line_no,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="spec_validation",
                    finding_type="duplicate_requirement",
                    message=(
                        f"{req_id} appears on line {seen[req_id]} and line {line_no}"
                    ),
                    suggestion=f"Remove the duplicate {req_id} or renumber.",
                ),),
            ))
        else:
            seen[req_id] = line_no

    # Check 3: Sequential with no gaps
    unique_ids = sorted(set(heading_ids))
    if unique_ids:
        numbers = []
        # Loop invariant: numbers contains parsed integers for unique_ids[0..i]
        for req_id in unique_ids:
            num_str = req_id.split("-", 1)[1]
            numbers.append(int(num_str))
        numbers.sort()

        if numbers:
            # Determine zero-padding width from the original REQ IDs
            first_id = unique_ids[0]
            width = len(first_id.split("-", 1)[1])
            expected = list(range(numbers[0], numbers[0] + len(numbers)))
            missing = set(expected) - set(numbers)
            # Loop invariant: func_results updated for missing numbers[0..i]
            for num in sorted(missing):
                missing_id = f"REQ-{num:0{width}d}"
                func_results.append(FunctionResult(
                    function=missing_id,
                    file="SPEC.md",
                    line=1,
                    level_requested=1,
                    level_achieved=0,
                    status=CheckStatus.FAILED,
                    details=(Detail(
                        level=VerificationLevel.STRUCTURAL,
                        tool="spec_validation",
                        finding_type="gap_in_sequence",
                        message=f"{missing_id} is missing from the sequence",
                        suggestion=f"Add {missing_id} or renumber to close the gap.",
                    ),),
                ))

    # Check 4: Each REQ heading has a description
    # Loop invariant: func_results updated for headings[0..i] missing descriptions
    for req_id, description, line_no in headings:
        if not description or not description.strip():
            func_results.append(FunctionResult(
                function=req_id,
                file="SPEC.md",
                line=line_no,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="spec_validation",
                    finding_type="missing_description",
                    message=f"{req_id} has no description",
                    suggestion=(
                        f"Add a description: '### {req_id}: Description of what "
                        f"this requirement means'."
                    ),
                ),),
            ))

    # If no failures, report success
    if not func_results:
        req_count = len(unique_ids) if unique_ids else len(set(all_req_ids))
        first = min(unique_ids) if unique_ids else "?"
        last = max(unique_ids) if unique_ids else "?"
        func_results.append(FunctionResult(
            function="SPEC.md",
            file="SPEC.md",
            line=1,
            level_requested=1,
            level_achieved=1,
            status=CheckStatus.PASSED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL,
                tool="spec_validation",
                finding_type="valid_spec",
                message=(
                    f"Spec is valid: {req_count} requirements "
                    f"({first} through {last})"
                ),
            ),),
        ))

    return make_check_result(
        tuple(func_results), level_requested=1, duration_seconds=0.0,
    )


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def extract_implementations(source: str) -> list[tuple[str, str, int]]:
    """Extract Implements: REQ-xxx references from function docstrings.

    Args:
        source: Python source code as a string.

    Returns:
        List of (function_name, req_id, line_number) tuples.
    """
    if not source.strip():
        return []
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError):
        return []

    results: list[tuple[str, str, int]] = []

    # Loop invariant: results contains all Implements references from nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        docstring = ast.get_docstring(node)
        if docstring is None:
            continue
        # Loop invariant: results contains refs from all matches in docstring[0..j]
        for match in _IMPLEMENTS_PATTERN.finditer(docstring):
            req_ids = _REQ_PATTERN.findall(match.group(0))
            for req_id in req_ids:
                results.append((node.name, req_id, node.lineno))

    return results


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def extract_verifications(source: str) -> list[tuple[str, str, int]]:
    """Extract Verifies: REQ-xxx references from function docstrings.

    Args:
        source: Python source code as a string.

    Returns:
        List of (function_name, req_id, line_number) tuples.
    """
    if not source.strip():
        return []
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError):
        return []

    results: list[tuple[str, str, int]] = []

    # Loop invariant: results contains all Verifies references from nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        docstring = ast.get_docstring(node)
        if docstring is None:
            continue
        # Loop invariant: results contains refs from all matches in docstring[0..j]
        for match in _VERIFIES_PATTERN.finditer(docstring):
            req_ids = _REQ_PATTERN.findall(match.group(0))
            for req_id in req_ids:
                results.append((node.name, req_id, node.lineno))

    return results


@icontract.require(
    lambda spec_content: is_non_empty_string(spec_content),
    "spec_content must be a non-empty string",
)
@icontract.require(
    lambda source_files: isinstance(source_files, tuple),
    "source_files must be a tuple",
)
@icontract.require(
    lambda test_sources: isinstance(test_sources, tuple),
    "test_sources must be a tuple",
)
@icontract.ensure(
    lambda result: isinstance(result, CheckResult),
    "result must be a CheckResult",
)
def check_spec_traceability(
    spec_content: str,
    source_files: tuple[SourceFile, ...],
    test_sources: tuple[tuple[str, str], ...],
) -> CheckResult:
    """Check that all spec requirements are implemented and tested.

    Args:
        spec_content: The full text of SPEC.md.
        source_files: Source files to scan for Implements: tags.
        test_sources: Tuple of (file_path, source_content) for test files.

    Returns:
        A CheckResult with findings for missing implementations,
        missing verifications, and orphan references.
    """
    spec_reqs = extract_spec_requirements(spec_content)
    if not spec_reqs:
        return make_check_result((), level_requested=1, duration_seconds=0.0)

    # Collect all implementations across source files
    implemented: dict[str, list[tuple[str, str, int]]] = {}
    # Loop invariant: implemented contains refs for source_files[0..i]
    for sf in source_files:
        impls = extract_implementations(sf.source)
        for func_name, req_id, line in impls:
            implemented.setdefault(req_id, []).append((sf.file_path, func_name, line))

    # Collect all verifications across test files
    verified: dict[str, list[tuple[str, str, int]]] = {}
    # Loop invariant: verified contains refs for test_sources[0..i]
    for test_path, test_source in test_sources:
        verifs = extract_verifications(test_source)
        for func_name, req_id, line in verifs:
            verified.setdefault(req_id, []).append((test_path, func_name, line))

    # Also check source files for Verifies tags (some projects put tests inline)
    # Loop invariant: verified updated with refs for source_files[0..i]
    for sf in source_files:
        verifs = extract_verifications(sf.source)
        for func_name, req_id, line in verifs:
            verified.setdefault(req_id, []).append((sf.file_path, func_name, line))

    all_referenced = set(implemented.keys()) | set(verified.keys())
    func_results: list[FunctionResult] = []

    # Check each spec requirement
    # Loop invariant: func_results contains findings for sorted(spec_reqs)[0..i]
    for req_id in sorted(spec_reqs):
        has_impl = req_id in implemented
        has_test = req_id in verified

        if has_impl and has_test:
            impl_info = implemented[req_id][0]
            func_results.append(FunctionResult(
                function=req_id,
                file=impl_info[0],
                line=impl_info[2],
                level_requested=1,
                level_achieved=1,
                status=CheckStatus.PASSED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="spec_traceability",
                    finding_type="covered",
                    message=f"{req_id} is implemented and tested",
                ),),
            ))
        elif has_impl and not has_test:
            impl_info = implemented[req_id][0]
            func_results.append(FunctionResult(
                function=req_id,
                file=impl_info[0],
                line=impl_info[2],
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="spec_traceability",
                    finding_type="missing_verification",
                    message=f"{req_id} is implemented but has no test",
                    suggestion=(
                        f"Add 'Verifies: {req_id}' to a test function's docstring."
                    ),
                ),),
            ))
        elif not has_impl and has_test:
            test_info = verified[req_id][0]
            func_results.append(FunctionResult(
                function=req_id,
                file=test_info[0],
                line=test_info[2],
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="spec_traceability",
                    finding_type="missing_implementation",
                    message=f"{req_id} has a test but no implementation",
                    suggestion=(
                        f"Add 'Implements: {req_id}' to the implementing function's docstring."
                    ),
                ),),
            ))
        else:
            func_results.append(FunctionResult(
                function=req_id,
                file="SPEC.md",
                line=1,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="spec_traceability",
                    finding_type="not_covered",
                    message=f"{req_id} has no implementation and no test",
                    suggestion=(
                        f"Implement {req_id} and add 'Implements: {req_id}' "
                        f"to the function's docstring, then write a test with "
                        f"'Verifies: {req_id}'."
                    ),
                ),),
            ))

    # Check for orphan references (code/tests referencing non-existent REQs)
    orphans = all_referenced - spec_reqs
    # Loop invariant: func_results updated with orphan findings for sorted(orphans)[0..i]
    for req_id in sorted(orphans):
        locations = implemented.get(req_id, []) + verified.get(req_id, [])
        if locations:
            loc = locations[0]
            func_results.append(FunctionResult(
                function=req_id,
                file=loc[0],
                line=loc[2],
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="spec_traceability",
                    finding_type="orphan_reference",
                    message=f"{req_id} is referenced in code but not in the spec",
                    suggestion=(
                        f"Add {req_id} to SPEC.md or remove the reference."
                    ),
                ),),
            ))

    return make_check_result(
        tuple(func_results),
        level_requested=1,
        duration_seconds=0.0,
    )
