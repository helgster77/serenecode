"""Shared contract predicates for Serenecode.

This module contains reusable boolean predicate functions used in
icontract decorators across the codebase. All predicates are pure
functions with no side effects and no I/O operations.

This is a core module — no I/O imports are permitted.
"""

from __future__ import annotations

import re

import icontract

# Pattern for valid snake_case identifiers
_SNAKE_CASE_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

# Pattern for valid PascalCase identifiers
_PASCAL_CASE_PATTERN = re.compile(r"^[A-Z][a-zA-Z0-9]*$")

# Pattern for UPPER_SNAKE_CASE constants
_UPPER_SNAKE_CASE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")

# Valid verification levels
_MIN_VERIFICATION_LEVEL = 1
_MAX_VERIFICATION_LEVEL = 5

# Valid exit codes per spec
_VALID_EXIT_CODES = frozenset({0, 1, 2, 3, 4, 5, 10})


@icontract.require(lambda value: isinstance(value, str), "value must be a string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def is_non_empty_string(value: str) -> bool:
    """Check that a string is non-empty and not just whitespace.

    Args:
        value: The string to check.

    Returns:
        True if the string is non-empty and contains non-whitespace characters.
    """
    return len(value.strip()) > 0


@icontract.require(lambda level: isinstance(level, int), "level must be an integer")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def is_valid_verification_level(level: int) -> bool:
    """Check that an integer is a valid verification level (1-5).

    Args:
        level: The level to validate.

    Returns:
        True if level is between 1 and 5 inclusive.
    """
    return _MIN_VERIFICATION_LEVEL <= level <= _MAX_VERIFICATION_LEVEL


@icontract.require(lambda code: isinstance(code, int), "code must be an integer")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def is_valid_exit_code(code: int) -> bool:
    """Check that an integer is a valid Serenecode exit code.

    Valid exit codes: 0 (passed), 1-5 (level failures), 10 (internal error).

    Args:
        code: The exit code to validate.

    Returns:
        True if the code is a valid Serenecode exit code.
    """
    return code in _VALID_EXIT_CODES


@icontract.require(lambda value: isinstance(value, int), "value must be an integer")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def is_non_negative_int(value: int) -> bool:
    """Check that an integer is non-negative.

    Args:
        value: The integer to check.

    Returns:
        True if value is an integer >= 0.
    """
    return value >= 0


@icontract.require(lambda value: isinstance(value, int), "value must be an integer")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def is_positive_int(value: int) -> bool:
    """Check that an integer is positive (>= 1).

    Args:
        value: The integer to check.

    Returns:
        True if value is an integer >= 1.
    """
    return value >= 1


@icontract.require(lambda value: isinstance(value, str), "value must be a string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def is_valid_file_path_string(value: str) -> bool:
    """Check that a string looks like a valid file path syntactically.

    This is a pure syntactic check — it does not touch the filesystem.
    Checks that the string is non-empty and does not contain null bytes.

    Args:
        value: The string to check.

    Returns:
        True if value is a syntactically valid file path string.
    """
    return len(value) > 0 and "\x00" not in value


@icontract.require(lambda name: isinstance(name, str), "name must be a string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def is_snake_case(name: str) -> bool:
    """Check that a name follows snake_case convention.

    Args:
        name: The identifier to check.

    Returns:
        True if the name is valid snake_case.
    """
    return bool(_SNAKE_CASE_PATTERN.match(name))


@icontract.require(lambda name: isinstance(name, str), "name must be a string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def is_pascal_case(name: str) -> bool:
    """Check that a name follows PascalCase convention.

    Args:
        name: The identifier to check.

    Returns:
        True if the name is valid PascalCase.
    """
    return bool(_PASCAL_CASE_PATTERN.match(name))


@icontract.require(lambda name: isinstance(name, str), "name must be a string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def is_upper_snake_case(name: str) -> bool:
    """Check that a name follows UPPER_SNAKE_CASE convention.

    Args:
        name: The identifier to check.

    Returns:
        True if the name is valid UPPER_SNAKE_CASE.
    """
    return bool(_UPPER_SNAKE_CASE_PATTERN.match(name))


@icontract.require(lambda name: isinstance(name, str), "name must be a string")
@icontract.ensure(lambda result: isinstance(result, bool), "result must be a boolean")
def is_valid_template_name(name: str) -> bool:
    """Check that a template name is one of the recognized templates.

    Args:
        name: The template name to check.

    Returns:
        True if name is 'default', 'strict', or 'minimal'.
    """
    return name in ("default", "strict", "minimal")
