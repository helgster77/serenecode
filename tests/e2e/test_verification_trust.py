"""Exercise actual pytest outcomes and coverage path handling through the CLI."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from serenecode.cli import main


def test_doctor_reports_missing_coverage_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None if name == "pytest_cov" else find_spec(name))
    result = CliRunner().invoke(main, ["doctor"])
    assert result.exit_code == 0
    assert "Verification interpreter:" in result.output
    assert "NOT FOUND: pytest-cov" in result.output


@pytest.mark.parametrize("expected,exit_code", [(4, 0), (99, 3), (None, 3)])
def test_test_outcome_is_preserved_from_outside_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expected: int | None, exit_code: int) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "pyproject.toml").write_text('[project]\nname="coverage-fixture"\nversion="0.0.0"\n')
    (project / "src").mkdir()
    (project / "tests").mkdir()
    (project / "src" / "math_ops.py").write_text('''"""Arithmetic."""
import icontract
@icontract.require(lambda x: x >= 0, "non-negative input")
@icontract.ensure(lambda result: result >= 0, "non-negative output")
def square(x: int) -> int:
    """Square the input."""
    return x * x
''')
    (project / "tests" / "test_math_ops.py").write_text(f'from math_ops import square\n\ndef test_square():\n    assert square(2) == {expected}\n')
    if expected is None:
        (project / "tests" / "test_unimportable.py").write_text("import definitely_missing_test_dependency\n")
    original_coverage = project / ".coverage"
    original_coverage.write_bytes(b"existing coverage must be preserved")
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["check", str(project / "src"), "--level", "3", "--allow-code-execution", "--format", "json"])
    assert result.exit_code == exit_code, result.output
    data = json.loads(result.stdout)
    assert data["passed"] is (exit_code == 0)
    if expected is not None:
        assert any(r["level_requested"] == 3 and r["function"] == "square" and r["status"] == "passed" for r in data["results"])
    if expected == 99:
        failures = [d for r in data["results"] for d in r["details"] if d["type"] == "test_failure"]
        assert failures and failures[0]["counterexample"]["test_exit_code"] == 1
    if expected is None:
        errors = [d for r in data["results"] for d in r["details"] if d["type"] == "test_execution_error"]
        assert errors and errors[0]["counterexample"]["test_exit_code"] == 2
    assert original_coverage.read_bytes() == b"existing coverage must be preserved"
