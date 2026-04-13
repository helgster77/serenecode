"""Integration tests for the FastMCP server boot in serenecode.mcp.server."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from serenecode.mcp.server import build_server, run_stdio_server
from serenecode.mcp.tools import get_state, reset_state


@pytest.fixture(autouse=True)
def _reset_mcp_state() -> None:
    """Ensure each test starts with clean MCP server state."""
    reset_state()


class TestBuildServer:
    """Tests for FastMCP server construction.

    Verifies: REQ-035
    """

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


class TestRunStdioServer:
    """Tests for run_stdio_server — covers lines 230, 234.

    The function blocks on stdin until the parent closes it, so we mock
    `build_server` to return a fake server whose `run()` is a no-op.
    """

    def test_run_calls_build_server_then_run(self, tmp_path: Path) -> None:
        """Branch (lines 230, 234): construct server then call .run()."""
        fake_server = MagicMock()
        with patch("serenecode.mcp.server.build_server", return_value=fake_server) as mock_build:
            run_stdio_server(project_root=str(tmp_path), allow_code_execution=True)
            mock_build.assert_called_once_with(
                project_root=str(tmp_path),
                allow_code_execution=True,
            )
            fake_server.run.assert_called_once()

    def test_run_defaults(self) -> None:
        fake_server = MagicMock()
        with patch("serenecode.mcp.server.build_server", return_value=fake_server) as mock_build:
            run_stdio_server()
            mock_build.assert_called_once_with(
                project_root=None,
                allow_code_execution=False,
            )
            fake_server.run.assert_called_once()
