"""Adapter implementations for Serenecode.

This package contains I/O implementations of the Protocol interfaces
defined in the ports package. Adapters handle file system access,
subprocess execution, and external tool integration.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable

import icontract

from serenecode.contracts.predicates import is_positive_int, is_valid_verification_level

if TYPE_CHECKING:
    from serenecode.ports.coverage_analyzer import CoverageAnalyzer
    from serenecode.ports.dead_code_analyzer import DeadCodeAnalyzer
    from serenecode.ports.property_tester import PropertyTester
    from serenecode.ports.symbolic_checker import SymbolicChecker
    from serenecode.ports.type_checker import TypeChecker

# Only pass these environment variables to subprocess calls.
# This prevents leaking credentials, API keys, and other
# sensitive values from the parent process environment.
_SAFE_ENV_KEYS = frozenset({
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "SHELL",
    "TMPDIR",
    "SYSTEMROOT",       # Windows
    "COMSPEC",          # Windows
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    # Corporate proxies and TLS (subprocess tools often need these)
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "SSL_CERT_FILE",
    "CURL_CA_BUNDLE",
    "REQUESTS_CA_BUNDLE",
    "PYTHONWARNINGS",
    "TZ",
})


@icontract.require(
    lambda extra_paths: extra_paths is None or isinstance(extra_paths, dict),
    "extra_paths must be None or a dictionary",
)
@icontract.ensure(lambda result: isinstance(result, dict), "result must be a dictionary")
def safe_subprocess_env(
    *,
    extra_paths: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build a subprocess environment with only safe variables.

    Filters os.environ to an allowlist of known-safe keys, then
    merges any extra path variables (PYTHONPATH, MYPYPATH, etc.).

    Args:
        extra_paths: Additional key-value pairs to set in the env.

    Returns:
        A filtered environment dictionary safe for subprocess calls.
    """
    env: dict[str, str] = {}
    # Loop invariant: env contains safe entries from os.environ[0..i]
    for key in _SAFE_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val
    if extra_paths is not None:
        env.update(extra_paths)
    if os.environ.get("SERENECODE_DEBUG"):
        import sys
        keys = sorted(env.keys())
        print(
            f"[serenecode] subprocess environment keys ({len(keys)}): {keys}",
            file=sys.stderr,
        )
    return env


@icontract.require(
    lambda level: is_valid_verification_level(level),
    "level must be between 1 and 6",
)
@icontract.require(
    lambda per_condition_timeout: per_condition_timeout is None
    or is_positive_int(per_condition_timeout),
    "per_condition_timeout must be None or at least 1",
)
@icontract.require(
    lambda per_path_timeout: per_path_timeout is None or is_positive_int(per_path_timeout),
    "per_path_timeout must be None or at least 1",
)
@icontract.require(
    lambda module_timeout: module_timeout is None or is_positive_int(module_timeout),
    "module_timeout must be None or at least 1",
)
@icontract.require(
    lambda coverage_timeout: coverage_timeout is None or is_positive_int(coverage_timeout),
    "coverage_timeout must be None or at least 1",
)
@icontract.ensure(
    lambda result: isinstance(result, tuple) and len(result) == 5,
    "result must be a 5-tuple of adapters (each possibly None)",
)
def wire_adapters(
    level: int,
    *,
    per_condition_timeout: int | None = None,
    per_path_timeout: int | None = None,
    module_timeout: int | None = None,
    coverage_timeout: int | None = None,
    on_unavailable: Callable[[str], None] | None = None,
    dead_code_placeholder: bool = False,
) -> tuple[
    TypeChecker | None,
    CoverageAnalyzer | None,
    PropertyTester | None,
    SymbolicChecker | None,
    DeadCodeAnalyzer | None,
]:
    """Wire up the adapter set for the requested verification level.

    Single source of truth for adapter construction, shared by the CLI
    and the MCP server. Each backend that fails to import is reported
    through ``on_unavailable`` (if provided) and wired as None. Timeout
    arguments left as None use the adapter defaults. When vulture is
    unavailable, ``dead_code_placeholder`` selects an
    UnavailableDeadCodeAnalyzer (reports SKIPPED findings) over None.

    Returns:
        (type_checker, coverage_analyzer, property_tester,
        symbolic_checker, dead_code_analyzer)
    """
    from serenecode.core.exceptions import ToolNotInstalledError

    def _notify(message: str) -> None:
        if on_unavailable is not None:
            on_unavailable(message)

    type_checker: TypeChecker | None = None
    coverage_analyzer: CoverageAnalyzer | None = None
    property_tester: PropertyTester | None = None
    symbolic_checker: SymbolicChecker | None = None

    if level >= 2:
        try:
            from serenecode.adapters.mypy_adapter import MypyTypeChecker
            type_checker = MypyTypeChecker()
        except ImportError:
            _notify("mypy not available for Level 2 checks.")

    if level >= 3:
        try:
            from serenecode.adapters.coverage_adapter import CoverageAnalyzerAdapter
            coverage_kwargs: dict[str, object] = {"allow_code_execution": True}
            if coverage_timeout is not None:
                coverage_kwargs["test_timeout"] = coverage_timeout
            coverage_analyzer = CoverageAnalyzerAdapter(**coverage_kwargs)  # type: ignore[arg-type]
        except (ImportError, ToolNotInstalledError):
            _notify("coverage not available for Level 3 checks.")

    if level >= 4:
        try:
            from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
            property_tester = HypothesisPropertyTester(allow_code_execution=True)
        except ImportError:
            _notify("Hypothesis not available for Level 4 checks.")

    if level >= 5:
        try:
            from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
            crosshair_kwargs: dict[str, object] = {"allow_code_execution": True}
            if per_condition_timeout is not None:
                crosshair_kwargs["per_condition_timeout"] = per_condition_timeout
            if per_path_timeout is not None:
                crosshair_kwargs["per_path_timeout"] = per_path_timeout
            if module_timeout is not None:
                crosshair_kwargs["module_timeout"] = module_timeout
            symbolic_checker = CrossHairSymbolicChecker(**crosshair_kwargs)
        except ImportError:
            _notify("CrossHair not available for Level 5 checks.")

    dead_code_analyzer: DeadCodeAnalyzer | None
    try:
        from serenecode.adapters.vulture_adapter import VultureDeadCodeAnalyzer
        dead_code_analyzer = VultureDeadCodeAnalyzer()
    except ImportError:
        _notify("vulture not available for dead-code analysis.")
        if dead_code_placeholder:
            from serenecode.adapters.unavailable_dead_code_adapter import (
                UnavailableDeadCodeAnalyzer,
            )
            dead_code_analyzer = UnavailableDeadCodeAnalyzer("vulture is not installed")
        else:
            dead_code_analyzer = None

    return (
        type_checker, coverage_analyzer, property_tester,
        symbolic_checker, dead_code_analyzer,
    )
