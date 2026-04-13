"""MCP tool implementations for spec-traceability features.

Contains the spec-related tool functions (validate_spec, list_reqs,
list_integrations, req_status, integration_status, orphans) and their
shared helpers (_collect_traceability_maps, _derive_traceability_status).

Extracted from tools.py to keep each module under the file-length limit.

This module is part of the MCP composition root and is exempt from
full structural verification.
"""

from __future__ import annotations

import os

import icontract

from serenecode.adapters.local_fs import LocalFileReader
from serenecode.checker.spec_traceability import (
    extract_declared_integration_ids,
    extract_declared_requirement_ids,
    extract_implementations,
    extract_integration_points,
    extract_verifications,
    validate_spec,
)
from serenecode.core.exceptions import ConfigurationError
from serenecode.mcp.schemas import (
    response_to_dict,
    to_check_response,
)
from serenecode.mcp.tools import _resolve_root

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@icontract.require(
    lambda project_root: isinstance(project_root, str) and len(project_root) > 0,
    "project_root must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple) and len(result) == 2,
    "result must be an (implementations, verifications) pair",
)
def _collect_traceability_maps(
    project_root: str,
) -> tuple[dict[str, list[dict[str, object]]], dict[str, list[dict[str, object]]]]:
    """Collect Implements/Verifies references for every Python file under a root."""
    reader = LocalFileReader()
    files = reader.list_python_files(project_root)
    impls_by_id: dict[str, list[dict[str, object]]] = {}
    verifs_by_id: dict[str, list[dict[str, object]]] = {}

    # Loop invariant: impls_by_id and verifs_by_id contain references from files[0..i]
    for file_path in files:
        try:
            source = reader.read_file(file_path)
        except OSError:
            continue
        for func_name, found_id, line in extract_implementations(source):
            impls_by_id.setdefault(found_id, []).append(
                {"file": file_path, "function": func_name, "line": line},
            )
        for func_name, found_id, line in extract_verifications(source):
            verifs_by_id.setdefault(found_id, []).append(
                {"file": file_path, "function": func_name, "line": line},
            )

    return impls_by_id, verifs_by_id


@icontract.require(
    lambda has_impl: isinstance(has_impl, bool),
    "has_impl must be a bool",
)
@icontract.require(
    lambda has_test: isinstance(has_test, bool),
    "has_test must be a bool",
)
@icontract.ensure(
    lambda result: result in {"complete", "implemented_only", "tested_only", "orphan"},
    "result must be a recognized status string",
)
def _derive_traceability_status(has_impl: bool, has_test: bool) -> str:
    """Derive a human-readable traceability status."""
    if has_impl and has_test:
        return "complete"
    if has_impl:
        return "implemented_only"
    if has_test:
        return "tested_only"
    return "orphan"


# ---------------------------------------------------------------------------
# Tool: serenecode_validate_spec
# ---------------------------------------------------------------------------


@icontract.require(
    lambda spec_file: isinstance(spec_file, str) and len(spec_file) > 0,
    "spec_file must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "passed" in result and "spec_present" in result,
    "result must be a JSON-friendly CheckResponse dict with spec_present",
)
def tool_validate_spec(spec_file: str) -> dict[str, object]:
    """Validate a SPEC.md for SereneCode readiness.

    Args:
        spec_file: Path to the SPEC.md file.

    Returns:
        A JSON-friendly dict shaped as a CheckResponse for the spec. When the file
        is missing or unreadable, ``spec_present`` is False and ``suggested_action``
        explains how to create SPEC.md from a narrative spec.
    """
    reader = LocalFileReader()
    abs_spec = os.path.abspath(spec_file)
    try:
        content = reader.read_file(spec_file)
    except ConfigurationError:
        return {
            "passed": False,
            "level_requested": 1,
            "level_achieved": 0,
            "verdict": "failed",
            "duration_seconds": 0.0,
            "summary": {
                "passed": 0,
                "failed": 1,
                "skipped": 0,
                "exempt": 0,
                "advisory_count": 0,
            },
            "findings": [],
            "spec_present": False,
            "spec_file": abs_spec,
            "suggested_action": (
                "No readable SPEC.md at this path. If requirements live in another file "
                "(e.g. *_SPEC.md, PRD.md), convert per \"Preparing a SereneCode-Ready Spec\" "
                "in SERENECODE.md and write SPEC.md with REQ/INT identifiers and a "
                "**Source:** line."
            ),
        }
    result = validate_spec(content)
    response = to_check_response(result)
    payload = response_to_dict(response)
    payload["spec_present"] = True
    payload["spec_file"] = abs_spec
    return payload


# ---------------------------------------------------------------------------
# Tool: serenecode_list_reqs
# ---------------------------------------------------------------------------


@icontract.require(
    lambda spec_file: isinstance(spec_file, str) and len(spec_file) > 0,
    "spec_file must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "req_ids" in result and "count" in result
    and "spec_present" in result,
    "result must contain req_ids, count, and spec_present fields",
)
def tool_list_reqs(spec_file: str) -> dict[str, object]:
    """List all REQ-xxx identifiers found in a SPEC.md file.

    Args:
        spec_file: Path to the SPEC.md file.

    Returns:
        A dict with `req_ids` (sorted list of strings), `count`, and `spec_present`.
    """
    reader = LocalFileReader()
    abs_spec = os.path.abspath(spec_file)
    try:
        content = reader.read_file(spec_file)
    except ConfigurationError:
        return {
            "spec_file": abs_spec,
            "req_ids": [],
            "count": 0,
            "spec_present": False,
            "suggested_action": (
                "No readable SPEC.md at this path. If requirements live in another file "
                "(e.g. *_SPEC.md, PRD.md), convert per \"Preparing a SereneCode-Ready Spec\" "
                "in SERENECODE.md and write SPEC.md with REQ/INT identifiers and a "
                "**Source:** line."
            ),
        }
    req_ids = sorted(extract_declared_requirement_ids(content))
    return {
        "spec_file": abs_spec,
        "req_ids": req_ids,
        "count": len(req_ids),
        "spec_present": True,
    }


# ---------------------------------------------------------------------------
# Tool: serenecode_list_integrations
# ---------------------------------------------------------------------------


@icontract.require(
    lambda spec_file: isinstance(spec_file, str) and len(spec_file) > 0,
    "spec_file must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "integration_ids" in result and "count" in result
    and "spec_present" in result,
    "result must contain integration_ids, count, and spec_present fields",
)
def tool_list_integrations(spec_file: str) -> dict[str, object]:
    """List all declared INT-xxx identifiers in a SPEC.md file.

    Args:
        spec_file: Path to the SPEC.md file.

    Returns:
        A dict with `integration_ids` (sorted list of strings), `count`, and `spec_present`.
    """
    reader = LocalFileReader()
    abs_spec = os.path.abspath(spec_file)
    try:
        content = reader.read_file(spec_file)
    except ConfigurationError:
        return {
            "spec_file": abs_spec,
            "integration_ids": [],
            "count": 0,
            "spec_present": False,
            "suggested_action": (
                "No readable SPEC.md at this path. If requirements live in another file "
                "(e.g. *_SPEC.md, PRD.md), convert per \"Preparing a SereneCode-Ready Spec\" "
                "in SERENECODE.md and write SPEC.md with REQ/INT identifiers and a "
                "**Source:** line."
            ),
        }
    integration_ids = sorted(extract_declared_integration_ids(content))
    return {
        "spec_file": abs_spec,
        "integration_ids": integration_ids,
        "count": len(integration_ids),
        "spec_present": True,
    }


# ---------------------------------------------------------------------------
# Tool: serenecode_req_status
# ---------------------------------------------------------------------------


@icontract.require(
    lambda spec_file: isinstance(spec_file, str) and len(spec_file) > 0,
    "spec_file must be a non-empty string",
)
@icontract.require(
    lambda req_id: (req_id is None) or (isinstance(req_id, str) and len(req_id) > 0),
    "req_id must be None or a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "reqs" in result,
    "result must contain a 'reqs' list",
)
def tool_req_status(
    spec_file: str,
    req_id: str | None = None,
) -> dict[str, object]:
    """Report implementation and verification status for spec requirements.

    Scans every Python file under the project root that contains `spec_file`
    for `Implements: REQ-xxx` and `Verifies: REQ-xxx` references and reports
    where each requirement is implemented and tested. Source files are
    auto-discovered — you do NOT pass `src_path` or `tests_path` separately.

    Args:
        spec_file: Path to SPEC.md.
        req_id: Optional requirement identifier (e.g. "REQ-042"). When
            omitted, the response includes the status of every REQ in the
            spec. When provided, the response is filtered to that one REQ
            (and the `reqs` list will have at most one entry).

    Returns:
        A dict with:
            - `spec_file`: absolute path to the spec
            - `project_root`: where source/test files were scanned
            - `reqs`: list of {req_id, exists_in_spec, status, implementations,
              verifications} entries. `status` is one of "complete",
              "implemented_only", "tested_only", or "orphan".
    """
    project_root = _resolve_root(os.path.dirname(spec_file))
    reader = LocalFileReader()
    spec_content = reader.read_file(spec_file)
    spec_reqs = extract_declared_requirement_ids(spec_content)
    impls_by_req, verifs_by_req = _collect_traceability_maps(project_root)

    # Determine which REQ ids to report on. The union of (spec ∪ found in code)
    # so we surface code-side orphans (REQs in code that aren't in the spec) too.
    candidate_ids: set[str]
    if req_id is not None:
        candidate_ids = {req_id}
    else:
        candidate_ids = set(spec_reqs) | {
            identifier for identifier in impls_by_req if identifier.startswith("REQ-")
        } | {
            identifier for identifier in verifs_by_req if identifier.startswith("REQ-")
        }

    reqs: list[dict[str, object]] = []
    for rid in sorted(candidate_ids):
        impls = impls_by_req.get(rid, [])
        verifs = verifs_by_req.get(rid, [])
        reqs.append({
            "req_id": rid,
            "exists_in_spec": rid in spec_reqs,
            "status": _derive_traceability_status(len(impls) > 0, len(verifs) > 0),
            "implementations": impls,
            "verifications": verifs,
        })

    return {
        "spec_file": os.path.abspath(spec_file),
        "project_root": project_root,
        "reqs": reqs,
    }


# ---------------------------------------------------------------------------
# Tool: serenecode_integration_status
# ---------------------------------------------------------------------------


@icontract.require(
    lambda spec_file: isinstance(spec_file, str) and len(spec_file) > 0,
    "spec_file must be a non-empty string",
)
@icontract.require(
    lambda integration_id: (integration_id is None)
    or (isinstance(integration_id, str) and len(integration_id) > 0),
    "integration_id must be None or a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "integrations" in result,
    "result must contain an 'integrations' list",
)
def tool_integration_status(
    spec_file: str,
    integration_id: str | None = None,
) -> dict[str, object]:
    """Report implementation and verification status for declared INT items.

    Args:
        spec_file: Path to SPEC.md.
        integration_id: Optional INT identifier (for example "INT-001").

    Returns:
        A dict with:
            - `spec_file`: absolute path to the spec
            - `project_root`: where source/test files were scanned
            - `integrations`: list of entries with metadata, references, and status
    """
    reader = LocalFileReader()
    spec_content = reader.read_file(spec_file)
    declared_points = extract_integration_points(spec_content)
    declared_ids = frozenset(point.identifier for point in declared_points)
    by_id = {point.identifier: point for point in declared_points}
    project_root = _resolve_root(os.path.dirname(spec_file))
    impls_by_id, verifs_by_id = _collect_traceability_maps(project_root)

    candidate_ids: set[str]
    if integration_id is not None:
        candidate_ids = {integration_id}
    else:
        candidate_ids = set(declared_ids) | {
            identifier for identifier in impls_by_id if identifier.startswith("INT-")
        } | {
            identifier for identifier in verifs_by_id if identifier.startswith("INT-")
        }

    integrations: list[dict[str, object]] = []
    for identifier in sorted(candidate_ids):
        impls = impls_by_id.get(identifier, [])
        verifs = verifs_by_id.get(identifier, [])
        point = by_id.get(identifier)
        integrations.append({
            "integration_id": identifier,
            "exists_in_spec": identifier in declared_ids,
            "status": _derive_traceability_status(len(impls) > 0, len(verifs) > 0),
            "kind": point.kind if point is not None else None,
            "source": point.source if point is not None else None,
            "target": point.target if point is not None else None,
            "supports": list(point.supports) if point is not None else [],
            "implementations": impls,
            "verifications": verifs,
        })

    return {
        "spec_file": os.path.abspath(spec_file),
        "project_root": project_root,
        "integrations": integrations,
    }


# ---------------------------------------------------------------------------
# Tool: serenecode_orphans
# ---------------------------------------------------------------------------


@icontract.require(
    lambda spec_file: isinstance(spec_file, str) and len(spec_file) > 0,
    "spec_file must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, dict) and "unimplemented" in result and "untested" in result,
    "result must contain unimplemented and untested lists",
)
def tool_orphans(spec_file: str) -> dict[str, object]:
    """List REQs in SPEC.md that have no implementation and/or no test.

    Args:
        spec_file: Path to SPEC.md.

    Returns:
        A dict with `unimplemented` (REQs with no `Implements:` reference)
        and `untested` (REQs with no `Verifies:` reference).
    """
    reader = LocalFileReader()
    spec_content = reader.read_file(spec_file)
    spec_reqs = extract_declared_requirement_ids(spec_content)
    project_root = _resolve_root(os.path.dirname(spec_file))
    files = reader.list_python_files(project_root)

    implemented: set[str] = set()
    tested: set[str] = set()
    for f in files:
        try:
            source = reader.read_file(f)
        except OSError:
            continue
        for _func_name, req_id, _line in extract_implementations(source):
            implemented.add(req_id)
        for _func_name, req_id, _line in extract_verifications(source):
            tested.add(req_id)

    unimplemented = sorted(spec_reqs - implemented)
    untested = sorted(spec_reqs - tested)
    return {
        "unimplemented": unimplemented,
        "untested": untested,
    }
