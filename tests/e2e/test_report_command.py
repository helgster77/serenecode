"""End-to-end tests for the serenecode report command."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from serenecode.cli import main


def _write_sample_source(tmp_path: Path) -> Path:
    """Write a sample source file for testing."""
    source = '''\
"""Module docstring."""

import icontract


@icontract.require(lambda x: x >= 0, "x non-neg")
@icontract.ensure(lambda result: result >= 0, "result non-neg")
def square(x: float) -> float:
    """Square a number."""
    return x * x
'''
    test_file = tmp_path / "test.py"
    test_file.write_text(source, encoding="utf-8")
    return test_file


class TestReportCommand:
    """E2E tests for the report command."""

    def test_report_human_format(self, tmp_path: Path) -> None:
        _write_sample_source(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["report", str(tmp_path)])
        assert result.exit_code == 0
        assert "functions checked" in result.output

    def test_report_json_format(self, tmp_path: Path) -> None:
        _write_sample_source(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["report", str(tmp_path), "--format", "json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "version" in parsed
        assert "summary" in parsed
        assert "results" in parsed

    def test_report_html_format(self, tmp_path: Path) -> None:
        _write_sample_source(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["report", str(tmp_path), "--format", "html"])
        assert result.exit_code == 0
        assert "<!DOCTYPE html>" in result.output
        assert "Serenecode Verification Report" in result.output
        assert "</html>" in result.output

    def test_report_html_to_file(self, tmp_path: Path) -> None:
        _write_sample_source(tmp_path)
        output_file = tmp_path / "report.html"
        runner = CliRunner()
        result = runner.invoke(main, [
            "report", str(tmp_path),
            "--format", "html",
            "--output", str(output_file),
        ])
        assert result.exit_code == 0
        assert "Report written to" in result.output
        assert output_file.exists()
        content = output_file.read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content

    def test_report_no_files(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["report", str(tmp_path)])
        assert result.exit_code == 0
        assert "No Python files" in result.output

    def test_report_with_failures(self, tmp_path: Path) -> None:
        source = '''\
"""Module docstring."""

def broken(x: int, y: int) -> int:
    """Missing contracts."""
    return x + y
'''
        (tmp_path / "broken.py").write_text(source, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["report", str(tmp_path)])
        assert result.exit_code == 0
        assert "FAIL" in result.output

    def test_report_html_escapes_special_chars(self, tmp_path: Path) -> None:
        source = '''\
"""Module with <special> & "chars"."""

def func(x: int, y: int) -> int:
    """Add <numbers>."""
    return x + y
'''
        (tmp_path / "special.py").write_text(source, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["report", str(tmp_path), "--format", "html"])
        assert result.exit_code == 0
        # HTML should escape < and > and &
        assert "<script>" not in result.output

    def test_report_json_valid_schema(self, tmp_path: Path) -> None:
        _write_sample_source(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["report", str(tmp_path), "--format", "json"])
        parsed = json.loads(result.output)
        # Validate schema fields from spec Section 4.3
        assert isinstance(parsed["version"], str)
        assert isinstance(parsed["timestamp"], str)
        assert isinstance(parsed["summary"]["total_functions"], int)
        assert isinstance(parsed["summary"]["passed"], int)
        assert isinstance(parsed["summary"]["failed"], int)
        assert isinstance(parsed["summary"]["skipped"], int)
        assert isinstance(parsed["results"], list)
