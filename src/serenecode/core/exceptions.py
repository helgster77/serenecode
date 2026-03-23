"""Domain exception hierarchy for Serenecode.

This module defines all domain-specific exceptions used throughout
the Serenecode codebase. All exceptions inherit from SerenecodeError,
which is the base exception for the entire framework.

This is a core module — no I/O imports are permitted.
"""

from __future__ import annotations


class SerenecodeError(Exception):
    """Base exception for all Serenecode errors."""


class ConfigurationError(SerenecodeError):
    """Raised when SERENECODE.md parsing or configuration fails."""


class StructuralViolationError(SerenecodeError):
    """Raised when code does not follow SERENECODE.md structural conventions."""


class VerificationError(SerenecodeError):
    """Raised when formal verification finds a counterexample."""


class InitializationError(SerenecodeError):
    """Raised when project initialization fails."""


class ToolNotInstalledError(SerenecodeError):
    """Raised when a required external tool is not installed."""


class UnsafeCodeExecutionError(SerenecodeError):
    """Raised when deep verification is requested without trusting the code."""
