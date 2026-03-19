"""End-to-end tests for the serenecode init command."""

from __future__ import annotations

from pathlib import Path

from serenecode.adapters.local_fs import LocalFileReader, LocalFileWriter
from serenecode.init import initialize_project


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
