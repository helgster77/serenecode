"""End-to-end tests for the serenecode check command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from serenecode.cli import main
from serenecode.models import make_check_result


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
        result = runner.invoke(main, ["check", str(tmp_path / "test.py"), "--level", "1"])
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
        result = runner.invoke(main, ["check", str(tmp_path / "test.py"), "--level", "1"])
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
        result = runner.invoke(main, ["check", str(tmp_path), "--level", "1"])
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
        result = runner.invoke(main, ["check", str(sub), "--level", "1"])
        assert result.exit_code == 0

    def test_verify_flag_starts_at_level_3(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "test.py").write_text('"""Module doc."""\n', encoding="utf-8")
        captured: dict[str, int] = {}

        def fake_run_pipeline(*args: object, **kwargs: object):
            captured["start_level"] = kwargs["start_level"]  # type: ignore[index]
            captured["level"] = kwargs["level"]  # type: ignore[index]
            return make_check_result((), level_requested=3, duration_seconds=0.0)

        monkeypatch.setattr("serenecode.cli.run_pipeline", fake_run_pipeline)

        runner = CliRunner()
        result = runner.invoke(main, ["check", str(tmp_path), "--verify", "--allow-code-execution"])

        assert result.exit_code == 0
        assert captured == {"start_level": 3, "level": 3}

    def test_src_layout_with_package_imports_passes_level_3(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        src_pkg = tmp_path / "src" / "pkg"
        src_pkg.mkdir(parents=True)
        (src_pkg / "__init__.py").write_text("", encoding="utf-8")
        (src_pkg / "helper.py").write_text(
            '''\
"""Helper module."""

import icontract


@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result, x: result == x * 2, "must double")
def double(x: int) -> int:
    """Double a non-negative integer."""
    return x * 2
''',
            encoding="utf-8",
        )
        (src_pkg / "mod.py").write_text(
            '''\
"""Wrapper module."""

import icontract
from pkg.helper import double


@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result, x: result == x * 2, "must double")
def wrapped_double(x: int) -> int:
    """Delegate doubling to the helper module."""
    return double(x)
''',
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["check", "src", "--level", "3", "--allow-code-execution"])

        assert result.exit_code == 0
        assert "Property testing skipped" not in result.output

    def test_package_subdirectory_with_relative_imports_passes_level_3(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        src_pkg = tmp_path / "src" / "pkg"
        src_pkg.mkdir(parents=True)
        (tmp_path / "SERENECODE.md").write_text("# config\n", encoding="utf-8")
        (src_pkg / "__init__.py").write_text('"""Package."""\n', encoding="utf-8")
        (src_pkg / "helper.py").write_text(
            '''\
"""Helper module."""

import icontract


@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result, x: result == x * 2, "must double")
def double(x: int) -> int:
    """Double a non-negative integer."""
    return x * 2
''',
            encoding="utf-8",
        )
        (src_pkg / "mod.py").write_text(
            '''\
"""Wrapper module."""

import icontract

from .helper import double


@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result, x: result == x * 2, "must double")
def wrapped_double(x: int) -> int:
    """Delegate doubling to the helper module."""
    return double(x)
''',
            encoding="utf-8",
        )

        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["check", "src/pkg", "--level", "3", "--allow-code-execution"])

        assert result.exit_code == 0
        assert "Property testing skipped" not in result.output

    def test_deep_check_requires_explicit_code_execution_flag(self, tmp_path: Path) -> None:
        (tmp_path / "test.py").write_text('"""Module doc."""\n', encoding="utf-8")

        runner = CliRunner()
        result = runner.invoke(main, ["check", str(tmp_path), "--level", "3"])

        assert result.exit_code == 10
        assert "--allow-code-execution" in result.output

    def test_scoped_core_directory_still_enforces_core_rules(self, tmp_path: Path) -> None:
        core_dir = tmp_path / "src" / "core"
        core_dir.mkdir(parents=True)
        (core_dir / "ioy.py").write_text(
            '''\
"""Core module."""

import os
''',
            encoding="utf-8",
        )

        runner = CliRunner()
        result = runner.invoke(main, ["check", str(core_dir), "--structural"])

        assert result.exit_code == 1
        assert "Forbidden import 'os' in core module" in result.output
