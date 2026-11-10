"""End-to-end tests for the verdict-soundness requirements (REQ-054 onwards).

Each test drives the CLI the way a user would and checks the verdict, the
exit code, or the shape of the output — the things a wrong internal result
would eventually show up as.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from serenecode.adapters import project_output_on_stderr
from serenecode.cli import main

_CONTRACTED_MODULE = '''\
"""A small module with real contracts."""

import icontract


@icontract.require(lambda count: count >= 0, "count is non-negative")
@icontract.ensure(lambda count, result: result == count + 1, "result is the successor")
def successor(count: int) -> int:
    """Return the next integer."""
    return count + 1
'''

_NOISY_MODULE = '''\
"""A module that prints when imported and when called."""

import icontract

print("IMPORT NOISE")


@icontract.require(lambda count: count >= 0, "count is non-negative")
@icontract.ensure(lambda count, result: result == count + 1, "result is the successor")
def successor(count: int) -> int:
    """Return the next integer."""
    print("CALL NOISE")
    return count + 1
'''

_EXITING_MODULE = '''\
"""A module whose function terminates the interpreter."""

import sys

import icontract


@icontract.require(lambda code: 0 <= code <= 3, "code is a small exit status")
@icontract.ensure(lambda result: result >= 0, "result is non-negative")
def leave(code: int) -> int:
    """Exit with the given status."""
    sys.exit(code)
'''


def _write_project(root: Path, module_source: str, function: str = "successor") -> None:
    """Create a one-module project with a test file for it."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "calc.py").write_text(module_source, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_calc.py").write_text(
        f"from calc import {function}\n\n\ndef test_it() -> None:\n    assert {function}(1) >= 0\n",
        encoding="utf-8",
    )


class TestProjectLocation:
    """Where a project is checked out must not change its verdict."""

    def test_project_below_a_tests_directory_is_still_checked(self, tmp_path: Path) -> None:
        """A checkout under `.../tests/...` is not a test suite.

        Verifies: REQ-055
        """
        root = tmp_path / "tests" / "proj"
        root.mkdir(parents=True)
        (root / "calc.py").write_text(
            '"""No contracts here."""\n\n\ndef successor(count: int) -> int:\n'
            '    """Return the next integer."""\n    return count + 1\n',
            encoding="utf-8",
        )

        result = CliRunner().invoke(main, ["check", str(root), "--structural"])

        assert result.exit_code == 1
        assert "missing @icontract.ensure" in result.output


class TestProjectOutput:
    """Project code cannot write into the report."""

    def test_redirect_sends_prints_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Verifies: REQ-064"""
        with project_output_on_stderr():
            print("project noise")
        print("report")

        captured = capsys.readouterr()
        assert captured.out == "report\n"
        assert "project noise" in captured.err

    def test_json_report_stays_parseable_when_the_project_prints(self, tmp_path: Path) -> None:
        """Verifies: REQ-064"""
        _write_project(tmp_path, _NOISY_MODULE)

        result = CliRunner().invoke(
            main,
            ["check", str(tmp_path), "--level", "4", "--allow-code-execution", "--format", "json"],
        )

        document = json.loads(result.stdout)
        assert document["level_requested"] == 4
        assert "NOISE" not in result.stdout
        assert "CALL NOISE" in result.stderr


class TestTargetExit:
    """A target that exits is a finding with a report, not the run's exit code."""

    def test_exit_in_a_checked_function_fails_level_4(self, tmp_path: Path) -> None:
        """Verifies: REQ-057"""
        _write_project(tmp_path, _EXITING_MODULE, function="leave")
        (tmp_path / "tests" / "test_calc.py").write_text(
            "import pytest\n\nfrom calc import leave\n\n\n"
            "def test_it() -> None:\n    with pytest.raises(SystemExit):\n        leave(0)\n",
            encoding="utf-8",
        )

        result = CliRunner().invoke(
            main,
            ["check", str(tmp_path), "--level", "4", "--allow-code-execution", "--format", "json"],
        )

        assert result.exit_code == 4
        document = json.loads(result.stdout)
        failed = [record for record in document["results"] if record["status"] == "failed"]
        assert [record["function"] for record in failed] == ["leave"]
        assert "SystemExit" in failed[0]["details"][0]["message"]


class TestEmptySpec:
    """An empty SPEC.md is a finding."""

    def test_empty_spec_produces_a_report(self, tmp_path: Path) -> None:
        """Verifies: REQ-068"""
        _write_project(tmp_path, _CONTRACTED_MODULE)
        (tmp_path / "SPEC.md").write_text("", encoding="utf-8")

        result = CliRunner().invoke(main, ["check", str(tmp_path), "--structural"])

        assert result.exit_code == 1
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert "No REQ-xxx or INT-xxx headings found in spec" in result.output


class TestMcpExtra:
    """The MCP extra names the SDK line the server works with."""

    def test_extra_is_bounded(self) -> None:
        """Verifies: REQ-067"""
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        assert '"mcp>=1.2,<2"' in pyproject.read_text(encoding="utf-8")

    def test_failed_server_import_reports_the_underlying_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Verifies: REQ-067"""
        # None in sys.modules makes the import statement raise ImportError.
        monkeypatch.setitem(sys.modules, "serenecode.mcp.server", None)

        result = CliRunner().invoke(main, ["mcp"])

        assert result.exit_code != 0
        assert "Underlying error:" in result.output
        assert ">=1.2,<2" in result.output


class TestLevelSixOnAProjectRoot:
    """Checking a project root must not ask test functions for contracts."""

    def test_project_with_tests_passes_level_6(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Level 6 follows Level 1: test files carry no production contracts.

        The project is addressed as `..` from inside it, which is also a
        path the Level 6 parser has to accept.

        Verifies: REQ-065
        """
        _write_project(tmp_path, _CONTRACTED_MODULE)
        monkeypatch.chdir(tmp_path / "tests")

        result = CliRunner().invoke(
            main,
            ["check", "..", "--level", "6", "--allow-code-execution", "--format", "json"],
        )

        document = json.loads(result.stdout)
        failed = [record for record in document["results"] if record["status"] == "failed"]
        assert failed == []
        assert result.exit_code == 0
        assert document["level_achieved"] == 6
