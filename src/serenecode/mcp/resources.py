"""MCP resources — read-only context the agent fetches without 'calling'.

Each function here is registered as an MCP resource at a `serenecode://`
URI by `server.py`. Resources return strings (already JSON-serialized)
because some MCP clients display resource content directly.

This module is part of the MCP composition root and is exempt from
full structural verification.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import icontract

from serenecode.adapters.local_fs import LocalFileReader
from serenecode.checker.spec_traceability import (
    extract_declared_requirement_ids,
    extract_integration_points,
)
from serenecode.config import default_config, parse_serenecode_md
from serenecode.mcp.tools import _resolve_root, get_state
from serenecode.mcp.schemas import response_to_dict
from serenecode.source_discovery import find_serenecode_md, find_spec_md


@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty JSON string",
)
def resource_config() -> str:
    """Return the active SerenecodeConfig as JSON for the current project.

    The configuration reflects the SERENECODE.md merged with the
    template defaults — i.e., the same config the verification pipeline
    would use for a CLI run.
    """
    project_root = _resolve_root(None)
    reader = LocalFileReader()
    serenecode_md = find_serenecode_md(project_root, reader)
    if serenecode_md is None:
        config = default_config()
    else:
        config = parse_serenecode_md(reader.read_file(serenecode_md))
    payload = {
        "project_root": project_root,
        "serenecode_md": serenecode_md,
        "template_name": config.template_name,
        "recommended_level": config.recommended_level,
        "contract_requirements": asdict(config.contract_requirements),
        "type_requirements": asdict(config.type_requirements),
        "architecture_rules": asdict(config.architecture_rules),
        "error_handling_rules": asdict(config.error_handling_rules),
        "loop_recursion_rules": asdict(config.loop_recursion_rules),
        "naming_conventions": asdict(config.naming_conventions),
        "exemptions": asdict(config.exemptions),
        "code_quality_rules": asdict(config.code_quality_rules),
    }
    return json.dumps(payload, indent=2, default=str)


@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty JSON string",
)
def resource_last_run() -> str:
    """Return the most recent CheckResponse from this server session.

    The cache is in-memory and resets when the server restarts.
    """
    state = get_state()
    if state.last_check is None:
        return json.dumps({"status": "no_runs_yet"}, indent=2)
    return json.dumps(response_to_dict(state.last_check), indent=2, default=str)


@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty JSON string",
)
def resource_exempt_modules() -> str:
    """Return the list of exempt path patterns for the active config."""
    project_root = _resolve_root(None)
    reader = LocalFileReader()
    serenecode_md = find_serenecode_md(project_root, reader)
    if serenecode_md is None:
        config = default_config()
    else:
        config = parse_serenecode_md(reader.read_file(serenecode_md))
    return json.dumps({
        "exempt_paths": list(config.exemptions.exempt_paths),
        "core_module_patterns": list(config.architecture_rules.core_module_patterns),
    }, indent=2)


@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty JSON string",
)
def resource_reqs() -> str:
    """Return the parsed REQ list from the project's SPEC.md.

    Looks for SPEC.md at the project root determined from the server's
    configured root (or the current working directory if none was set).
    Use the `serenecode_list_reqs` tool if you need to point at a
    specific SPEC.md file at a non-standard location.
    """
    project_root = _resolve_root(None)
    reader = LocalFileReader()
    candidate = find_spec_md(project_root, reader)
    if candidate is None:
        return json.dumps({"status": "no_spec_found", "project_root": project_root})
    content = reader.read_file(candidate)
    req_ids = sorted(extract_declared_requirement_ids(content))
    return json.dumps({
        "spec_file": candidate,
        "req_ids": req_ids,
        "count": len(req_ids),
    }, indent=2)


@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty JSON string",
)
def resource_integrations() -> str:
    """Return parsed integration-point metadata from the project's SPEC.md."""
    project_root = _resolve_root(None)
    reader = LocalFileReader()
    candidate = find_spec_md(project_root, reader)
    if candidate is None:
        return json.dumps({"status": "no_spec_found", "project_root": project_root})
    content = reader.read_file(candidate)
    integrations = extract_integration_points(content)
    return json.dumps({
        "spec_file": candidate,
        "integration_ids": [point.identifier for point in integrations],
        "integrations": [
            {
                "integration_id": point.identifier,
                "kind": point.kind,
                "source": point.source,
                "target": point.target,
                "supports": list(point.supports),
            }
            for point in integrations
        ],
        "count": len(integrations),
    }, indent=2)
