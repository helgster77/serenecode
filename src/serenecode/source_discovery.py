"""Helpers for finding configuration files and building SourceFile objects.

This module keeps the CLI and library API in sync when they discover
source files, derive module references for higher verification levels,
and locate SERENECODE.md in parent directories.
"""

from __future__ import annotations

import keyword
import os
from pathlib import Path

import icontract

from serenecode.contracts.predicates import is_non_empty_string
from serenecode.core.exceptions import ConfigurationError
from serenecode.core.pipeline import SourceFile
from serenecode.ports.file_system import FileReader

__all__ = [
    "build_source_files",
    "discover_narrative_spec_paths",
    "discover_test_file_stems",
    "find_serenecode_md",
    "find_spec_md",
    "is_test_file_path",
    "normalize_search_root",
    "determine_context_root",
]

_NARRATIVE_ROOT_FILENAMES = frozenset({
    "PRD.md",
    "prd.md",
    "requirements.md",
    "REQUIREMENTS.md",
})

_ARCHITECTURE_DIR_NAMES = frozenset({
    "adapters",
    "checker",
    "contracts",
    "core",
    "ports",
})

_PROJECT_MARKERS = (
    "SERENECODE.md",
    "pyproject.toml",
    ".git",
)


@icontract.require(
    lambda search_root: is_non_empty_string(search_root),
    "search_root must be a non-empty string",
)
@icontract.require(
    lambda reader: reader is not None,
    "reader must be provided",
)
@icontract.require(
    lambda file_paths: all(is_non_empty_string(file_path) for file_path in file_paths),
    "file_paths must contain only non-empty paths",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple),
    "result must be a tuple",
)
def build_source_files(
    file_paths: list[str],
    reader: FileReader,
    search_root: str,
) -> tuple[SourceFile, ...]:
    """Build SourceFile objects from file paths.

    Args:
        file_paths: Paths to Python files.
        reader: File reader for reading contents.
        search_root: Root path originally requested by the user.

    Returns:
        Tuple of SourceFile objects.

    Raises:
        ConfigurationError: If any discovered file cannot be read.
    """
    source_files: list[SourceFile] = []
    normalized_root = determine_context_root(search_root)

    # Loop invariant: source_files contains SourceFile objects for file_paths[0..i]
    for file_path in file_paths:
        try:
            source = reader.read_file(file_path)
        except Exception as exc:
            raise ConfigurationError(
                f"Cannot prepare source file '{file_path}': {exc}"
            ) from exc

        source_files.append(SourceFile(
            file_path=file_path,
            module_path=_derive_module_path(file_path, normalized_root),
            source=source,
            importable_module=_derive_module_reference(file_path, normalized_root),
            import_search_paths=_derive_import_search_paths(file_path, normalized_root),
            context_root=normalized_root,
        ))

    return tuple(source_files)


@icontract.require(
    lambda search_root: is_non_empty_string(search_root),
    "search_root must be a non-empty string",
)
@icontract.require(
    lambda reader: reader is not None,
    "reader must be provided",
)
@icontract.ensure(
    lambda result: isinstance(result, frozenset),
    "result must be a frozenset",
)
def discover_test_file_stems(
    search_root: str,
    reader: FileReader,
) -> frozenset[str]:
    """Discover test file stems from the project's tests/ directory.

    Searches for a ``tests/`` directory at the project root and collects
    the basenames (without ``.py``) of all files matching ``test_*.py``.

    Args:
        search_root: The user-supplied path (file or directory).
        reader: File reader for listing files.

    Returns:
        Frozenset of test file stems, e.g. ``{"test_engine", "test_models"}``.
    """
    project_root = determine_context_root(search_root)
    tests_dir = os.path.join(project_root, "tests")

    if not os.path.isdir(tests_dir):
        return frozenset()

    try:
        test_files = reader.list_python_files(tests_dir)
    except Exception:
        return frozenset()

    stems: set[str] = set()
    # Loop invariant: stems contains test stems from test_files[0..i]
    for test_file in test_files:
        basename = os.path.basename(test_file)
        if basename.startswith("test_") and basename.endswith(".py"):
            stems.add(basename.removesuffix(".py"))

    return frozenset(stems)


@icontract.require(
    lambda path: is_non_empty_string(path),
    "path must be a non-empty string",
)
@icontract.require(
    lambda reader: reader is not None,
    "reader must be provided",
)
@icontract.ensure(
    lambda result: result is None or result.endswith("SERENECODE.md"),
    "result must be a SERENECODE.md path when present",
)
def find_serenecode_md(path: str, reader: FileReader) -> str | None:
    """Find SERENECODE.md by searching up from the given path."""
    return _find_named_file_upwards(path, "SERENECODE.md", reader)


@icontract.require(
    lambda path: is_non_empty_string(path),
    "path must be a non-empty string",
)
@icontract.require(
    lambda reader: reader is not None,
    "reader must be provided",
)
@icontract.ensure(
    lambda result: result is None or result.endswith("SPEC.md"),
    "result must be a SPEC.md path when present",
)
def find_spec_md(path: str, reader: FileReader) -> str | None:
    """Find SPEC.md by searching up from the given path."""
    return _find_named_file_upwards(path, "SPEC.md", reader)


@icontract.require(
    lambda project_root: is_non_empty_string(project_root),
    "project_root must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple),
    "result must be a tuple of paths",
)
def discover_narrative_spec_paths(project_root: str) -> tuple[str, ...]:
    """Return sorted absolute paths to likely narrative spec files at the project root.

    Detects ``*_SPEC.md`` (other than ``SPEC.md``) and common PRD / requirements
    filenames. Does not recurse into subdirectories. Used for CLI hints and
    ``serenecode doctor`` — not for loading traceability content (that is always
    ``SPEC.md`` with REQ/INT identifiers).
    """
    root = Path(project_root)
    if not root.is_dir():
        return ()

    found: list[str] = []
    # silent-except: gracefully return empty when directory listing fails due to permissions
    try:
        # Loop invariant: found is sorted for entries processed so far from iterdir
        for p in sorted(root.iterdir()):
            if not p.is_file():
                continue
            name = p.name
            if name == "SPEC.md":
                continue
            if name.endswith("_SPEC.md") or name in _NARRATIVE_ROOT_FILENAMES:
                found.append(str(p.resolve()))
    except OSError:
        return ()

    return tuple(found)


@icontract.require(
    lambda file_path: is_non_empty_string(file_path),
    "file_path must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a bool",
)
def is_test_file_path(file_path: str) -> bool:
    """Return True when a path points to a test-only Python file."""
    normalized = os.path.normpath(file_path)
    basename = os.path.basename(normalized)
    path_parts = normalized.split(os.sep)
    return (
        "tests" in path_parts
        or basename.startswith("test_")
        or basename == "conftest.py"
    )


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda filename: is_non_empty_string(filename), "filename must be non-empty")
@icontract.require(lambda reader: reader is not None, "reader must be provided")
@icontract.ensure(
    lambda result: result is None or is_non_empty_string(result),
    "result must be None or a non-empty path string",
)
def _find_named_file_upwards(
    path: str,
    filename: str,
    reader: FileReader,
) -> str | None:
    """Search ancestor directories for a named file."""
    current = normalize_search_root(path)

    max_depth = 50
    remaining = max_depth
    # Loop invariant: no checked ancestor directory contains filename
    # Variant: remaining decreases towards 0, guaranteeing termination
    while remaining > 0:
        candidate = os.path.join(current, filename)
        if reader.file_exists(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
        remaining -= 1

    return None


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.ensure(lambda result: is_non_empty_string(result), "result must be a non-empty string")
def normalize_search_root(path: str) -> str:
    """Normalize a user-supplied search path to a directory path."""
    absolute = os.path.abspath(path)
    if os.path.isdir(absolute):
        return absolute
    return os.path.dirname(absolute)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.ensure(lambda result: is_non_empty_string(result), "result must be a non-empty string")
def determine_context_root(path: str) -> str:
    """Determine the stable root used for module-path derivation.

    Walks up from the search root looking for project markers
    (SERENECODE.md, pyproject.toml, .git) or a ``src/`` directory.
    If the user passes a deeply nested path like ``checker/`` without
    any ancestor markers, the fallback returns the search root as-is,
    which may produce incorrect relative module paths. Always run
    from a project root or a path that has ancestor markers.
    """
    search_root = normalize_search_root(path)

    current = search_root
    # Loop invariant: current is an ancestor of search_root already checked for markers
    while True:
        if _has_project_marker(current):
            return current

        if os.path.basename(current) == "src":
            return os.path.dirname(current)

        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    if os.path.isfile(os.path.join(search_root, "__init__.py")):
        return _walk_package_root(search_root)

    if os.path.basename(search_root) in _ARCHITECTURE_DIR_NAMES:
        return os.path.dirname(search_root)

    return search_root


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a bool")
def _has_project_marker(path: str) -> bool:
    """Check whether a directory looks like a project root."""
    # Loop invariant: no marker from _PROJECT_MARKERS[0..i] exists in path
    for marker in _PROJECT_MARKERS:
        if os.path.exists(os.path.join(path, marker)):
            return True
    return False


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.ensure(lambda result: is_non_empty_string(result), "result must be a non-empty string")
def _walk_package_root(path: str) -> str:
    """Walk up package directories and return the import root above them."""
    current = os.path.abspath(path)

    # Loop invariant: current is the directory above the deepest package chain seen so far
    while os.path.isfile(os.path.join(current, "__init__.py")):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    return current


@icontract.require(lambda file_path: is_non_empty_string(file_path), "file_path must be a non-empty string")
@icontract.require(lambda search_root: is_non_empty_string(search_root), "search_root must be a non-empty string")
@icontract.ensure(lambda result: is_non_empty_string(result), "result must be a non-empty string")
def _derive_module_path(file_path: str, search_root: str) -> str:
    """Derive a normalized module path for structural/compositional checks."""
    relative = _relative_to_root(file_path, search_root)
    normalized = relative.replace(os.sep, "/")

    if normalized.startswith("src/"):
        return normalized[4:]

    return normalized


@icontract.require(lambda file_path: is_non_empty_string(file_path), "file_path must be a non-empty string")
@icontract.require(lambda search_root: is_non_empty_string(search_root), "search_root must be a non-empty string")
@icontract.ensure(lambda result: result is None or isinstance(result, str), "result must be a string or None")
def _derive_module_reference(file_path: str, search_root: str) -> str | None:
    """Derive a module reference for Level 3/4 backends.

    Returns dotted module names for common relative/project-local layouts and
    absolute file paths for standalone files that are not importable packages.
    """
    if not file_path.endswith(".py"):
        return None

    relative = _relative_to_root(file_path, search_root)
    normalized_relative = relative.replace(os.sep, "/")
    normalized_file = os.path.abspath(file_path).replace(os.sep, "/")

    if normalized_relative.startswith("src/"):
        module_ref = _module_reference_from_relative(normalized_relative[4:])
        if module_ref is not None:
            return module_ref

    module_ref = _module_reference_from_relative(normalized_relative)
    if module_ref is not None:
        return module_ref

    return normalized_file


@icontract.require(lambda file_path: is_non_empty_string(file_path), "file_path must be a non-empty string")
@icontract.require(lambda search_root: is_non_empty_string(search_root), "search_root must be a non-empty string")
@icontract.ensure(lambda result: isinstance(result, tuple), "result must be a tuple")
def _derive_import_search_paths(file_path: str, search_root: str) -> tuple[str, ...]:
    """Derive sys.path roots needed to import a discovered module."""
    absolute_file = os.path.abspath(file_path)
    normalized_relative = _relative_to_root(file_path, search_root).replace(os.sep, "/")

    candidates: list[str] = []
    if normalized_relative.startswith("src/"):
        candidates.append(os.path.join(search_root, "src"))
    else:
        candidates.append(search_root)

    candidates.append(_infer_import_root(absolute_file))

    roots: list[str] = []
    # Loop invariant: roots contains unique, existing paths from candidates[0..i]
    for candidate in candidates:
        absolute_candidate = os.path.abspath(candidate)
        if not os.path.isdir(absolute_candidate):
            continue
        if any(
            os.path.commonpath([absolute_candidate, root]) == root
            for root in roots
        ):
            continue
        if absolute_candidate not in roots:
            roots.append(absolute_candidate)

    return tuple(roots)


@icontract.require(lambda relative_path: is_non_empty_string(relative_path), "relative_path must be a non-empty string")
@icontract.ensure(lambda result: result is None or isinstance(result, str), "result must be a string or None")
def _module_reference_from_relative(relative_path: str) -> str | None:
    """Build a dotted module name from a root-relative path when valid."""
    if not relative_path.endswith(".py"):
        return None

    module_part = relative_path[:-3]
    if module_part.endswith("/__init__"):
        module_part = module_part[:-9]

    if not module_part:
        return None

    segments = [segment for segment in module_part.split("/") if segment]
    if not segments:
        return None

    # Loop invariant: all segments in segments[0..i] are valid identifiers
    for segment in segments:
        if not segment.isidentifier() or keyword.iskeyword(segment):
            return None

    return ".".join(segments)


@icontract.require(lambda absolute_file: is_non_empty_string(absolute_file), "absolute_file must be a non-empty string")
@icontract.ensure(lambda result: is_non_empty_string(result), "result must be a non-empty string")
def _infer_import_root(absolute_file: str) -> str:
    """Infer the import root by walking above package directories."""
    current = os.path.dirname(absolute_file)

    # Loop invariant: current is the directory above the deepest package chain seen so far
    while os.path.isfile(os.path.join(current, "__init__.py")):
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    return current


@icontract.require(lambda file_path: is_non_empty_string(file_path), "file_path must be a non-empty string")
@icontract.require(lambda search_root: is_non_empty_string(search_root), "search_root must be a non-empty string")
@icontract.ensure(lambda result: is_non_empty_string(result), "result must be a non-empty string")
def _relative_to_root(file_path: str, search_root: str) -> str:
    """Compute a root-relative path when possible."""
    absolute_file = os.path.abspath(file_path)

    try:
        common = os.path.commonpath([absolute_file, search_root])
    except ValueError:
        common = ""

    if common == search_root:
        return os.path.relpath(absolute_file, search_root)

    return os.path.relpath(absolute_file)
