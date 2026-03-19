"""End-to-end tests for the serenecode status command."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from serenecode.cli import main


class TestStatusCommand:
    """E2E tests for the status command."""

    def test_status_valid_file(self, tmp_path: Path) -> None:
        source = '''\
"""Module docstring."""

import icontract


@icontract.require(lambda x: x >= 0, "x non-neg")
@icontract.ensure(lambda result: result >= 0, "result non-neg")
def square(x: float) -> float:
    """Square a number."""
    return x * x
'''
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["status", str(tmp_path / "test.py")])
        assert result.exit_code == 0
        assert "PASSED" in result.output or "functions checked" in result.output

    def test_status_failing_file(self, tmp_path: Path) -> None:
        source = '''\
"""Module docstring."""

def add(x: int, y: int) -> int:
    """Add numbers."""
    return x + y
'''
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["status", str(tmp_path / "test.py")])
        assert result.exit_code == 0  # status doesn't set exit codes
        assert "FAIL" in result.output

    def test_status_no_files(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["status", str(tmp_path)])
        assert result.exit_code == 0
        assert "No Python files" in result.output

    def test_status_json_format(self, tmp_path: Path) -> None:
        source = '''\
"""Module docstring."""

import icontract


@icontract.require(lambda x: x >= 0, "x non-neg")
@icontract.ensure(lambda result: result >= 0, "result non-neg")
def square(x: float) -> float:
    """Square a number."""
    return x * x
'''
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["status", str(tmp_path / "test.py"), "--format", "json"])
        assert result.exit_code == 0
        parsed = json.loads(result.output)
        assert "version" in parsed
        assert "summary" in parsed

    def test_status_directory(self, tmp_path: Path) -> None:
        sub = tmp_path / "src"
        sub.mkdir()
        source = '"""Module doc."""\n'
        (sub / "a.py").write_text(source, encoding="utf-8")
        (sub / "b.py").write_text(source, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["status", str(sub)])
        assert result.exit_code == 0
