"""MCP server entry point for Serenecode.

Exposes the Serenecode verification pipeline as MCP tools an AI agent
can call mid-edit. This is a composition root — it wires the MCP
protocol layer to the existing core/checker/spec_traceability code.

The package is exempt from full structural verification because it is
adapter/protocol-shim code. It is integration-tested via direct calls
to its tool handlers and an end-to-end CLI smoke test.

Submodules `server`, `tools`, `resources`, and `schemas` are imported
explicitly by callers (the CLI subcommand and tests) rather than
re-exported from this `__init__.py`. The eager-re-export pattern
caused a circular import when `serenecode.adapters.module_loader`
loaded `serenecode.mcp.tools` directly: tools.py imports `mcp.schemas`,
which triggers `mcp/__init__.py`, which used to re-import `mcp.server`,
which imports `mcp.resources`, which re-imports `mcp.tools` mid-load.
Keep this `__init__.py` empty.
"""

from __future__ import annotations

__all__: list[str] = []
