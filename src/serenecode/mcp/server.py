"""FastMCP server boot for Serenecode.

Wires the tool functions in `tools.py` and the resource readers in
`resources.py` to a FastMCP server. The server runs over stdio so it
can be registered with any MCP-speaking AI tool (Claude Code, Cursor,
Cline, etc.) using a single `claude mcp add` (or equivalent) command.

Per-call code-execution gating mirrors the CLI: the server boots in
read-only mode unless `--allow-code-execution` is passed, in which case
Levels 3-6 tools become callable.

This module is part of the MCP composition root and is exempt from
full structural verification.
"""

from __future__ import annotations

import os
from typing import Any, cast

import icontract

try:
    from mcp.server.fastmcp import FastMCP
except ImportError as exc:  # pragma: no cover - import-time gate
    raise ImportError(
        "The 'mcp' package is not installed. Install with: "
        "pip install 'serenecode[mcp]' or uv add 'mcp>=1.0'",
    ) from exc

from serenecode.mcp.resources import (
    resource_config,
    resource_exempt_modules,
    resource_integrations,
    resource_last_run,
    resource_reqs,
)
from serenecode.mcp.tools import (
    get_state,
    tool_check,
    tool_check_file,
    tool_check_function,
    tool_dead_code,
    tool_integration_status,
    tool_list_integrations,
    tool_list_reqs,
    tool_orphans,
    tool_req_status,
    tool_suggest_contracts,
    tool_suggest_test,
    tool_uncovered,
    tool_validate_spec,
    tool_verify_fixed,
)

_SERVER_NAME = "serenecode"
_INSTRUCTIONS = (
    "PREFERRED WORKFLOW: While editing, use serenecode_check_function (or "
    "serenecode_check_file) on the code you just changed — not a full-project "
    "serenecode_check on every turn. Reserve serenecode_check for CI, release "
    "gates, or intentional whole-tree runs. "
    "Call serenecode_check_function on every function you write or edit to "
    "verify contracts, types, coverage, and architectural conventions. Use "
    "serenecode_suggest_contracts to derive icontract decorators from a function "
    "signature, serenecode_validate_spec plus serenecode_req_status / "
    "serenecode_integration_status for spec traceability, serenecode_dead_code "
    "for likely unused code review, and serenecode_uncovered to find missing "
    "test coverage. "
    "Levels 3-6 require --allow-code-execution at startup; that flag allows "
    "importing and running project code (same trust as pytest) — not a sandbox. "
    "Project paths passed to tools are resolved on the host filesystem and are "
    "not confined to a single workspace unless your client enforces that. "
    "See docs/SECURITY.md in the Serenecode repository for the full trust model."
)


@icontract.require(
    lambda project_root: project_root is None or isinstance(project_root, str),
    "project_root must be None or a string",
)
@icontract.require(
    lambda allow_code_execution: isinstance(allow_code_execution, bool),
    "allow_code_execution must be a bool",
)
@icontract.ensure(
    lambda result: result.name == "serenecode",
    "server must be named 'serenecode'",
)
def build_server(
    project_root: str | None = None,
    allow_code_execution: bool = False,
) -> FastMCP[Any]:
    """Construct a FastMCP server with all serenecode tools and resources registered.

    Args:
        project_root: Default project root used when a tool call doesn't
            include a path. May be None.
        allow_code_execution: If True, Levels 3-6 tools may import and
            execute project modules. Defaults to False (read-only mode).

    Returns:
        A configured FastMCP instance ready for `.run()`.
    """
    state = get_state()
    state.project_root = (
        os.path.abspath(project_root) if project_root else None
    )
    state.allow_code_execution = allow_code_execution

    server = FastMCP(name=_SERVER_NAME, instructions=_INSTRUCTIONS)

    # Tools — verification core
    server.tool(
        name="serenecode_check",
        description=(
            "Run the verification pipeline on an entire project root (CI / batch). "
            "Prefer serenecode_check_function or serenecode_check_file during "
            "interactive editing — full-tree checks are slower and noisier. "
            "Returns findings, summary counts, and overall pass/fail. "
            "Levels 3-6 require --allow-code-execution at server startup."
        ),
    )(cast(Any, tool_check))
    server.tool(
        name="serenecode_check_file",
        description=(
            "Run the verification pipeline scoped to a single source file. "
            "Prefer this over serenecode_check during editing; faster than a "
            "full-project run."
        ),
    )(cast(Any, tool_check_file))
    server.tool(
        name="serenecode_check_function",
        description=(
            "PRIMARY TOOL FOR EDITING: run the pipeline on one function in one file. "
            "Use after each edit instead of serenecode_check (full tree). "
            "Validates contracts, types, coverage, and conventions for that symbol only."
        ),
    )(cast(Any, tool_check_function))
    server.tool(
        name="serenecode_verify_fixed",
        description=(
            "Re-run the verification on one function and report whether a "
            "specific finding (matched by message substring) is gone. Use "
            "after editing to confirm a fix without re-running the full pipeline."
        ),
    )(cast(Any, tool_verify_fixed))

    # Tools — authoring helpers
    server.tool(
        name="serenecode_suggest_contracts",
        description=(
            "Suggest icontract @require/@ensure decorators for a function "
            "based on its signature and the structural checker's recommendations."
        ),
    )(cast(Any, tool_suggest_contracts))
    server.tool(
        name="serenecode_uncovered",
        description=(
            "Report Level 3 coverage findings for a single function — which "
            "lines and branches are uncovered, plus mock-classification suggestions. "
            "Requires --allow-code-execution."
        ),
    )(cast(Any, tool_uncovered))
    server.tool(
        name="serenecode_suggest_test",
        description=(
            "Return any test scaffold suggestions the coverage adapter "
            "generated for a function. Requires --allow-code-execution."
        ),
    )(cast(Any, tool_suggest_test))

    # Tools — spec / REQ traceability
    server.tool(
        name="serenecode_validate_spec",
        description=(
            "Validate a SPEC.md for SereneCode readiness: REQ-xxx ids present, "
            "**Source:** line, no duplicates, no gaps, descriptions on all requirements. "
            "If the file is missing or unreadable, spec_present is false and "
            "suggested_action explains converting a narrative spec to SPEC.md."
        ),
    )(cast(Any, tool_validate_spec))
    server.tool(
        name="serenecode_list_reqs",
        description=(
            "List all REQ-xxx identifiers in a SPEC.md. When the file is missing, "
            "spec_present is false and suggested_action explains next steps."
        ),
    )(cast(Any, tool_list_reqs))
    server.tool(
        name="serenecode_list_integrations",
        description=(
            "List all declared INT-xxx integration identifiers in a SPEC.md. "
            "When the file is missing, spec_present is false and suggested_action "
            "explains next steps."
        ),
    )(cast(Any, tool_list_integrations))
    server.tool(
        name="serenecode_req_status",
        description=(
            "Report implementation and verification status for one REQ: "
            "which functions implement it, which tests verify it, and the "
            "derived status (complete | implemented_only | tested_only | orphan)."
        ),
    )(cast(Any, tool_req_status))
    server.tool(
        name="serenecode_integration_status",
        description=(
            "Report implementation and verification status for one INT: "
            "integration metadata, implementing symbols, verifying tests, "
            "and the derived status."
        ),
    )(cast(Any, tool_integration_status))
    server.tool(
        name="serenecode_orphans",
        description=(
            "List REQs in SPEC.md that have no `Implements:` reference "
            "(unimplemented) or no `Verifies:` reference (untested)."
        ),
    )(cast(Any, tool_orphans))
    server.tool(
        name="serenecode_dead_code",
        description=(
            "Return likely dead-code findings for a path, with guidance to ask "
            "the user before removing or allowlisting code."
        ),
    )(cast(Any, tool_dead_code))

    # Resources — read-only context
    server.resource(
        "serenecode://config",
        description=(
            "Active SerenecodeConfig as JSON: contracts, types, architecture, "
            "exemptions, and code quality rules merged from SERENECODE.md."
        ),
        mime_type="application/json",
    )(cast(Any, resource_config))
    server.resource(
        "serenecode://findings/last-run",
        description="The most recent CheckResponse from this server session.",
        mime_type="application/json",
    )(cast(Any, resource_last_run))
    server.resource(
        "serenecode://exempt-modules",
        description="The exempt path patterns and core module patterns for the active config.",
        mime_type="application/json",
    )(cast(Any, resource_exempt_modules))
    server.resource(
        "serenecode://reqs",
        description="Parsed REQ-xxx list from the project's SPEC.md if present.",
        mime_type="application/json",
    )(cast(Any, resource_reqs))
    server.resource(
        "serenecode://integrations",
        description="Parsed INT-xxx integration metadata from the project's SPEC.md if present.",
        mime_type="application/json",
    )(cast(Any, resource_integrations))

    return server


@icontract.require(
    lambda project_root: project_root is None or isinstance(project_root, str),
    "project_root must be None or a string",
)
@icontract.require(
    lambda allow_code_execution: isinstance(allow_code_execution, bool),
    "allow_code_execution must be a bool",
)
@icontract.ensure(lambda result: result is None, "no return value (blocks until stdin closes)")
def run_stdio_server(
    project_root: str | None = None,
    allow_code_execution: bool = False,
) -> None:
    """Build and run the Serenecode MCP server over stdio.

    Blocks until the parent process closes stdin. Designed to be invoked
    by `serenecode mcp` from the CLI.
    """
    server = build_server(
        project_root=project_root,
        allow_code_execution=allow_code_execution,
    )
    server.run()  # FastMCP defaults to stdio transport
