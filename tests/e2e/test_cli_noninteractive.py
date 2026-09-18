"""End-to-end tests for unattended CLI use.

These drive the real entry point in a subprocess where stdin is closed, so a
regression that reintroduces a prompt hangs the test rather than passing on a
CliRunner's simulated input.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from serenecode.templates.content import get_template


_SAMPLE = textwrap.dedent('''
    """Sample module."""

    from __future__ import annotations

    import icontract


    @icontract.require(lambda value: value > 0, "positive")
    @icontract.ensure(lambda result: result > 0, "positive")
    def double(value: int) -> int:
        """Double a value.

        Args:
            value: Input.

        Returns:
            The doubled value.
        """
        return value * 2
''')


_ENTRY_POINT = "from serenecode.cli import main; main()"


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run the CLI with stdin closed so any prompt fails instead of blocking."""
    return subprocess.run(
        [sys.executable, "-c", _ENTRY_POINT, *args],
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """A minimal SereneCode project with one clean source file."""
    (tmp_path / "SERENECODE.md").write_text(get_template("default"), encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "sample.py").write_text(_SAMPLE, encoding="utf-8")
    return tmp_path


class TestJsonOutputIsParseable:
    """stdout under --format json must be a JSON document and nothing else."""

    def test_structural_json_stdout_parses_directly(self, project: Path) -> None:
        """Piping stdout straight into a JSON parser succeeds.

        Verifies: REQ-043
        """
        result = _run(["check", "src/sample.py", "--structural", "--format", "json"], project)
        parsed = json.loads(result.stdout)
        assert parsed["summary"]["verdict"] in {"complete", "partial", "failed"}

    def test_progress_and_timing_go_to_stderr(self, project: Path) -> None:
        """Progress lines and the wall-time trailer never reach stdout.

        Verifies: REQ-043
        """
        result = _run(["check", "src/sample.py", "--structural", "--format", "json"], project)
        assert "Level 1:" not in result.stdout
        assert "Total wall time" not in result.stdout
        assert "Level 1:" in result.stderr

    def test_json_findings_carry_function_and_line(self, project: Path) -> None:
        """Machine consumers get the same location the human output shows.

        Verifies: REQ-043
        """
        (project / "src" / "broken.py").write_text(
            textwrap.dedent('''
                """Broken module."""

                def add(a: int, b: int) -> int:
                    """Add two numbers."""
                    return a + b
            '''),
            encoding="utf-8",
        )
        result = _run(["check", "src/broken.py", "--structural", "--format", "json"], project)
        parsed = json.loads(result.stdout)
        failures = [r for r in parsed["results"] if r["status"] == "failed"]
        assert failures
        assert all(r["function"] and r["line"] for r in failures)

    def test_every_finding_uses_one_path_spelling(self, project: Path) -> None:
        """Structural and dead-code findings name the file the same way.

        Verifies: REQ-047
        """
        result = _run(["check", "src/", "--structural", "--format", "json"], project)
        parsed = json.loads(result.stdout)
        paths = {r["file"] for r in parsed["results"] if r["file"].endswith("sample.py")}
        assert len(paths) == 1


class TestInitRunsUnattended:
    """`serenecode init` must complete without stdin."""

    def test_yes_flag_answers_every_prompt(self, tmp_path: Path) -> None:
        """--yes alone initializes a project with the recommended defaults.

        Verifies: REQ-044
        """
        result = _run(["init", "--yes"], tmp_path)
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "SERENECODE.md").exists()
        assert "(default template)" in result.stdout

    def test_flags_alone_skip_every_prompt(self, tmp_path: Path) -> None:
        """Supplying all three answers needs no --yes.

        Verifies: REQ-044
        """
        result = _run(
            ["init", "--level", "strict", "--spec", "generate", "--no-mcp"],
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert "(strict template)" in result.stdout

    def test_no_mcp_suppresses_the_setup_snippet(self, tmp_path: Path) -> None:
        """--no-mcp omits the MCP registration block.

        Verifies: REQ-044
        """
        without = _run(["init", "--level", "minimal", "--spec", "generate", "--no-mcp"], tmp_path)
        assert "serenecode mcp" not in without.stdout

    def test_mcp_flag_prints_the_setup_snippet(self, tmp_path: Path) -> None:
        """--mcp prints it without asking.

        Verifies: REQ-044
        """
        with_mcp = _run(["init", "--level", "minimal", "--spec", "generate", "--mcp"], tmp_path)
        assert "serenecode mcp" in with_mcp.stdout

    def test_existing_file_is_kept_without_yes(self, tmp_path: Path) -> None:
        """An unattended run will not overwrite without --yes.

        Verifies: REQ-044
        """
        (tmp_path / "SERENECODE.md").write_text("mine", encoding="utf-8")
        result = _run(
            ["init", "--level", "minimal", "--spec", "generate", "--no-mcp"],
            tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "SERENECODE.md").read_text(encoding="utf-8") == "mine"
        assert "--yes to overwrite" in result.stdout

    def test_yes_overwrites_an_existing_file(self, tmp_path: Path) -> None:
        """--yes is the explicit consent to replace existing files.

        Verifies: REQ-044
        """
        (tmp_path / "SERENECODE.md").write_text("mine", encoding="utf-8")
        result = _run(["init", "--yes"], tmp_path)
        assert result.returncode == 0, result.stderr
        assert (tmp_path / "SERENECODE.md").read_text(encoding="utf-8") != "mine"
