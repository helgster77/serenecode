"""Port definitions for file system operations.

This module defines the Protocol interfaces for reading and writing
files. Core modules depend on these protocols rather than concrete
file system implementations, enabling dependency injection and
testability.

This is a ports module — no implementations, only abstract contracts.
"""

from __future__ import annotations

from typing import Protocol

import icontract


@icontract.invariant(lambda self: True, "protocol has no runtime state")
class FileReader(Protocol):
    """Port for reading file contents.

    Implementations must handle encoding and raise domain exceptions
    for file system errors.
    """

    @icontract.require(lambda path: isinstance(path, str), "path must be a string")
    @icontract.ensure(lambda result: isinstance(result, str), "result must be a string")
    def read_file(self, path: str) -> str:
        """Read a file and return its contents as a UTF-8 string.

        Args:
            path: Path to the file to read.

        Returns:
            The full file contents as a string.
        """
        ...

    @icontract.require(lambda path: isinstance(path, str), "path must be a string")
    @icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
    def file_exists(self, path: str) -> bool:
        """Check whether a file exists at the given path.

        Args:
            path: Path to check.

        Returns:
            True if a file exists at path.
        """
        ...

    @icontract.require(lambda directory: isinstance(directory, str), "directory must be a string")
    @icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
    def list_python_files(self, directory: str) -> list[str]:
        """List all Python (.py) files in a directory recursively.

        Args:
            directory: Root directory to search.

        Returns:
            List of absolute or relative paths to .py files.
        """
        ...


@icontract.invariant(lambda self: True, "protocol has no runtime state")
class FileWriter(Protocol):
    """Port for writing file contents.

    Implementations must handle encoding and raise domain exceptions
    for file system errors.
    """

    @icontract.require(lambda path: isinstance(path, str), "path must be a string")
    @icontract.require(lambda content: isinstance(content, str), "content must be a string")
    @icontract.ensure(lambda result: result is None, "result must be None")
    def write_file(self, path: str, content: str) -> None:
        """Write content to a file, creating it if it doesn't exist.

        Args:
            path: Path to the file to write.
            content: Content to write as UTF-8.
        """
        ...

    @icontract.require(lambda path: isinstance(path, str), "path must be a string")
    @icontract.ensure(lambda result: result is None, "result must be None")
    def ensure_directory(self, path: str) -> None:
        """Ensure a directory exists, creating it if necessary.

        Args:
            path: Path to the directory.
        """
        ...
