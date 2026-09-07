"""Filesystem adapter for project-wide traceability context in scoped checks."""

from __future__ import annotations

import os

import icontract

from serenecode.core.pipeline import SourceFile
from serenecode.ports.file_system import FileReader
from serenecode.source_discovery import build_source_files, is_test_file_path


@icontract.require(lambda source_files: isinstance(source_files, tuple), "sources must be a tuple")
@icontract.require(lambda reader: reader is not None, "reader must be provided")
@icontract.ensure(lambda result: isinstance(result, tuple), "context sources must be a tuple")
def discover_traceability_sources(
    source_files: tuple[SourceFile, ...], reader: FileReader,
) -> tuple[SourceFile, ...]:
    """Load implementation context from each selected project's source tree.

    Selected source objects win over disk contents, preserving callers' snapshots.
    A src layout limits context to src; flat projects use the project root.
    Test files remain in the separate verification-reference input.
    """
    by_path: dict[str, SourceFile] = {}
    roots = {sf.context_root for sf in source_files if sf.context_root is not None}
    # Loop invariant: by_path contains context from each previously visited project.
    for root in sorted(roots):
        scope = os.path.join(root, "src") if os.path.isdir(os.path.join(root, "src")) else root
        paths = [p for p in reader.list_python_files(scope) if not is_test_file_path(p)]
        for source_file in build_source_files(paths, reader, root):
            by_path[os.path.realpath(source_file.file_path)] = source_file
    for source_file in source_files:
        if not is_test_file_path(source_file.file_path):
            by_path[os.path.realpath(source_file.file_path)] = source_file
    return tuple(by_path.values())
