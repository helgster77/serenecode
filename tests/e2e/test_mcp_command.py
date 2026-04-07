"""End-to-end tests for the `serenecode mcp` CLI subcommand.

These tests verify that the subcommand registers correctly and can be
invoked. The full stdio MCP transport is not exercised here (that
requires an MCP client); see tests/integration/test_mcp_server.py for
direct tool-handler tests.
"""

from __future__ import annotations

from click.testing import CliRunner

from serenecode.cli import main


class TestMcpCommand:
    """Tests for the `serenecode mcp` CLI subcommand."""

    def test_help_lists_subcommand(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "mcp" in result.output

    def test_mcp_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "MCP server" in result.output
        assert "--allow-code-execution" in result.output
        assert "--project-root" in result.output

    def test_mcp_help_mentions_stdio(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "stdio" in result.output.lower()

    def test_mcp_help_mentions_claude_mcp_add(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["mcp", "--help"])
        assert result.exit_code == 0
        assert "claude mcp add" in result.output
