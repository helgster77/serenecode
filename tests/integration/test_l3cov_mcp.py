"""Coverage-gap tests for MCP tools, spec tools, and resources.

Targets:
- ``mcp.tools._wire_adapters`` — the except-ImportError fallbacks.
- ``mcp.tools._format_health_response`` — the "error" and "warning" statuses.
- ``mcp.tools_spec.tool_list_integrations`` — the unreadable-spec branch.
- ``mcp.tools_spec.tool_integration_status`` — the no-filter (all INTs) branch.
- ``mcp.resources.resource_exempt_modules`` — SERENECODE.md-present branch.
- ``mcp.resources.resource_integrations`` — no-SPEC.md branch.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

import pytest

from serenecode.config import ModuleHealthConfig
from serenecode.mcp.resources import resource_exempt_modules, resource_integrations
from serenecode.mcp.server import build_server
from serenecode.mcp.tools import _format_health_response, _wire_adapters, reset_state
from serenecode.mcp.tools_spec import tool_integration_status, tool_list_integrations


@pytest.fixture(autouse=True)
def _reset_mcp_state() -> None:
    """Ensure each test starts with clean MCP server state."""
    reset_state()


_ADAPTER_MODULES = (
    "serenecode.adapters.mypy_adapter",
    "serenecode.adapters.coverage_adapter",
    "serenecode.adapters.hypothesis_adapter",
    "serenecode.adapters.crosshair_adapter",
    "serenecode.adapters.vulture_adapter",
)


class TestWireAdaptersImportFailures:
    """Cover the except-ImportError branches of _wire_adapters."""

    def test_all_adapter_imports_failing_yields_none_handles(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Setting sys.modules entries to None forces ImportError on import."""
        for module_name in _ADAPTER_MODULES:
            monkeypatch.setitem(sys.modules, module_name, None)
        adapters = _wire_adapters(5)
        assert adapters["type_checker"] is None
        assert adapters["coverage_analyzer"] is None
        assert adapters["property_tester"] is None
        assert adapters["symbolic_checker"] is None

    def test_vulture_import_failure_falls_back_to_unavailable_analyzer(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from serenecode.adapters.unavailable_dead_code_adapter import (
            UnavailableDeadCodeAnalyzer,
        )
        monkeypatch.setitem(
            sys.modules, "serenecode.adapters.vulture_adapter", None,
        )
        adapters = _wire_adapters(1)
        assert isinstance(adapters["dead_code_analyzer"], UnavailableDeadCodeAnalyzer)


def _make_metrics(
    func_lines: int, param_count: int, method_count: int,
) -> dict[str, object]:
    return {
        "function_count": 1,
        "class_count": 1,
        "largest_function": {"name": "f", "lines": func_lines, "line": 1},
        "max_parameters": {"name": "f", "count": param_count, "line": 1},
        "largest_class": {"name": "C", "method_count": method_count, "line": 1},
    }


def _make_health_config() -> ModuleHealthConfig:
    return ModuleHealthConfig(
        enabled=True,
        file_length_warn=10, file_length_error=20,
        function_length_warn=10, function_length_error=20,
        parameter_count_warn=3, parameter_count_error=6,
        class_method_count_warn=5, class_method_count_error=10,
    )


class TestFormatHealthResponseStatuses:
    """Cover the "error" and "warning" branches of the nested _status."""

    def test_value_above_error_threshold_reports_error(self) -> None:
        response = _format_health_response(
            "/abs/mod.py", 25, _make_metrics(25, 7, 11),
            _make_health_config(), [],
        )
        status = response["status"]
        assert status["file_length"] == "error"
        assert status["function_length"] == "error"
        assert status["parameter_count"] == "error"
        assert status["class_method_count"] == "error"

    def test_value_between_warn_and_error_reports_warning(self) -> None:
        response = _format_health_response(
            "/abs/mod.py", 15, _make_metrics(15, 4, 7),
            _make_health_config(), [],
        )
        status = response["status"]
        assert status["file_length"] == "warning"
        assert status["function_length"] == "warning"
        assert status["parameter_count"] == "warning"
        assert status["class_method_count"] == "warning"

    def test_value_at_or_below_warn_reports_ok(self) -> None:
        response = _format_health_response(
            "/abs/mod.py", 5, _make_metrics(5, 2, 3),
            _make_health_config(), [],
        )
        status = response["status"]
        assert status["file_length"] == "ok"
        assert status["function_length"] == "ok"
        assert status["parameter_count"] == "ok"
        assert status["class_method_count"] == "ok"


class TestToolListIntegrations:
    """Cover the unreadable-spec branch of tool_list_integrations."""

    def test_missing_spec_reports_not_present(self, tmp_path: Path) -> None:
        result = tool_list_integrations(str(tmp_path / "SPEC.md"))
        assert result["spec_present"] is False
        assert result["integration_ids"] == []
        assert result["count"] == 0
        assert "suggested_action" in result


class TestToolIntegrationStatus:
    """Cover the no-filter branch of tool_integration_status."""

    def test_without_integration_id_reports_all_declared_ints(
        self, tmp_path: Path,
    ) -> None:
        spec = tmp_path / "SPEC.md"
        spec.write_text(textwrap.dedent("""\
            # Spec

            **Source:** none — test fixture.

            ### REQ-001: One
            Desc.

            ### INT-001: One integration
            Kind: call
            Source: service.run
            Target: gateway.send
            Supports: REQ-001

            ### INT-002: Another integration
            Kind: call
            Source: service.stop
            Target: gateway.close
            Supports: REQ-001
        """), encoding="utf-8")
        result = tool_integration_status(str(spec))
        ids = [entry["integration_id"] for entry in result["integrations"]]
        assert ids == ["INT-001", "INT-002"]
        assert all(entry["exists_in_spec"] for entry in result["integrations"])
        assert result["integrations"][0]["kind"] == "call"


class TestResourcesBranches:
    """Cover the remaining branches in mcp/resources.py."""

    def test_exempt_modules_parses_serenecode_md_when_present(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "SERENECODE.md").write_text(
            "Template: minimal\n", encoding="utf-8",
        )
        build_server(project_root=str(tmp_path))
        text = resource_exempt_modules()
        data = json.loads(text)
        assert isinstance(data["exempt_paths"], list)
        assert isinstance(data["core_module_patterns"], list)

    def test_integrations_without_spec_reports_no_spec_found(
        self, tmp_path: Path,
    ) -> None:
        build_server(project_root=str(tmp_path))
        text = resource_integrations()
        data = json.loads(text)
        assert data["status"] == "no_spec_found"
        assert data["project_root"] == str(tmp_path)
