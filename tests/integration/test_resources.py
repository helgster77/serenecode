"""Integration tests for the read-only MCP resources in serenecode.mcp.resources."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from serenecode.mcp.resources import (
    resource_config,
    resource_exempt_modules,
    resource_integrations,
    resource_last_run,
    resource_reqs,
)
from serenecode.mcp.server import build_server
from serenecode.mcp.tools import reset_state, tool_check


@pytest.fixture(autouse=True)
def _reset_mcp_state() -> None:
    """Ensure each test starts with clean MCP server state."""
    reset_state()


class TestResources:
    """Tests for serenecode://config / findings/last-run / exempt-modules / reqs."""

    def test_config_resource_returns_json(self, tmp_path: Path) -> None:
        # Need a real project context — write a minimal SERENECODE.md
        (tmp_path / "SERENECODE.md").write_text("Template: default\n", encoding="utf-8")
        build_server(project_root=str(tmp_path))
        text = resource_config()
        data = json.loads(text)
        assert data["template_name"] == "default"
        assert "code_quality_rules" in data

    def test_last_run_empty_state(self) -> None:
        reset_state()
        text = resource_last_run()
        data = json.loads(text)
        assert data["status"] == "no_runs_yet"

    def test_last_run_after_check(self, tmp_path: Path) -> None:
        (tmp_path / "m.py").write_text('"""Doc."""\n', encoding="utf-8")
        tool_check(path=str(tmp_path), level=1)
        text = resource_last_run()
        data = json.loads(text)
        assert "passed" in data
        assert "summary" in data

    def test_exempt_modules_returns_list(self) -> None:
        reset_state()
        text = resource_exempt_modules()
        data = json.loads(text)
        assert "exempt_paths" in data
        assert isinstance(data["exempt_paths"], list)

    def test_reqs_no_spec_found(self, tmp_path: Path) -> None:
        build_server(project_root=str(tmp_path))
        text = resource_reqs()
        data = json.loads(text)
        assert data["status"] == "no_spec_found"

    def test_reqs_with_spec_present(self, tmp_path: Path) -> None:
        (tmp_path / "SPEC.md").write_text(textwrap.dedent("""\
            # Spec

            **Source:** none — test fixture.

            ### REQ-001: One
            Desc.
        """), encoding="utf-8")
        build_server(project_root=str(tmp_path))
        text = resource_reqs()
        data = json.loads(text)
        assert data["count"] == 1
        assert data["req_ids"] == ["REQ-001"]

    def test_integrations_with_spec_present(self, tmp_path: Path) -> None:
        (tmp_path / "SPEC.md").write_text(textwrap.dedent("""\
            # Spec

            **Source:** none — test fixture.

            ### REQ-001: One
            Desc.

            ### INT-001: One integration
            Kind: call
            Source: service.run
            Target: gateway.send
            Supports: REQ-001
        """), encoding="utf-8")
        build_server(project_root=str(tmp_path))
        text = resource_integrations()
        data = json.loads(text)
        assert data["count"] == 1
        assert data["integration_ids"] == ["INT-001"]
        assert data["integrations"][0]["kind"] == "call"

    def test_config_resource_no_serenecode_md_uses_default(self, tmp_path: Path) -> None:
        """Branch (line 41): no SERENECODE.md → fall back to default_config()."""
        # tmp_path has no SERENECODE.md anywhere up the tree
        build_server(project_root=str(tmp_path))
        text = resource_config()
        data = json.loads(text)
        assert data["template_name"] == "default"
        # Confirm it didn't try to parse a missing file
        assert data["serenecode_md"] is None

    def test_exempt_modules_no_serenecode_md_uses_default(self, tmp_path: Path) -> None:
        """Branch (line 86): no SERENECODE.md → fall back to default_config()."""
        build_server(project_root=str(tmp_path))
        text = resource_exempt_modules()
        data = json.loads(text)
        assert "exempt_paths" in data
        # Default config has the standard exempt paths
        assert "cli.py" in data["exempt_paths"]
