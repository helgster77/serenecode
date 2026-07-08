"""Coverage-gap E2E tests for the serenecode doctor and status commands."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import serenecode.cli as cli_module
from serenecode.cli import main
from serenecode.models import ExitCode

PASSING_SOURCE = '''\
"""Module docstring."""

import icontract


@icontract.require(lambda x: x >= 0, "x non-neg")
@icontract.ensure(lambda result: result >= 0, "result non-neg")
def square(x: float) -> float:
    """Square a number."""
    return x * x
'''


class TestDoctorCommand:
    """E2E tests for the doctor command."""

    def test_doctor_with_mcp_installed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli_module, "_mcp_extra_installed", lambda: True)
        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "Serenecode doctor" in result.output
        assert "OK: MCP Python package is available" in result.output
        assert "claude mcp add serenecode" in result.output
        assert "Spec status (project root):" in result.output
        assert "SPEC.md (traceability): not found" in result.output
        assert "Narrative / source-like files at root: none detected" in result.output

    def test_doctor_without_mcp_extra(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli_module, "_mcp_extra_installed", lambda: False)
        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "NOT FOUND: MCP extra is not installed" in result.output
        assert "pip install 'serenecode[mcp]'" in result.output

    def test_doctor_reports_spec_and_narrative_files(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "SERENECODE.md").write_text("# SERENECODE\n", encoding="utf-8")
        (tmp_path / "SPEC.md").write_text("## REQ-001\n", encoding="utf-8")
        (tmp_path / "FEATURE_SPEC.md").write_text("narrative\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(cli_module, "_mcp_extra_installed", lambda: True)
        runner = CliRunner()
        result = runner.invoke(main, ["doctor"])
        assert result.exit_code == 0
        assert "SPEC.md (traceability):" in result.output
        assert "not found" not in result.output
        assert "FEATURE_SPEC.md" in result.output


class TestStatusCommand:
    """E2E tests for status command error and config branches."""

    def test_status_uses_serenecode_md_when_present(self, tmp_path: Path) -> None:
        (tmp_path / "SERENECODE.md").write_text(
            "# SERENECODE\n\nTemplate: default\n", encoding="utf-8"
        )
        (tmp_path / "good.py").write_text(PASSING_SOURCE, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["status", str(tmp_path)])
        assert result.exit_code == 0
        # No default-config warning is expected when SERENECODE.md is found.
        assert "No SERENECODE.md found" not in result.output

    def test_status_build_source_files_error_exits_internal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "good.py").write_text(PASSING_SOURCE, encoding="utf-8")

        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("cannot build source files")

        monkeypatch.setattr(cli_module, "build_source_files", boom)
        runner = CliRunner()
        result = runner.invoke(main, ["status", str(tmp_path)])
        assert result.exit_code == ExitCode.INTERNAL
        assert "Error: cannot build source files" in result.output

    def test_status_spec_read_error_exits_internal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / "good.py").write_text(PASSING_SOURCE, encoding="utf-8")

        def boom(*args: object, **kwargs: object) -> object:
            raise RuntimeError("spec is unreadable")

        monkeypatch.setattr(cli_module, "_load_spec_inputs", boom)
        runner = CliRunner()
        result = runner.invoke(main, ["status", str(tmp_path)])
        assert result.exit_code == ExitCode.INTERNAL
        assert "Error reading spec: spec is unreadable" in result.output
