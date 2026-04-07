"""Integration tests for the local file system adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from serenecode.adapters.local_fs import LocalFileReader, LocalFileWriter
from serenecode.core.exceptions import ConfigurationError, InitializationError


class TestLocalFileReader:
    """Integration tests for LocalFileReader."""

    def test_read_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world", encoding="utf-8")
        reader = LocalFileReader()
        content = reader.read_file(str(test_file))
        assert content == "hello world"

    def test_read_nonexistent_raises(self) -> None:
        reader = LocalFileReader()
        with pytest.raises(ConfigurationError):
            reader.read_file("/nonexistent/file.txt")

    def test_file_exists_true(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello", encoding="utf-8")
        reader = LocalFileReader()
        assert reader.file_exists(str(test_file)) is True

    def test_file_exists_false(self) -> None:
        reader = LocalFileReader()
        assert reader.file_exists("/nonexistent/file.txt") is False

    def test_list_python_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("# a", encoding="utf-8")
        (tmp_path / "b.py").write_text("# b", encoding="utf-8")
        (tmp_path / "c.txt").write_text("# c", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "d.py").write_text("# d", encoding="utf-8")

        reader = LocalFileReader()
        files = reader.list_python_files(str(tmp_path))
        py_files = [f for f in files if f.endswith(".py")]
        assert len(py_files) == 3

    def test_list_python_files_single_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.py"
        test_file.write_text("# test", encoding="utf-8")
        reader = LocalFileReader()
        files = reader.list_python_files(str(test_file))
        assert len(files) == 1

    def test_list_python_files_ignores_virtualenv_directories(self, tmp_path: Path) -> None:
        (tmp_path / "app.py").write_text("# app", encoding="utf-8")
        venv_file = tmp_path / ".venv" / "lib" / "python3.11" / "site-packages" / "pkg.py"
        venv_file.parent.mkdir(parents=True)
        venv_file.write_text("# ignored", encoding="utf-8")

        reader = LocalFileReader()
        files = reader.list_python_files(str(tmp_path))

        assert str(tmp_path / "app.py") in files
        assert str(venv_file) not in files

    def test_list_python_files_nonexistent_raises(self) -> None:
        reader = LocalFileReader()
        with pytest.raises(ConfigurationError):
            reader.list_python_files("/nonexistent/dir")


class TestLocalFileWriter:
    """Integration tests for LocalFileWriter."""

    def test_write_file(self, tmp_path: Path) -> None:
        test_file = tmp_path / "test.txt"
        writer = LocalFileWriter()
        writer.write_file(str(test_file), "hello world")
        assert test_file.read_text(encoding="utf-8") == "hello world"

    def test_write_file_creates_parents(self, tmp_path: Path) -> None:
        test_file = tmp_path / "sub" / "dir" / "test.txt"
        writer = LocalFileWriter()
        writer.write_file(str(test_file), "hello")
        assert test_file.read_text(encoding="utf-8") == "hello"

    def test_ensure_directory(self, tmp_path: Path) -> None:
        new_dir = tmp_path / "new" / "dir"
        writer = LocalFileWriter()
        writer.ensure_directory(str(new_dir))
        assert new_dir.is_dir()

    def test_write_file_oserror_raises_initialization_error(self, tmp_path: Path) -> None:
        """Branch (lines 150-151): OSError during write → InitializationError."""
        from serenecode.core.exceptions import InitializationError
        # Try to write into a path where the parent is an existing FILE — that
        # makes mkdir fail with OSError because you can't create a directory
        # under a regular file.
        blocking_file = tmp_path / "blocker"
        blocking_file.write_text("not a directory", encoding="utf-8")
        target = blocking_file / "child.txt"
        writer = LocalFileWriter()
        import pytest
        with pytest.raises(InitializationError, match="Cannot write file"):
            writer.write_file(str(target), "content")

    def test_ensure_directory_oserror_raises_initialization_error(self, tmp_path: Path) -> None:
        """Branch (lines 166-167): OSError during mkdir → InitializationError."""
        from serenecode.core.exceptions import InitializationError
        blocking_file = tmp_path / "blocker"
        blocking_file.write_text("not a directory", encoding="utf-8")
        target = blocking_file / "child"
        writer = LocalFileWriter()
        import pytest
        with pytest.raises(InitializationError, match="Cannot create directory"):
            writer.ensure_directory(str(target))
