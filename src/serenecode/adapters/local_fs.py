"""Local file system adapter for Serenecode.

This module implements the FileReader and FileWriter protocols using
pathlib for actual file system operations. It is the only module
that directly touches the real file system.

This is an adapter module — it handles I/O and wraps OS errors
in domain exceptions.
"""

from __future__ import annotations

from pathlib import Path

from serenecode.core.exceptions import ConfigurationError, InitializationError


class LocalFileReader:
    """File reader implementation using pathlib.

    Reads files from the local file system and lists Python files
    in directories.
    """

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

    def file_exists(self, path: str) -> bool:
        """Check whether a file exists at the given path.

        Args:
            path: Path to check.

        Returns:
            True if a file exists at path.
        """
        return Path(path).is_file()

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
            files = sorted(str(p) for p in dir_path.rglob("*.py"))
        except OSError as exc:
            raise ConfigurationError(
                f"Cannot list files in '{directory}': {exc}"
            ) from exc

        return files


class LocalFileWriter:
    """File writer implementation using pathlib.

    Writes files to the local file system and creates directories.
    """

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
