"""End-to-end tests for the serenecode check command."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from serenecode.cli import main


class TestCheckCommand:
    """E2E tests for the check command."""

    def test_check_valid_file_passes(self, tmp_path: Path) -> None:
        source = '''\
"""Module docstring."""

import icontract


@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result: result >= 0, "result must be non-negative")
def square(x: float) -> float:
    """Square a number."""
    return x * x
'''
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["check", str(tmp_path / "test.py")])
        assert result.exit_code == 0
        assert "PASSED" in result.output

    def test_check_missing_contracts_fails(self, tmp_path: Path) -> None:
        source = '''\
"""Module docstring."""


def add(x: int, y: int) -> int:
    """Add numbers."""
    return x + y
'''
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["check", str(tmp_path / "test.py")])
        assert result.exit_code == 1
        assert "FAILED" in result.output

    def test_check_json_format(self, tmp_path: Path) -> None:
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
        result = runner.invoke(main, ["check", str(tmp_path / "test.py"), "--format", "json", "--level", "1"])
        assert result.exit_code == 0
        # CliRunner mixes stderr progress lines with stdout JSON;
        # extract the JSON object between first '{' and last '}'
        output = result.output
        json_start = output.index("{")
        json_end = output.rindex("}") + 1
        parsed = json.loads(output[json_start:json_end])
        assert "version" in parsed
        assert "summary" in parsed
        assert "results" in parsed

    def test_check_no_files(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["check", str(tmp_path)])
        assert result.exit_code == 0
        assert "No Python files" in result.output

    def test_check_structural_flag(self, tmp_path: Path) -> None:
        source = '''\
"""Module docstring."""

def add(x: int, y: int) -> int:
    """Add numbers."""
    return x + y
'''
        (tmp_path / "test.py").write_text(source, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["check", str(tmp_path / "test.py"), "--structural"])
        assert result.exit_code == 1

    def test_check_directory(self, tmp_path: Path) -> None:
        sub = tmp_path / "src"
        sub.mkdir()
        source = '''\
"""Module docstring."""

import icontract


@icontract.require(lambda x: x > 0, "x positive")
@icontract.ensure(lambda result: result > 0, "result positive")
def double(x: int) -> int:
    """Double a number."""
    return x * 2
'''
        (tmp_path / "src" / "test.py").write_text(source, encoding="utf-8")
        runner = CliRunner()
        result = runner.invoke(main, ["check", str(sub)])
        assert result.exit_code == 0
