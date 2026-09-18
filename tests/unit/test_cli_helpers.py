"""Minimal tests for cli_helpers module."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from serenecode.cli import main
from serenecode.cli_helpers import (
    _env_int_or,
    _init_confirm_callback,
    _resolve_init_mcp_setup,
    _resolve_init_spec_mode,
    _resolve_init_template,
)


def test_env_int_or_returns_fallback_when_env_unset(monkeypatch: object) -> None:
    """_env_int_or returns the fallback when the env var is not set."""
    import os

    env_name = "SERENECODE_TEST_NONEXISTENT_VAR_12345"
    # Ensure the variable is not set
    os.environ.pop(env_name, None)
    assert _env_int_or(env_name, 4) == 4


def test_env_int_or_reads_env_variable(monkeypatch: object) -> None:
    """_env_int_or reads the environment variable when set."""
    import os

    env_name = "SERENECODE_TEST_CLI_HELPERS_VAR"
    os.environ[env_name] = "7"
    try:
        assert _env_int_or(env_name, 4) == 7
    finally:
        os.environ.pop(env_name, None)


class TestInitAnswerResolution:
    """`serenecode init` answers come from flags, --yes, or a prompt."""

    def test_flags_are_returned_without_prompting(self) -> None:
        """A supplied flag short-circuits its prompt.

        Verifies: REQ-044
        """
        assert _resolve_init_spec_mode("existing", False) == "existing"
        assert _resolve_init_template("strict", False) == "strict"
        assert _resolve_init_mcp_setup(False, False) is False

    def test_yes_selects_the_recommended_defaults(self) -> None:
        """--yes answers the unsupplied prompts.

        Verifies: REQ-044
        """
        assert _resolve_init_spec_mode(None, True) == "generate"
        assert _resolve_init_template(None, True) == "default"
        assert _resolve_init_mcp_setup(None, True) is True

    def test_prompts_are_used_when_nothing_is_supplied(self) -> None:
        """Without flags or --yes the prompts still drive the answers.

        Verifies: REQ-044
        """
        runner = CliRunner()
        with runner.isolation(input="1\n3\nn\n"):
            assert _resolve_init_spec_mode(None, False) == "existing"
            assert _resolve_init_template(None, False) == "strict"
            assert _resolve_init_mcp_setup(None, False) is False


class TestInitConfirmCallback:
    """Overwriting an existing file needs explicit consent."""

    def test_yes_confirms_without_asking(self) -> None:
        """--yes means initialize_project proceeds unconditionally.

        Verifies: REQ-044
        """
        assert _init_confirm_callback(True, False) is None

    def test_interactive_session_is_asked(self) -> None:
        """An interactive run still gets the overwrite prompt.

        Verifies: REQ-044
        """
        callback = _init_confirm_callback(False, True)
        assert callback is not None
        runner = CliRunner()
        with runner.isolation(input="n\n"):
            assert callback("Overwrite?") is False

    def test_unattended_run_declines_and_explains(self) -> None:
        """With nobody to ask, the existing file is kept and the reason shown.

        Verifies: REQ-044
        """
        callback = _init_confirm_callback(False, False)
        assert callback is not None
        runner = CliRunner()
        with runner.isolation() as streams:
            assert callback("SERENECODE.md already exists. Overwrite?") is False
            printed = streams[0].getvalue().decode()
        assert "--yes to overwrite" in printed


def test_init_command_runs_through_its_prompts(tmp_path: Path) -> None:
    """The interactive path of the init command still works end to end.

    Verifies: REQ-044
    """
    runner = CliRunner()
    result = runner.invoke(main, ["init", str(tmp_path)], input="2\n2\ny\n")
    assert result.exit_code == 0, result.output
    assert (tmp_path / "SERENECODE.md").exists()
    assert "Welcome to Serenecode!" in result.output


def test_init_command_with_an_existing_spec_and_no_mcp(tmp_path: Path) -> None:
    """The other interactive answers reach their own output branches.

    Verifies: REQ-044
    """
    runner = CliRunner()
    result = runner.invoke(main, ["init", str(tmp_path)], input="1\n1\nn\n")
    assert result.exit_code == 0, result.output
    assert "(minimal template)" in result.output
    assert "Place narrative requirements" in result.output
    assert "serenecode mcp" not in result.output


def test_init_command_runs_unattended_in_process(tmp_path: Path) -> None:
    """--yes takes the non-interactive path with no prompt output.

    Verifies: REQ-044
    """
    runner = CliRunner()
    result = runner.invoke(main, ["init", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert "Welcome to Serenecode!" not in result.output
    assert "(default template)" in result.output


def test_init_command_with_flags_skips_the_welcome(tmp_path: Path) -> None:
    """Fully flagged runs prompt for nothing.

    Verifies: REQ-044
    """
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", str(tmp_path), "--level", "strict", "--spec", "generate", "--mcp"],
    )
    assert result.exit_code == 0, result.output
    assert "Welcome to Serenecode!" not in result.output
    assert "serenecode mcp" in result.output


def test_init_command_updates_an_existing_claude_md(tmp_path: Path) -> None:
    """An existing CLAUDE.md is updated rather than created.

    Verifies: REQ-044
    """
    (tmp_path / "CLAUDE.md").write_text("# Project notes\n", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(main, ["init", str(tmp_path), "--yes"])
    assert result.exit_code == 0, result.output
    assert "Updated CLAUDE.md" in result.output
    assert "# Project notes" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")


def test_init_command_keeps_an_existing_serenecode_md(tmp_path: Path) -> None:
    """Without --yes an unattended run leaves the file and says why.

    Verifies: REQ-044
    """
    (tmp_path / "SERENECODE.md").write_text("mine", encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["init", str(tmp_path), "--level", "minimal", "--spec", "generate", "--no-mcp"],
    )
    assert result.exit_code == 0, result.output
    assert "Created SERENECODE.md" not in result.output
    assert "--yes to overwrite" in result.output
    assert (tmp_path / "SERENECODE.md").read_text(encoding="utf-8") == "mine"
