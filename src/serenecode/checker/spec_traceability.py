"""Spec traceability checker for Serenecode.

This module verifies that every declared spec item in a SPEC.md file
is represented in implementation and test references in the codebase.
SereneCode supports two item namespaces:

- `REQ-xxx` for behavioral requirements
- `INT-xxx` for explicit integration points

This is a core module — no I/O operations are permitted. All content is
received as strings.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass

import icontract

from serenecode.contracts.predicates import (
    is_non_empty_string,
    is_valid_int_id,
)
from serenecode.core.pipeline import SourceFile
from serenecode.models import (
    CheckResult,
    CheckStatus,
    Detail,
    FunctionResult,
    VerificationLevel,
    make_check_result,
)

_SPEC_ITEM_PATTERN = re.compile(r"\b(?:REQ|INT)-\d{3,4}\b")
_REQ_PATTERN = re.compile(r"\bREQ-\d{3,4}\b")
_INT_PATTERN = re.compile(r"\bINT-\d{3,4}\b")
_HEADING_PATTERN = re.compile(
    r"^#{1,6}\s+((?:REQ|INT)-\d{3,4})(?::\s*(.+))?$",
)
# Links narrative specs (PRD, *_SPEC.md, etc.) to this REQ/INT traceability file.
# Matches Markdown like ``**Source:** path`` where the colon is inside the bold span.
_TRACEABILITY_SOURCE_HEADER = re.compile(
    r"(?m)^\s*\*\*Sources?:\*\*\s+\S",
)
_IMPLEMENTS_PATTERN = re.compile(
    r"Implements:\s*((?:(?:REQ|INT)-\d{3,4})(?:\s*,\s*(?:REQ|INT)-\d{3,4})*)",
)
_VERIFIES_PATTERN = re.compile(
    r"Verifies:\s*((?:(?:REQ|INT)-\d{3,4})(?:\s*,\s*(?:REQ|INT)-\d{3,4})*)",
)
_FIELD_PATTERN = re.compile(r"^(Kind|Source|Target|Supports):\s*(.+?)\s*$")
_SUPPORTED_INTEGRATION_KINDS = frozenset({"call", "implements"})


@icontract.require(
    lambda spec_content: isinstance(spec_content, str),
    "spec_content must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a boolean",
)
def _has_traceability_source_header(spec_content: str) -> bool:
    """True when SPEC.md declares where narrative / upstream requirements live."""
    return _TRACEABILITY_SOURCE_HEADER.search(spec_content) is not None


@icontract.require(lambda value: isinstance(value, str), "value must be a string")
@icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
def _normalize_integration_field_text(value: str) -> str:
    """Strip whitespace and optional Markdown backticks from INT field values."""
    s = value.strip()
    if len(s) >= 2 and s.startswith("`") and s.endswith("`"):
        s = s[1:-1].strip()
    return s


@icontract.invariant(
    lambda self: is_valid_int_id(self.identifier),
    "identifier must be a valid INT id",
)
@icontract.invariant(
    lambda self: is_non_empty_string(self.description),
    "description must be non-empty",
)
@icontract.invariant(
    lambda self: self.kind in _SUPPORTED_INTEGRATION_KINDS,
    "kind must be a supported integration kind",
)
@icontract.invariant(
    lambda self: is_non_empty_string(self.source),
    "source must be non-empty",
)
@icontract.invariant(
    lambda self: is_non_empty_string(self.target),
    "target must be non-empty",
)
@icontract.invariant(
    lambda self: self.line >= 1,
    "line must be at least 1",
)
@dataclass(frozen=True)
class IntegrationPoint:
    """A declared `INT-xxx` integration point from SPEC.md."""

    identifier: str
    description: str
    kind: str
    source: str
    target: str
    line: int
    supports: tuple[str, ...] = ()


# allow-unused: public API
@icontract.require(
    lambda spec_content: isinstance(spec_content, str),
    "spec_content must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, frozenset),
    "result must be a frozenset",
)
def extract_spec_requirements(spec_content: str) -> frozenset[str]:
    """Extract all REQ-xxx identifiers mentioned anywhere in spec content.

    Args:
        spec_content: The full text of SPEC.md.

    Returns:
        Frozenset of unique requirement IDs found in the spec text.
    """
    return frozenset(_REQ_PATTERN.findall(spec_content))


@icontract.require(
    lambda spec_content: isinstance(spec_content, str),
    "spec_content must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, frozenset),
    "result must be a frozenset",
)
def extract_declared_requirement_ids(spec_content: str) -> frozenset[str]:
    """Extract all declared REQ headings from spec content.

    Args:
        spec_content: The full text of SPEC.md.

    Returns:
        Frozenset of REQ identifiers declared as headings.
    """
    return frozenset(
        identifier
        for identifier, _description, _line, _body in _parse_spec_sections(spec_content)
        if identifier.startswith("REQ-")
    )


@icontract.require(
    lambda spec_content: isinstance(spec_content, str),
    "spec_content must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, frozenset),
    "result must be a frozenset",
)
def extract_declared_integration_ids(spec_content: str) -> frozenset[str]:
    """Extract all declared INT headings from spec content.

    Args:
        spec_content: The full text of SPEC.md.

    Returns:
        Frozenset of INT identifiers declared as headings.
    """
    return frozenset(
        identifier
        for identifier, _description, _line, _body in _parse_spec_sections(spec_content)
        if identifier.startswith("INT-")
    )


@icontract.require(
    lambda spec_content: isinstance(spec_content, str),
    "spec_content must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple),
    "result must be a tuple",
)
def extract_integration_points(spec_content: str) -> tuple[IntegrationPoint, ...]:
    """Extract well-formed `INT-xxx` declarations from SPEC.md.

    Malformed integrations are skipped here; `validate_spec()` is the
    function that reports structural failures for them.

    Args:
        spec_content: The full text of SPEC.md.

    Returns:
        Tuple of parsed, well-formed integration points.
    """
    declared_reqs = extract_declared_requirement_ids(spec_content)
    integrations: list[IntegrationPoint] = []

    # Loop invariant: integrations contains all well-formed INT sections seen so far
    for identifier, description, line_no, body in _parse_spec_sections(spec_content):
        if not identifier.startswith("INT-"):
            continue
        if description is None or not description.strip():
            continue

        fields = _parse_integration_fields(body)
        kind_entry = fields.get("Kind")
        source_entry = fields.get("Source")
        target_entry = fields.get("Target")
        if kind_entry is None or source_entry is None or target_entry is None:
            continue

        kind = _normalize_integration_field_text(kind_entry[0]).lower()
        if kind not in _SUPPORTED_INTEGRATION_KINDS:
            continue

        supports: tuple[str, ...] = ()
        supports_entry = fields.get("Supports")
        if supports_entry is not None:
            parsed_supports = _parse_supports_value(supports_entry[0])
            if parsed_supports is None:
                continue
            if any(req_id not in declared_reqs for req_id in parsed_supports):
                continue
            supports = parsed_supports

        integrations.append(IntegrationPoint(
            identifier=identifier,
            description=description.strip(),
            kind=kind,
            source=_normalize_integration_field_text(source_entry[0]),
            target=_normalize_integration_field_text(target_entry[0]),
            line=line_no,
            supports=supports,
        ))

    return tuple(integrations)


@icontract.require(
    lambda spec_content: is_non_empty_string(spec_content),
    "spec_content must be a non-empty string",
)
@icontract.ensure(
    lambda result: result.level_requested == 1,
    "spec validation reports findings at the structural level",
)
def validate_spec(spec_content: str) -> CheckResult:
    """Validate that a SPEC.md is well-formed and ready for SereneCode.

    Checks:
    1. At least one REQ-xxx or INT-xxx heading exists.
    2. A traceability **Source:** line links this file to narrative specs (or states none).
    3. No duplicate IDs appear in headings.
    4. REQ and INT IDs are each sequential with no gaps.
    5. Every heading has a description.
    6. Every INT heading has required fields and valid references.

    Args:
        spec_content: The full text of SPEC.md.

    Returns:
        A CheckResult with validation findings.
    """
    sections = _parse_spec_sections(spec_content)
    func_results: list[FunctionResult] = []

    if not sections:
        func_results.append(_spec_failure(
            1, "no_requirements",
            "No REQ-xxx or INT-xxx headings found in spec",
            "Add headings like '### REQ-001: Requirement' or '### INT-001: Integration point'.",
        ))
        return make_check_result(
            tuple(func_results), level_requested=1, duration_seconds=0.0,
        )

    if not _has_traceability_source_header(spec_content):
        func_results.append(_spec_failure(
            1, "missing_traceability_source",
            "No **Source:** line found (traceability anchor for narrative specs)",
            "Add a line near the top, e.g. '**Source:** path/to/other_spec.md' when REQ/INT "
            "content is derived from a PRD or *_SPEC.md, or '**Source:** none — this SPEC.md "
            "is authoritative.' `serenecode check --spec` and REQ/INT traceability apply only to SPEC.md.",
        ))

    declared_reqs, declared_ints = _split_sections(sections)
    func_results.extend(_validate_spec_structure(sections, declared_reqs, declared_ints))

    if not func_results:
        func_results.append(_spec_valid_result(declared_reqs, declared_ints))

    return make_check_result(
        tuple(func_results), level_requested=1, duration_seconds=0.0,
    )


def _spec_failure(
    line: int, finding_type: str, message: str, suggestion: str,
) -> FunctionResult:
    """Create a standardized spec validation failure."""
    return FunctionResult(
        function="SPEC.md", file="SPEC.md", line=line,
        level_requested=1, level_achieved=0,
        status=CheckStatus.FAILED,
        details=(Detail(
            level=VerificationLevel.STRUCTURAL, tool="spec_validation",
            finding_type=finding_type, message=message, suggestion=suggestion,
        ),),
    )


def _split_sections(
    sections: tuple[tuple[str, str | None, int, tuple[tuple[str, int], ...]], ...],
) -> tuple[list[tuple[str, str | None, int]], list[tuple[str, str | None, int, tuple[tuple[str, int], ...]]]]:
    """Split parsed sections into REQ and INT groups."""
    declared_reqs = [
        (identifier, description, line_no)
        for identifier, description, line_no, _body in sections
        if identifier.startswith("REQ-")
    ]
    declared_ints = [
        (identifier, description, line_no, body)
        for identifier, description, line_no, body in sections
        if identifier.startswith("INT-")
    ]
    return declared_reqs, declared_ints


def _validate_spec_structure(
    sections: tuple[tuple[str, str | None, int, tuple[tuple[str, int], ...]], ...],
    declared_reqs: list[tuple[str, str | None, int]],
    declared_ints: list[tuple[str, str | None, int, tuple[tuple[str, int], ...]]],
) -> list[FunctionResult]:
    """Run duplicate/gap, description, and integration checks."""
    results: list[FunctionResult] = []
    results.extend(_duplicate_and_gap_findings(
        declared_reqs, prefix="REQ", duplicate_finding_type="duplicate_requirement",
    ))
    results.extend(_duplicate_and_gap_findings(
        [(i, d, l) for i, d, l, _b in declared_ints],
        prefix="INT", duplicate_finding_type="duplicate_integration",
    ))

    # Loop invariant: results contains missing-description findings for sections[0..i]
    for identifier, description, line_no, _body in sections:
        if description is None or not description.strip():
            results.append(_spec_failure(
                line_no, "missing_description",
                f"{identifier} has no description",
                f"Add a description: '### {identifier}: What this item means and why it matters'.",
            ))

    declared_req_ids = frozenset(identifier for identifier, _desc, _line in declared_reqs)
    results.extend(_integration_validation_findings(declared_ints, declared_req_ids))
    return results


def _spec_valid_result(
    declared_reqs: list[tuple[str, str | None, int]],
    declared_ints: list[tuple[str, str | None, int, tuple[tuple[str, int], ...]]],
) -> FunctionResult:
    """Create a passing result when spec is valid."""
    summary_parts: list[str] = []
    if declared_reqs:
        summary_parts.append(f"{len(declared_reqs)} requirements")
    if declared_ints:
        summary_parts.append(f"{len(declared_ints)} integration points")
    return FunctionResult(
        function="SPEC.md", file="SPEC.md", line=1,
        level_requested=1, level_achieved=1,
        status=CheckStatus.PASSED,
        details=(Detail(
            level=VerificationLevel.STRUCTURAL, tool="spec_validation",
            finding_type="valid_spec",
            message=f"Spec is valid: {', '.join(summary_parts)}",
        ),),
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
    """Extract `Implements:` references from function, method, or class docstrings.

    Args:
        source: Python source code as a string.

    Returns:
        List of `(symbol_name, spec_id, line_number)` tuples.
    """
    return _extract_docstring_references(source, _IMPLEMENTS_PATTERN)


@icontract.require(
    lambda source: isinstance(source, str),
    "source must be a string",
)
@icontract.ensure(
    lambda result: isinstance(result, list),
    "result must be a list",
)
def extract_verifications(source: str) -> list[tuple[str, str, int]]:
    """Extract `Verifies:` references from function, method, or class docstrings.

    Args:
        source: Python source code as a string.

    Returns:
        List of `(symbol_name, spec_id, line_number)` tuples.
    """
    return _extract_docstring_references(source, _VERIFIES_PATTERN)


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
    lambda result: result.level_requested == 1,
    "spec traceability reports findings at the structural level",
)
def check_spec_traceability(
    spec_content: str,
    source_files: tuple[SourceFile, ...],
    test_sources: tuple[tuple[str, str], ...],
) -> CheckResult:
    """Check that all declared spec items are implemented and tested.

    Args:
        spec_content: The full text of SPEC.md.
        source_files: Source files to scan for `Implements:` tags.
        test_sources: Tuple of `(file_path, source_content)` for test files.

    Returns:
        A CheckResult with findings for missing implementations,
        missing verifications, and orphan references.
    """
    declared_reqs = extract_declared_requirement_ids(spec_content)
    declared_ints = extract_declared_integration_ids(spec_content)
    declared_items = declared_reqs | declared_ints
    if not declared_items:
        return make_check_result((), level_requested=1, duration_seconds=0.0)

    implemented, verified = _collect_all_references(
        source_files, test_sources,
    )
    all_referenced = set(implemented.keys()) | set(verified.keys())
    func_results: list[FunctionResult] = []

    func_results.extend(_traceability_coverage_findings(
        declared_items, implemented, verified,
    ))
    func_results.extend(_orphan_reference_findings(
        all_referenced - declared_items, implemented, verified,
    ))

    return make_check_result(
        tuple(func_results), level_requested=1, duration_seconds=0.0,
    )


def _collect_all_references(
    source_files: tuple[SourceFile, ...],
    test_sources: tuple[tuple[str, str], ...],
) -> tuple[dict[str, list[tuple[str, str, int]]], dict[str, list[tuple[str, str, int]]]]:
    """Collect all Implements and Verifies references."""
    implemented = _collect_references_from_sources(source_files, extract_implementations)
    verified = _collect_references_from_test_sources(test_sources, extract_verifications)
    source_verified = _collect_references_from_sources(source_files, extract_verifications)
    # Loop invariant: verified contains merged verification refs
    for identifier, refs in source_verified.items():
        verified.setdefault(identifier, []).extend(refs)
    return implemented, verified


def _traceability_item_finding(
    identifier: str,
    impl_refs: list[tuple[str, str, int]],
    test_refs: list[tuple[str, str, int]],
) -> FunctionResult:
    """Create a traceability finding for a single declared item."""
    has_impl = len(impl_refs) > 0
    has_test = len(test_refs) > 0
    item_label = "integration point" if identifier.startswith("INT-") else "requirement"

    if has_impl and has_test:
        return FunctionResult(
            function=identifier, file=impl_refs[0][0], line=impl_refs[0][2],
            level_requested=1, level_achieved=1, status=CheckStatus.PASSED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL, tool="spec_traceability",
                finding_type="covered",
                message=f"{identifier} {item_label} is implemented and tested",
            ),),
        )
    if has_impl and not has_test:
        return FunctionResult(
            function=identifier, file=impl_refs[0][0], line=impl_refs[0][2],
            level_requested=1, level_achieved=0, status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL, tool="spec_traceability",
                finding_type="missing_verification",
                message=f"{identifier} {item_label} is implemented but has no test",
                suggestion=f"Add 'Verifies: {identifier}' to a test function or class docstring.",
            ),),
        )
    if not has_impl and has_test:
        return FunctionResult(
            function=identifier, file=test_refs[0][0], line=test_refs[0][2],
            level_requested=1, level_achieved=0, status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL, tool="spec_traceability",
                finding_type="missing_implementation",
                message=f"{identifier} {item_label} has a test but no implementation reference",
                suggestion=f"Add 'Implements: {identifier}' to the implementing symbol's docstring.",
            ),),
        )
    return FunctionResult(
        function=identifier, file="SPEC.md", line=1,
        level_requested=1, level_achieved=0, status=CheckStatus.FAILED,
        details=(Detail(
            level=VerificationLevel.STRUCTURAL, tool="spec_traceability",
            finding_type="not_covered",
            message=f"{identifier} {item_label} has no implementation and no test",
            suggestion=(
                f"Implement {identifier} and add 'Implements: {identifier}' "
                f"to the docstring, then add a test with 'Verifies: {identifier}'."
            ),
        ),),
    )


def _traceability_coverage_findings(
    declared_items: frozenset[str],
    implemented: dict[str, list[tuple[str, str, int]]],
    verified: dict[str, list[tuple[str, str, int]]],
) -> list[FunctionResult]:
    """Build coverage findings for all declared items."""
    results: list[FunctionResult] = []
    # Loop invariant: results contains findings for declared_items[0..i]
    for identifier in sorted(declared_items):
        results.append(_traceability_item_finding(
            identifier,
            implemented.get(identifier, []),
            verified.get(identifier, []),
        ))
    return results


def _orphan_reference_findings(
    orphans: set[str],
    implemented: dict[str, list[tuple[str, str, int]]],
    verified: dict[str, list[tuple[str, str, int]]],
) -> list[FunctionResult]:
    """Build findings for references not declared in the spec."""
    results: list[FunctionResult] = []
    # Loop invariant: results contains orphan findings for orphans[0..i]
    for identifier in sorted(orphans):
        locations = implemented.get(identifier, []) + verified.get(identifier, [])
        if not locations:
            continue
        loc = locations[0]
        results.append(FunctionResult(
            function=identifier, file=loc[0], line=loc[2],
            level_requested=1, level_achieved=0, status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL, tool="spec_traceability",
                finding_type="orphan_reference",
                message=f"{identifier} is referenced in code or tests but not declared in the spec",
                suggestion=f"Add {identifier} to SPEC.md or remove the reference.",
            ),),
        ))
    return results


@icontract.require(lambda spec_content: isinstance(spec_content, str), "spec_content must be a string")
@icontract.ensure(lambda result: isinstance(result, tuple), "result must be a tuple")
def _parse_spec_sections(
    spec_content: str,
) -> tuple[tuple[str, str | None, int, tuple[tuple[str, int], ...]], ...]:
    """Parse declared REQ/INT heading sections from SPEC.md text."""
    lines = spec_content.splitlines()
    sections: list[tuple[str, str | None, int, tuple[tuple[str, int], ...]]] = []
    current_id: str | None = None
    current_description: str | None = None
    current_line = 1
    current_body: list[tuple[str, int]] = []

    # Loop invariant: sections contains all completed heading sections from lines[0..i]
    for line_idx, line in enumerate(lines, start=1):
        match = _HEADING_PATTERN.match(line.strip())
        if match:
            if current_id is not None:
                sections.append((
                    current_id,
                    current_description,
                    current_line,
                    tuple(current_body),
                ))
            current_id = match.group(1)
            current_description = match.group(2)
            current_line = line_idx
            current_body = []
            continue
        if current_id is not None:
            current_body.append((line, line_idx))

    if current_id is not None:
        sections.append((
            current_id,
            current_description,
            current_line,
            tuple(current_body),
        ))

    return tuple(sections)


@icontract.require(lambda headings: isinstance(headings, list), "headings must be a list")
@icontract.require(lambda prefix: isinstance(prefix, str) and len(prefix) > 0, "prefix must be non-empty")
@icontract.require(
    lambda duplicate_finding_type: isinstance(duplicate_finding_type, str) and len(duplicate_finding_type) > 0,
    "duplicate_finding_type must be non-empty",
)
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _duplicate_and_gap_findings(
    headings: list[tuple[str, str | None, int]],
    prefix: str,
    duplicate_finding_type: str,
) -> list[FunctionResult]:
    """Build duplicate and gap findings for one heading prefix."""
    results: list[FunctionResult] = []
    seen: dict[str, int] = {}

    # Loop invariant: seen maps heading ids in headings[0..i] to their first line number
    for identifier, _description, line_no in headings:
        if identifier in seen:
            results.append(FunctionResult(
                function=identifier,
                file="SPEC.md",
                line=line_no,
                level_requested=1,
                level_achieved=0,
                status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL,
                    tool="spec_validation",
                    finding_type=duplicate_finding_type,
                    message=(
                        f"{identifier} appears on line {seen[identifier]} and line {line_no}"
                    ),
                    suggestion=f"Remove the duplicate {identifier} or renumber.",
                ),),
            ))
        else:
            seen[identifier] = line_no

    unique_ids = sorted(set(identifier for identifier, _description, _line in headings))
    if not unique_ids:
        return results

    numbers: list[int] = []
    # Loop invariant: numbers contains parsed integer suffixes for unique_ids[0..i]
    for identifier in unique_ids:
        numbers.append(int(identifier.split("-", 1)[1]))
    numbers.sort()

    if numbers:
        width = len(unique_ids[0].split("-", 1)[1])
        expected = list(range(numbers[0], numbers[0] + len(numbers)))
        missing = set(expected) - set(numbers)
        # Loop invariant: results contains gap findings for missing[0..i]
        for number in sorted(missing):
            missing_id = f"{prefix}-{number:0{width}d}"
            results.append(FunctionResult(
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

    return results


@icontract.require(lambda integrations: isinstance(integrations, list), "integrations must be a list")
@icontract.require(lambda declared_req_ids: isinstance(declared_req_ids, frozenset), "declared_req_ids must be a frozenset")
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _integration_validation_findings(
    integrations: list[tuple[str, str | None, int, tuple[tuple[str, int], ...]]],
    declared_req_ids: frozenset[str],
) -> list[FunctionResult]:
    """Build validation findings for declared INT sections."""
    results: list[FunctionResult] = []

    # Loop invariant: results contains INT validation findings for integrations[0..i]
    for identifier, _description, heading_line, body in integrations:
        fields = _parse_integration_fields(body)
        results.extend(_validate_required_fields(identifier, heading_line, fields))
        results.extend(_validate_kind_field(identifier, fields))
        results.extend(_validate_supports_field(identifier, fields, declared_req_ids))

    return results


def _validate_required_fields(
    identifier: str,
    heading_line: int,
    fields: dict[str, tuple[str, int]],
) -> list[FunctionResult]:
    """Check that Kind, Source, and Target fields are present."""
    results: list[FunctionResult] = []
    if fields.get("Kind") is None:
        results.append(_integration_field_failure(
            identifier, heading_line, "Kind",
            "Add 'Kind: call' or 'Kind: implements' below the heading.",
        ))
    if fields.get("Source") is None:
        results.append(_integration_field_failure(
            identifier, heading_line, "Source",
            "Add 'Source: Component.function' below the heading.",
        ))
    if fields.get("Target") is None:
        results.append(_integration_field_failure(
            identifier, heading_line, "Target",
            "Add 'Target: Dependency.function' below the heading.",
        ))
    return results


def _validate_kind_field(
    identifier: str,
    fields: dict[str, tuple[str, int]],
) -> list[FunctionResult]:
    """Check that the Kind field has a supported value."""
    kind_entry = fields.get("Kind")
    if kind_entry is None:
        return []
    kind_value = kind_entry[0].strip().lower()
    if kind_value in _SUPPORTED_INTEGRATION_KINDS:
        return []
    supported = ", ".join(sorted(_SUPPORTED_INTEGRATION_KINDS))
    return [FunctionResult(
        function=identifier, file="SPEC.md", line=kind_entry[1],
        level_requested=1, level_achieved=0, status=CheckStatus.FAILED,
        details=(Detail(
            level=VerificationLevel.STRUCTURAL, tool="spec_validation",
            finding_type="unsupported_integration_kind",
            message=f"{identifier} declares unsupported integration kind '{kind_entry[0].strip()}'",
            suggestion=f"Use one of the supported kinds: {supported}.",
        ),),
    )]


def _validate_supports_field(
    identifier: str,
    fields: dict[str, tuple[str, int]],
    declared_req_ids: frozenset[str],
) -> list[FunctionResult]:
    """Check that the Supports field references valid REQ ids."""
    supports_entry = fields.get("Supports")
    if supports_entry is None:
        return []
    parsed_supports = _parse_supports_value(supports_entry[0])
    if parsed_supports is None:
        return [FunctionResult(
            function=identifier, file="SPEC.md", line=supports_entry[1],
            level_requested=1, level_achieved=0, status=CheckStatus.FAILED,
            details=(Detail(
                level=VerificationLevel.STRUCTURAL, tool="spec_validation",
                finding_type="invalid_support_reference",
                message=f"{identifier} has an invalid Supports field",
                suggestion="List comma-separated REQ ids, e.g. 'Supports: REQ-001, REQ-002'.",
            ),),
        )]
    results: list[FunctionResult] = []
    # Loop invariant: results contains failures for parsed_supports[0..i] missing from declared_req_ids
    for req_id in parsed_supports:
        if req_id not in declared_req_ids:
            results.append(FunctionResult(
                function=identifier, file="SPEC.md", line=supports_entry[1],
                level_requested=1, level_achieved=0, status=CheckStatus.FAILED,
                details=(Detail(
                    level=VerificationLevel.STRUCTURAL, tool="spec_validation",
                    finding_type="invalid_support_reference",
                    message=f"{identifier} references unsupported requirement '{req_id}' in Supports",
                    suggestion=f"Declare {req_id} as a REQ heading or remove it from Supports.",
                ),),
            ))
    return results


@icontract.require(lambda identifier: isinstance(identifier, str) and len(identifier) > 0, "identifier must be non-empty")
@icontract.require(lambda line_no: isinstance(line_no, int) and line_no >= 1, "line_no must be >= 1")
@icontract.require(lambda field_name: isinstance(field_name, str) and len(field_name) > 0, "field_name must be non-empty")
@icontract.require(lambda suggestion: isinstance(suggestion, str) and len(suggestion) > 0, "suggestion must be non-empty")
@icontract.ensure(lambda result: result.file == "SPEC.md", "result must reference SPEC.md")
def _integration_field_failure(
    identifier: str,
    line_no: int,
    field_name: str,
    suggestion: str,
) -> FunctionResult:
    """Create a standardized missing-field failure for an INT section."""
    return FunctionResult(
        function=identifier,
        file="SPEC.md",
        line=line_no,
        level_requested=1,
        level_achieved=0,
        status=CheckStatus.FAILED,
        details=(Detail(
            level=VerificationLevel.STRUCTURAL,
            tool="spec_validation",
            finding_type="missing_integration_field",
            message=f"{identifier} is missing required field '{field_name}'",
            suggestion=suggestion,
        ),),
    )


@icontract.require(lambda body: isinstance(body, tuple), "body must be a tuple")
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dict")
def _parse_integration_fields(
    body: tuple[tuple[str, int], ...],
) -> dict[str, tuple[str, int]]:
    """Extract structured INT fields from a section body."""
    fields: dict[str, tuple[str, int]] = {}

    # Loop invariant: fields contains recognized field entries from body[0..i]
    for line, line_no in body:
        match = _FIELD_PATTERN.match(line.strip())
        if match:
            fields[match.group(1)] = (match.group(2), line_no)
    return fields


@icontract.require(lambda value: isinstance(value, str), "value must be a string")
@icontract.ensure(lambda result: result is None or isinstance(result, tuple), "result must be tuple or None")
def _parse_supports_value(value: str) -> tuple[str, ...] | None:
    """Parse a Supports field into REQ ids, or None if malformed."""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if not parts:
        return None

    # Loop invariant: all items already added to parsed are valid REQ ids
    for part in parts:
        if not _REQ_PATTERN.fullmatch(part):
            return None
    return tuple(parts)


@icontract.require(lambda source: isinstance(source, str), "source must be a string")
@icontract.require(lambda pattern: hasattr(pattern, "finditer"), "pattern must support finditer")
@icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
def _extract_docstring_references(
    source: str,
    pattern: re.Pattern[str],
) -> list[tuple[str, str, int]]:
    """Extract spec-item references from matching docstring tags."""
    if not source.strip():
        return []
    # silent-except: traceability is best-effort over arbitrary user sources; unparseable files yield no refs
    try:
        tree = ast.parse(source)
    except (SyntaxError, TypeError):
        return []

    results: list[tuple[str, str, int]] = []

    # Loop invariant: results contains references from nodes[0..i]
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        docstring = ast.get_docstring(node)
        if docstring is None:
            continue
        # Loop invariant: results contains refs from pattern matches in this docstring[0..j]
        for match in pattern.finditer(docstring):
            identifiers = _SPEC_ITEM_PATTERN.findall(match.group(0))
            for identifier in identifiers:
                results.append((node.name, identifier, node.lineno))

    return results


@icontract.require(lambda source_files: isinstance(source_files, tuple), "source_files must be a tuple")
@icontract.require(lambda extractor: callable(extractor), "extractor must be callable")
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dict")
def _collect_references_from_sources(
    source_files: tuple[SourceFile, ...],
    extractor: Callable[[str], list[tuple[str, str, int]]],
) -> dict[str, list[tuple[str, str, int]]]:
    """Collect traceability references from SourceFile tuples."""
    collected: dict[str, list[tuple[str, str, int]]] = {}

    # Loop invariant: collected contains all references from source_files[0..i]
    for source_file in source_files:
        refs = extractor(source_file.source)
        for symbol_name, identifier, line_no in refs:
            collected.setdefault(identifier, []).append((
                source_file.file_path,
                symbol_name,
                line_no,
            ))

    return collected


@icontract.require(lambda test_sources: isinstance(test_sources, tuple), "test_sources must be a tuple")
@icontract.require(lambda extractor: callable(extractor), "extractor must be callable")
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dict")
def _collect_references_from_test_sources(
    test_sources: tuple[tuple[str, str], ...],
    extractor: Callable[[str], list[tuple[str, str, int]]],
) -> dict[str, list[tuple[str, str, int]]]:
    """Collect traceability references from raw `(path, source)` tuples."""
    collected: dict[str, list[tuple[str, str, int]]] = {}

    # Loop invariant: collected contains all references from test_sources[0..i]
    for test_path, test_source in test_sources:
        refs = extractor(test_source)
        for symbol_name, identifier, line_no in refs:
            collected.setdefault(identifier, []).append((
                test_path,
                symbol_name,
                line_no,
            ))

    return collected
