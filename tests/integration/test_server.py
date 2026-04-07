"""Integration tests for the FastMCP server boot in serenecode.mcp.server."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from serenecode.mcp.server import build_server
from serenecode.mcp.tools import get_state, reset_state


@pytest.fixture(autouse=True)
def _reset_mcp_state() -> None:
    """Ensure each test starts with clean MCP server state."""
    reset_state()


class TestBuildServer:
    """Tests for FastMCP server construction."""

    def test_build_server_returns_fastmcp_instance(self) -> None:
        server = build_server()
        assert server.name == "serenecode"

    def test_build_server_records_project_root(self, tmp_path: Path) -> None:
        build_server(project_root=str(tmp_path))
        state = get_state()
        assert state.project_root == os.path.abspath(str(tmp_path))

    def test_build_server_records_code_execution_flag(self) -> None:
        build_server(allow_code_execution=True)
        state = get_state()
        assert state.allow_code_execution is True

    def test_build_server_defaults_to_read_only(self) -> None:
        build_server()
        state = get_state()
        assert state.allow_code_execution is False
