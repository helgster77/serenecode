"""Find the project settings that make mypy run code (Level 2).

mypy is a static checker, but two of its configuration options execute
code: ``plugins`` names Python modules that mypy imports and runs, and
``python_executable`` names a program that mypy starts to ask for its
search path. A project that Level 2 only reads can therefore run code
through its mypy configuration. This module finds the configuration
file mypy would use and reports those settings, so the adapter can
refuse to start mypy until code execution has been allowed.

This is an adapter module — it reads configuration files from disk and
is exempt from full contract requirements.
"""

from __future__ import annotations

import configparser
import os
import sys
from dataclasses import dataclass

import icontract

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - mypy itself depends on tomli below Python 3.11
    import tomli as tomllib

# The order mypy tries inside one directory.
_CONFIG_FILE_NAMES = ("mypy.ini", ".mypy.ini", "pyproject.toml", "setup.cfg")

# Files that count as mypy configuration only when they carry a mypy section.
_SHARED_CONFIG_FILE_NAMES = frozenset({"pyproject.toml", "setup.cfg"})

# Settings whose value mypy imports or executes.
_EXECUTING_SETTINGS = ("plugins", "python_executable")

_REPOSITORY_MARKERS = (".git", ".hg")


@icontract.invariant(
    lambda self: len(self.config_file) > 0 and len(self.settings) > 0,
    "a declaration names its file and at least one executing setting",
)
@dataclass(frozen=True)
class ExecutingMypySettings:
    """The code-executing settings found in one mypy configuration file."""

    config_file: str
    settings: tuple[str, ...]  # rendered as "name = value"


@icontract.require(lambda start_dir: len(start_dir) > 0, "start_dir must be a non-empty path")
@icontract.ensure(
    lambda result: result is None or os.path.basename(result.config_file) in _CONFIG_FILE_NAMES,
    "a declaration always comes from a mypy configuration file",
)
def find_executing_mypy_settings(start_dir: str) -> ExecutingMypySettings | None:
    """Return the code-executing settings mypy would pick up when run in start_dir.

    Implements: REQ-066

    Follows mypy's own discovery: in the working directory, then in each
    ancestor up to the repository root, the first of ``mypy.ini``,
    ``.mypy.ini``, ``pyproject.toml`` (with ``[tool.mypy]``) and
    ``setup.cfg`` (with ``[mypy]``) is the configuration. Only that file
    is inspected. A user-level configuration is the user's own choice and
    is not examined.

    Args:
        start_dir: The directory mypy will be started in.

    Returns:
        The executing settings of the applicable configuration file, or
        None when there is no project configuration or it declares none.
    """
    # Loop invariant: no candidate before this one was a mypy configuration
    for candidate in _candidate_config_files(os.path.abspath(start_dir)):
        options = _read_mypy_options(candidate)
        if options is None:
            continue
        settings = tuple(
            f"{name} = {options[name]}" for name in _EXECUTING_SETTINGS if options.get(name)
        )
        return ExecutingMypySettings(candidate, settings) if settings else None
    return None


@icontract.require(lambda start_dir: os.path.isabs(start_dir), "start_dir must be absolute")
@icontract.ensure(
    lambda result: all(os.path.basename(path) in _CONFIG_FILE_NAMES for path in result),
    "only mypy configuration file names are candidates",
)
def _candidate_config_files(start_dir: str) -> list[str]:
    """List the configuration paths mypy tries, nearest directory first."""
    candidates: list[str] = []
    directory = start_dir
    # Variant: directory moves one level towards the filesystem root
    # Loop invariant: candidates holds the config paths of every directory visited so far
    while True:
        candidates.extend(os.path.join(directory, name) for name in _CONFIG_FILE_NAMES)
        parent = os.path.dirname(directory)
        at_repository_root = any(
            os.path.exists(os.path.join(directory, marker)) for marker in _REPOSITORY_MARKERS
        )
        if at_repository_root or parent == directory:
            return candidates
        directory = parent


@icontract.require(lambda path: len(path) > 0, "path must be a non-empty string")
@icontract.ensure(
    lambda result: result is None or all(isinstance(value, str) for value in result.values()),
    "option values are rendered as strings",
)
def _read_mypy_options(path: str) -> dict[str, str] | None:
    """Read the global mypy options from one candidate file.

    Returns:
        The options, or None when the file is absent, unreadable, or is a
        shared file (pyproject.toml, setup.cfg) without a mypy section.
    """
    if not os.path.isfile(path):
        return None
    name = os.path.basename(path)
    # silent-except: a file mypy cannot parse either is not a usable configuration
    try:
        if name == "pyproject.toml":
            with open(path, "rb") as handle:
                section = tomllib.load(handle).get("tool", {}).get("mypy")
            if not isinstance(section, dict):
                return None
            return {key: _render_toml_value(value) for key, value in section.items()}
        parser = configparser.RawConfigParser()
        parser.read(path, encoding="utf-8")
    except (OSError, ValueError, AttributeError, configparser.Error):
        return None
    if parser.has_section("mypy"):
        return {key: " ".join(value.split()) for key, value in parser.items("mypy")}
    return None if name in _SHARED_CONFIG_FILE_NAMES else {}


# no-precondition: every TOML value has a rendering
@icontract.ensure(lambda result: isinstance(result, str), "the rendering is a string")
def _render_toml_value(value: object) -> str:
    """Render a TOML option value the way it would read in an INI file."""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(item) for item in value)
    return str(value)
