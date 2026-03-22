"""Local file system adapter for Serenecode.

This module implements the FileReader and FileWriter protocols using
pathlib for actual file system operations. It is the only module
that directly touches the real file system.

This is an adapter module — it handles I/O and wraps OS errors
in domain exceptions.
"""

from __future__ import annotations

import os
from pathlib import Path

import icontract

from serenecode.contracts.predicates import is_non_empty_string
from serenecode.core.exceptions import ConfigurationError, InitializationError

_IGNORED_DIR_NAMES = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    ".hypothesis",
    "__pycache__",
    "build",
    "dist",
    "env",
    "node_modules",
    "venv",
})


@icontract.invariant(lambda self: True, "reader carries no mutable state")
class LocalFileReader:
    """File reader implementation using pathlib.

    Reads files from the local file system and lists Python files
    in directories.
    """

    @icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
    @icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
    def read_file(self, path: str) -> str:
        """Read a file and return its contents as a UTF-8 string.

        Args:
            path: Path to the file to read.

        Returns:
            The full file contents as a string.

        Raises:
            ConfigurationError: If the file cannot be read.
        """
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError as exc:
            raise ConfigurationError(f"Cannot read file '{path}': {exc}") from exc

    @icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
    @icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
    def file_exists(self, path: str) -> bool:
        """Check whether a file exists at the given path.

        Args:
            path: Path to check.

        Returns:
            True if a file exists at path.
        """
        return Path(path).is_file()

    @icontract.require(lambda directory: is_non_empty_string(directory), "directory must be a non-empty string")
    @icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
    def list_python_files(self, directory: str) -> list[str]:
        """List all Python (.py) files in a directory recursively.

        Args:
            directory: Root directory to search.

        Returns:
            Sorted list of paths to .py files as strings.

        Raises:
            ConfigurationError: If the directory cannot be read.
        """
        dir_path = Path(directory)

        if not dir_path.exists():
            raise ConfigurationError(f"Directory does not exist: '{directory}'")

        if dir_path.is_file():
            if dir_path.suffix == ".py":
                return [str(dir_path)]
            return []

        try:
            files: list[str] = []
            # Loop invariant: files contains Python files discovered from prior os.walk entries
            for current_root, dir_names, file_names in os.walk(dir_path):
                dir_names[:] = sorted(
                    d for d in dir_names
                    if d not in _IGNORED_DIR_NAMES
                )

                # Loop invariant: files contains matching Python files from file_names[0..i]
                for file_name in sorted(file_names):
                    if not file_name.endswith(".py"):
                        continue
                    files.append(str(Path(current_root) / file_name))
        except OSError as exc:
            raise ConfigurationError(
                f"Cannot list files in '{directory}': {exc}"
            ) from exc

        return sorted(files)


@icontract.invariant(lambda self: True, "writer carries no mutable state")
class LocalFileWriter:
    """File writer implementation using pathlib.

    Writes files to the local file system and creates directories.
    """

    @icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
    @icontract.require(lambda content: isinstance(content, str), "content must be a string")
    @icontract.ensure(lambda result: result is None, "result must be None")
    def write_file(self, path: str, content: str) -> None:
        """Write content to a file, creating parent directories if needed.

        Args:
            path: Path to the file to write.
            content: Content to write as UTF-8.

        Raises:
            InitializationError: If the file cannot be written.
        """
        try:
            file_path = Path(path)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            raise InitializationError(f"Cannot write file '{path}': {exc}") from exc

    @icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
    @icontract.ensure(lambda result: result is None, "result must be None")
    def ensure_directory(self, path: str) -> None:
        """Ensure a directory exists, creating it if necessary.

        Args:
            path: Path to the directory.

        Raises:
            InitializationError: If the directory cannot be created.
        """
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise InitializationError(
                f"Cannot create directory '{path}': {exc}"
            ) from exc
