"""End-to-end tests for the serenecode init command."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from serenecode.adapters.local_fs import LocalFileReader, LocalFileWriter
from serenecode.cli import _print_mcp_setup_snippet, main
from serenecode.init import initialize_project, merge_claude_md


class TestInitCommand:
    """E2E tests for project initialization."""

    def test_init_creates_serenecode_md(self, tmp_path: Path) -> None:
        reader = LocalFileReader()
        writer = LocalFileWriter()
        result = initialize_project(
            directory=str(tmp_path),
            template="default",
            file_reader=reader,
            file_writer=writer,
        )
        assert result.serenecode_md_created is True
        assert (tmp_path / "SERENECODE.md").exists()

    def test_init_creates_claude_md(self, tmp_path: Path) -> None:
        reader = LocalFileReader()
        writer = LocalFileWriter()
        result = initialize_project(
            directory=str(tmp_path),
            template="default",
            file_reader=reader,
            file_writer=writer,
        )
        assert result.claude_md_created is True
        claude_content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "## Serenecode" in claude_content

    def test_init_with_existing_claude_md_updates(self, tmp_path: Path) -> None:
        (tmp_path / "CLAUDE.md").write_text("# Existing\n\nSome content.\n", encoding="utf-8")
        reader = LocalFileReader()
        writer = LocalFileWriter()
        result = initialize_project(
            directory=str(tmp_path),
            template="default",
            file_reader=reader,
            file_writer=writer,
        )
        assert result.claude_md_updated is True
        claude_content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# Existing" in claude_content
        assert "## Serenecode" in claude_content

    def test_init_does_not_duplicate_serenecode_section(self, tmp_path: Path) -> None:
        existing = "# My Project\n\n## Serenecode\nAlready here.\n"
        (tmp_path / "CLAUDE.md").write_text(existing, encoding="utf-8")
        reader = LocalFileReader()
        writer = LocalFileWriter()
        result = initialize_project(
            directory=str(tmp_path),
            template="default",
            file_reader=reader,
            file_writer=writer,
        )
        assert result.claude_md_updated is False
        content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert content.count("## Serenecode") == 1

    def test_init_strict_template(self, tmp_path: Path) -> None:
        reader = LocalFileReader()
        writer = LocalFileWriter()
        result = initialize_project(
            directory=str(tmp_path),
            template="strict",
            file_reader=reader,
            file_writer=writer,
        )
        assert result.template_used == "strict"
        content = (tmp_path / "SERENECODE.md").read_text(encoding="utf-8")
        assert "Strict" in content

    def test_init_minimal_template(self, tmp_path: Path) -> None:
        reader = LocalFileReader()
        writer = LocalFileWriter()
        result = initialize_project(
            directory=str(tmp_path),
            template="minimal",
            file_reader=reader,
            file_writer=writer,
        )
        assert result.template_used == "minimal"
        content = (tmp_path / "SERENECODE.md").read_text(encoding="utf-8")
        assert "Minimal" in content

    def test_init_with_confirm_callback_deny(self, tmp_path: Path) -> None:
        (tmp_path / "SERENECODE.md").write_text("# Old", encoding="utf-8")
        reader = LocalFileReader()
        writer = LocalFileWriter()
        result = initialize_project(
            directory=str(tmp_path),
            template="default",
            file_reader=reader,
            file_writer=writer,
            confirm_callback=lambda msg: False,
        )
        assert result.serenecode_md_created is False
        # Content should be unchanged
        content = (tmp_path / "SERENECODE.md").read_text(encoding="utf-8")
        assert content == "# Old"


class TestInitCliFlow:
    """End-to-end tests for the interactive `serenecode init` CLI flow."""

    def test_three_questions_create_project(self, tmp_path: Path) -> None:
        runner = CliRunner()
        # spec=2, level=2, mcp=Y (no Proceed prompt anymore)
        result = runner.invoke(main, ["init", str(tmp_path)], input="2\n2\nY\n")
        assert result.exit_code == 0
        assert (tmp_path / "SERENECODE.md").exists()
        assert (tmp_path / "CLAUDE.md").exists()

    def test_mcp_question_is_present(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["init", str(tmp_path)], input="2\n2\nY\n")
        assert result.exit_code == 0
        assert "Set up the Serenecode MCP server" in result.output
        assert "Set up MCP?" in result.output

    def test_mcp_yes_prints_setup_snippet(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["init", str(tmp_path)], input="2\n2\nY\n")
        assert result.exit_code == 0
        assert "MCP server setup" in result.output
        assert "claude mcp add serenecode" in result.output

    def test_mcp_no_skips_setup_snippet(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["init", str(tmp_path)], input="2\n2\nn\n")
        assert result.exit_code == 0
        assert "MCP server setup" not in result.output
        assert "claude mcp add serenecode" not in result.output

    def test_no_proceed_prompt(self, tmp_path: Path) -> None:
        # The old flow had a 4th Y/n confirmation. After cleanup, the only
        # confirms in the happy path are the three questions.
        runner = CliRunner()
        result = runner.invoke(main, ["init", str(tmp_path)], input="2\n2\nY\n")
        assert result.exit_code == 0
        assert "Proceed?" not in result.output

    def test_strict_template_via_cli(self, tmp_path: Path) -> None:
        runner = CliRunner()
        # spec=2, level=3 (strict), mcp=Y
        result = runner.invoke(main, ["init", str(tmp_path)], input="2\n3\nY\n")
        assert result.exit_code == 0
        assert "strict template" in result.output
        content = (tmp_path / "SERENECODE.md").read_text(encoding="utf-8")
        assert "Strict" in content


class TestPrintMcpSetupSnippet:
    """Tests for the _print_mcp_setup_snippet helper."""

    def test_when_mcp_installed_skips_install_step(self) -> None:
        lines: list[str] = []
        with patch("serenecode.cli._mcp_extra_installed", return_value=True):
            _print_mcp_setup_snippet(lines.append)
        text = "\n".join(lines)
        assert "pip install 'serenecode[mcp]'" not in text
        assert "claude mcp add serenecode" in text
        assert "Register the server" in text

    def test_when_mcp_not_installed_includes_install_step(self) -> None:
        lines: list[str] = []
        with patch("serenecode.cli._mcp_extra_installed", return_value=False):
            _print_mcp_setup_snippet(lines.append)
        text = "\n".join(lines)
        # PyPI install path
        assert "pip install 'serenecode[mcp]'" in text
        # Source checkout install path (covers the case where the MCP feature
        # has not yet been published to PyPI, or the user is on a dev branch)
        assert "uv sync --extra mcp" in text
        assert "pip install -e '.[mcp]'" in text
        # Sibling/subproject venv install path with explicit shell-glob warning
        assert 'pip install -e "/path/to/serenecode[mcp]"' in text
        assert "shell glob" in text
        assert "claude mcp add serenecode" in text

    def test_snippet_mentions_other_mcp_clients(self) -> None:
        lines: list[str] = []
        with patch("serenecode.cli._mcp_extra_installed", return_value=True):
            _print_mcp_setup_snippet(lines.append)
        text = "\n".join(lines)
        assert "Cursor" in text
        assert "Cline" in text
        assert "Continue" in text

    def test_snippet_points_at_serenecode_md_section(self) -> None:
        lines: list[str] = []
        with patch("serenecode.cli._mcp_extra_installed", return_value=True):
            _print_mcp_setup_snippet(lines.append)
        text = "\n".join(lines)
        assert "MCP Integration" in text


class TestMergeClaudeMd:
    """Tests for merge_claude_md — covers branch gaps at lines 341, 344."""

    def test_existing_content_none_returns_section(self) -> None:
        """Branch (line 341): existing_content is None → return section directly."""
        result = merge_claude_md(None, "## Serenecode\n\nDirective.")
        assert result == "## Serenecode\n\nDirective."

    def test_existing_with_serenecode_section_unchanged(self) -> None:
        """Branch (line 344): existing already has '## Serenecode' → unchanged."""
        existing = "# Project\n\n## Serenecode\nAlready configured.\n"
        result = merge_claude_md(existing, "## Serenecode\nNew directive.")
        assert result == existing

    def test_existing_without_serenecode_section_appends(self) -> None:
        existing = "# Project\n\nSome content."
        section = "## Serenecode\n\nDirective."
        result = merge_claude_md(existing, section)
        assert "# Project" in result
        assert "## Serenecode" in result
        assert result.endswith("Directive.")


class TestInitializeProjectBranches:
    """Tests for initialize_project — covers branch gaps at 408-412, 426."""

    def test_overwrites_existing_serenecode_with_backup(self, tmp_path: Path) -> None:
        """Branch (lines 408-412): existing SERENECODE.md → backup before overwrite."""
        original = "# Old SERENECODE.md\n\nold content"
        (tmp_path / "SERENECODE.md").write_text(original, encoding="utf-8")
        reader = LocalFileReader()
        writer = LocalFileWriter()
        result = initialize_project(
            directory=str(tmp_path),
            template="default",
            file_reader=reader,
            file_writer=writer,
            confirm_callback=lambda msg: True,  # confirm overwrite
        )
        assert result.serenecode_md_created is True
        # The backup file should exist
        backup = tmp_path / "SERENECODE.md.bak"
        assert backup.exists()
        assert backup.read_text(encoding="utf-8") == original

    def test_claude_update_with_confirm_callback_yes(self, tmp_path: Path) -> None:
        """Branch (line 426): existing CLAUDE.md without Serenecode + confirm=yes."""
        existing_claude = "# My Project\n\nSome content.\n"
        (tmp_path / "CLAUDE.md").write_text(existing_claude, encoding="utf-8")
        reader = LocalFileReader()
        writer = LocalFileWriter()
        confirm_calls: list[str] = []

        def confirm(msg: str) -> bool:
            confirm_calls.append(msg)
            return True

        result = initialize_project(
            directory=str(tmp_path),
            template="default",
            file_reader=reader,
            file_writer=writer,
            confirm_callback=confirm,
        )
        assert result.claude_md_updated is True
        # The CLAUDE.md confirm callback should have been called
        assert any("CLAUDE.md" in m for m in confirm_calls)
        new_content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "# My Project" in new_content
        assert "## Serenecode" in new_content

    def test_claude_update_with_confirm_callback_no(self, tmp_path: Path) -> None:
        """Branch (line 426): existing CLAUDE.md + confirm=no → no update."""
        existing_claude = "# My Project\n\nSome content.\n"
        (tmp_path / "CLAUDE.md").write_text(existing_claude, encoding="utf-8")
        reader = LocalFileReader()
        writer = LocalFileWriter()

        result = initialize_project(
            directory=str(tmp_path),
            template="default",
            file_reader=reader,
            file_writer=writer,
            confirm_callback=lambda msg: False if "CLAUDE.md" in msg else True,
        )
        assert result.claude_md_updated is False
        new_content = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert new_content == existing_claude
