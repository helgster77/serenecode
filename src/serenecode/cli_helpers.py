"""CLI helper functions extracted from cli.py.

This module contains private helper functions used by the CLI commands.
As a composition-root helper / adapter layer, it is exempt from full
contract requirements (no-invariant: stateless helper module).
"""

from __future__ import annotations

import os
from typing import Callable

import click
import icontract

from serenecode.adapters.local_fs import LocalFileReader
from serenecode.contracts.predicates import (
    is_non_empty_string,
    is_valid_exit_code,
)
from serenecode.models import CheckResult, ExitCode
from serenecode.ports.dead_code_analyzer import DeadCodeAnalyzer
from serenecode.source_discovery import (
    determine_context_root,
    discover_narrative_spec_paths,
    find_spec_md,
)

_TRUST_REQUIRED_MESSAGE = (
    "Levels 3-6 import and execute project modules. "
    "Only run on trusted code with allow_code_execution=True / --allow-code-execution."
)

_SPEC_TRACEABILITY_HINT = (
    "If requirements live in another file (e.g. *_SPEC.md, PRD.md), convert it per "
    "\"Preparing a SereneCode-Ready Spec\" in SERENECODE.md and write SPEC.md with "
    "REQ/INT identifiers and a **Source:** line. Traceability applies only to SPEC.md."
)


@icontract.require(
    lambda module_search_root: is_non_empty_string(module_search_root),
    "module_search_root must be a non-empty string",
)
@icontract.require(lambda reader: reader is not None, "reader must be provided")
@icontract.require(
    lambda spec_explicit_path: spec_explicit_path is None or is_non_empty_string(
        spec_explicit_path,
    ),
    "spec_explicit_path must be None or a non-empty string",
)
@icontract.ensure(lambda result: result is None, "hint helpers return None")
def _echo_spec_traceability_hints(
    module_search_root: str,
    reader: LocalFileReader,
    *,
    spec_explicit_path: str | None,
) -> None:
    """Print guidance when SPEC.md is missing or unreadable."""
    click.echo(_SPEC_TRACEABILITY_HINT, err=True)
    if spec_explicit_path is not None:
        return
    root = determine_context_root(module_search_root)
    candidates = discover_narrative_spec_paths(root)
    if candidates:
        click.echo("Source-like files in project root:", err=True)
        for candidate in candidates:
            click.echo(f"  {candidate}", err=True)


@icontract.require(lambda reader: reader is not None, "reader must be provided")
@icontract.ensure(lambda result: result is None, "doctor status printer returns None")
def _print_spec_status_for_doctor(reader: LocalFileReader) -> None:
    """Summarize SPEC.md vs narrative files for ``serenecode doctor``."""
    click.echo("Spec status (project root):")
    root = determine_context_root(".")
    spec_path = find_spec_md(".", reader)
    if spec_path:
        click.echo(f"  SPEC.md (traceability): {os.path.abspath(spec_path)}")
    else:
        click.echo("  SPEC.md (traceability): not found")
    narrative = discover_narrative_spec_paths(root)
    if narrative:
        click.echo("  Narrative / source-like files at root:")
        for p in narrative:
            click.echo(f"    {p}")
    else:
        click.echo("  Narrative / source-like files at root: none detected")
    click.echo("")


@icontract.require(lambda name: isinstance(name, str) and len(name) > 0, "name must be non-empty")
@icontract.require(lambda fallback: isinstance(fallback, int) and fallback >= 1, "fallback must be >= 1")
@icontract.ensure(lambda result: isinstance(result, int) and result >= 1, "result must be a positive int")
def _env_int_or(name: str, fallback: int) -> int:
    """Use integer from environment when set, otherwise ``fallback`` (e.g. CLI value)."""
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return fallback
    try:
        return max(1, int(str(raw).strip()))
    except ValueError:
        return fallback


@icontract.ensure(
    lambda result: isinstance(result, bool),
    "result must be a boolean",
)
def _mcp_extra_installed() -> bool:
    """Return True if the optional `mcp` package is importable.

    Used by `serenecode init` to decide whether to print the
    `pip install 'serenecode[mcp]'` line in the post-init MCP
    setup snippet, or skip it because the user already has it.
    """
    # silent-except: probing for an optional dependency; absence is the answer, not an error
    try:
        import mcp.server.fastmcp  # noqa: F401  pragma: no cover
    except ImportError:
        return False
    return True


@icontract.require(
    lambda echo: callable(echo),
    "echo must be a callable that accepts one string",
)
@icontract.ensure(lambda result: result is None, "snippet printer returns None")
def _print_mcp_setup_snippet(echo: Callable[[str], None]) -> None:
    """Print the post-init MCP server setup instructions to the user.

    Detects whether the `mcp` extra is already installed and adapts
    the snippet (install + register, or register only).
    """
    echo("")
    echo("MCP server setup")
    echo("----------------")
    echo("Your AI assistant can verify code mid-edit by calling Serenecode")
    echo("tools through MCP, instead of waiting for `serenecode check` at")
    echo("the end. Recommended for any project where an AI is doing the work.")
    echo("")
    if not _mcp_extra_installed():
        echo("  1. Install the optional `mcp` extra:")
        echo("       # If you installed serenecode from PyPI:")
        echo("       pip install 'serenecode[mcp]'")
        echo("       # or: uv add 'serenecode[mcp]'")
        echo("")
        echo("       # If you cloned the repo and are running from source,")
        echo("       # from the serenecode repo root:")
        echo("       uv sync --extra mcp")
        echo("       # or: pip install -e '.[mcp]'")
        echo("")
        echo("       # From a sibling/subproject venv pointing at the source:")
        echo('       pip install -e "/path/to/serenecode[mcp]"')
        echo("       # The whole argument MUST be quoted — `[mcp]` is a shell glob.")
        echo("")
        echo("  2. Register the server with your AI coding tool:")
    else:
        echo("  Register the server with your AI coding tool:")
    echo("       # Claude Code:")
    echo("       claude mcp add serenecode -- uv run serenecode mcp --allow-code-execution")
    echo("")
    echo("       # Cursor / Cline / Continue: see their MCP docs — same `serenecode mcp`")
    echo("       # stdio command works for every MCP-speaking client.")
    echo("")
    echo("Once registered, your assistant can call `serenecode_check_function`")
    echo("after every function it writes. See SERENECODE.md 'MCP Integration'")
    echo("for the full tool catalog and recommended workflow.")


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(lambda reader: reader is not None, "reader must be provided")
@icontract.ensure(lambda result: isinstance(result, tuple), "result must be a tuple")
def _collect_test_sources(
    path: str,
    reader: LocalFileReader,
) -> tuple[tuple[str, str], ...]:
    """Collect test file sources for spec traceability scanning."""
    from serenecode.source_discovery import determine_context_root, normalize_search_root
    import os

    project_root = determine_context_root(normalize_search_root(path))
    tests_dir = os.path.join(project_root, "tests")

    if not os.path.isdir(tests_dir):
        return ()

    # silent-except: fallback to empty when test directory is unreadable
    try:
        test_files = reader.list_python_files(tests_dir)
    except Exception:
        return ()

    sources: list[tuple[str, str]] = []
    # Loop invariant: sources contains (path, content) for test_files[0..i]
    for test_file in test_files:
        # silent-except: skip unreadable test files, best-effort collection
        try:
            content = reader.read_file(test_file)
            sources.append((test_file, content))
        except Exception:
            continue
    return tuple(sources)


@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(
    lambda spec_path: spec_path is None or is_non_empty_string(spec_path),
    "spec_path must be None or a non-empty string",
)
@icontract.require(lambda reader: reader is not None, "reader must be provided")
@icontract.ensure(
    lambda result: isinstance(result, tuple) and len(result) == 2,
    "result must be a (spec_content, test_sources) pair",
)
def _load_spec_inputs(
    path: str,
    spec_path: str | None,
    reader: LocalFileReader,
) -> tuple[str | None, tuple[tuple[str, str], ...]]:
    """Load SPEC.md content and test sources for baseline traceability checks."""
    resolved_spec = spec_path if spec_path is not None else find_spec_md(path, reader)
    if resolved_spec is None:
        return None, ()

    spec_content = reader.read_file(resolved_spec)
    test_sources = _collect_test_sources(path, reader)
    return spec_content, test_sources


@icontract.ensure(
    lambda result: result is None or hasattr(result, "analyze_paths"),
    "result must be None or a dead-code analyzer",
)
def _maybe_make_dead_code_analyzer() -> DeadCodeAnalyzer | None:
    """Construct the dead-code analyzer when its backend is available."""
    try:
        from serenecode.adapters.vulture_adapter import VultureDeadCodeAnalyzer
        return VultureDeadCodeAnalyzer()
    except ImportError:
        from serenecode.adapters.unavailable_dead_code_adapter import UnavailableDeadCodeAnalyzer
        return UnavailableDeadCodeAnalyzer("vulture is not installed")


def _load_check_config(
    config_search_root: str,
    reader: LocalFileReader,
    skip_module_health: bool,
) -> object:
    """Load SerenecodeConfig from SERENECODE.md or return default."""
    from serenecode.config import parse_serenecode_md, default_config
    from serenecode.source_discovery import find_serenecode_md

    serenecode_md_path = find_serenecode_md(config_search_root, reader)
    if serenecode_md_path:
        config_content = reader.read_file(serenecode_md_path)
        config = parse_serenecode_md(config_content)
    else:
        config = default_config()
        click.echo("Warning: No SERENECODE.md found, using default configuration.", err=True)

    if skip_module_health:
        from dataclasses import replace as _replace
        config = _replace(
            config,
            module_health=_replace(config.module_health, enabled=False),
        )
    return config


def _resolve_effective_level(
    level: int | None,
    structural: bool,
    verify: bool,
    config: object,
) -> tuple[int, int]:
    """Determine effective level and start_level from CLI flags.

    Returns:
        (effective_level, start_level)
    """
    if structural:
        return 1, 1

    recommended = getattr(config, "recommended_level", 4)
    if level is not None:
        effective_level = level
        if verify:
            effective_level = max(effective_level, 3)
    else:
        effective_level = recommended
        if verify:
            effective_level = max(effective_level, 3)

    start_level = 3 if verify and not structural else 1
    return effective_level, start_level


def _discover_sources_and_spec(
    path: str,
    module_search_root: str,
    spec_path: str | None,
    reader: LocalFileReader,
) -> tuple[object, frozenset[str], str | None, tuple[tuple[str, str], ...]]:
    """Discover source files, test stems, and spec content.

    Returns:
        (source_files, test_stems, spec_content, test_sources)

    Raises:
        SystemExit on errors.
    """
    from serenecode.source_discovery import (
        build_source_files,
        determine_context_root,
        discover_narrative_spec_paths,
        discover_test_file_stems,
    )
    from serenecode.core.exceptions import ConfigurationError

    try:
        files = reader.list_python_files(path)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        import sys
        sys.exit(ExitCode.INTERNAL)

    if not files:
        click.echo("No Python files found.")
        import sys
        sys.exit(ExitCode.PASSED)

    try:
        source_files = build_source_files(files, reader, module_search_root)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        import sys
        sys.exit(ExitCode.INTERNAL)

    test_stems = discover_test_file_stems(module_search_root, reader)

    try:
        spec_content, test_sources = _load_spec_inputs(module_search_root, spec_path, reader)
    except ConfigurationError as exc:
        click.echo(f"Error reading spec: {exc}", err=True)
        _echo_spec_traceability_hints(
            module_search_root, reader, spec_explicit_path=spec_path,
        )
        import sys
        sys.exit(ExitCode.INTERNAL)

    if spec_content is None:
        root = determine_context_root(module_search_root)
        if discover_narrative_spec_paths(root):
            click.echo(
                "No SPEC.md found for traceability (searched upward from the search root).",
                err=True,
            )
            _echo_spec_traceability_hints(
                module_search_root, reader, spec_explicit_path=spec_path,
            )

    return source_files, test_stems, spec_content, test_sources


def _wire_adapters(
    level: int,
    per_condition_timeout: int,
    per_path_timeout: int,
    module_timeout: int,
    coverage_timeout: int,
) -> tuple[object, object, object, object, object]:
    """Wire up adapters for levels 2-5 and dead code.

    Returns:
        (type_checker, coverage_analyzer, property_tester, symbolic_checker, dead_code_analyzer)
    """
    type_checker = None
    coverage_analyzer = None
    property_tester = None
    symbolic_checker = None

    if level >= 2:
        try:
            from serenecode.adapters.mypy_adapter import MypyTypeChecker
            type_checker = MypyTypeChecker()
        except ImportError:
            click.echo("Warning: mypy not available for Level 2 checks.", err=True)

    if level >= 3:
        try:
            from serenecode.adapters.coverage_adapter import CoverageAnalyzerAdapter
            coverage_analyzer = CoverageAnalyzerAdapter(
                allow_code_execution=True,
                test_timeout=coverage_timeout,
            )
        except ImportError:
            click.echo("Warning: coverage not available for Level 3 checks.", err=True)

    if level >= 4:
        try:
            from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
            property_tester = HypothesisPropertyTester(allow_code_execution=True)
        except ImportError:
            click.echo("Warning: Hypothesis not available for Level 4 checks.", err=True)

    if level >= 5:
        try:
            from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
            symbolic_checker = CrossHairSymbolicChecker(
                per_condition_timeout=per_condition_timeout,
                per_path_timeout=per_path_timeout,
                module_timeout=module_timeout,
                allow_code_execution=True,
            )
        except ImportError:
            click.echo("Warning: CrossHair not available for Level 5 checks.", err=True)

    try:
        from serenecode.adapters.vulture_adapter import VultureDeadCodeAnalyzer
        dead_code_analyzer: object = VultureDeadCodeAnalyzer()
    except ImportError:
        click.echo("Warning: vulture not available for dead-code analysis.", err=True)
        dead_code_analyzer = None

    return type_checker, coverage_analyzer, property_tester, symbolic_checker, dead_code_analyzer


@icontract.require(lambda check_result: check_result is not None, "result must be provided")
@icontract.ensure(lambda result: is_valid_exit_code(result), "exit code must be valid")
def _determine_exit_code(check_result: CheckResult) -> int:
    """Determine the CLI exit code from a failed CheckResult.

    Uses the verification level of the first failure to determine
    the appropriate exit code per spec Section 4.2.

    Args:
        check_result: A CheckResult with failures.

    Returns:
        An exit code integer (1-6 or 10).
    """
    from serenecode.models import CheckStatus

    # Find the lowest failing level across all failed results
    min_level = 10  # start above any valid level
    # Loop invariant: min_level is the lowest failure level seen in results[0..i]
    for func_result in check_result.results:
        if func_result.status == CheckStatus.FAILED:
            # Loop invariant: checked details[0..j] for level
            for detail in func_result.details:
                level_val = detail.level.value
                if 1 <= level_val <= 6 and level_val < min_level:
                    min_level = level_val

    if min_level <= 6:
        return min_level

    # No FAILED results found — we're here because of skips or
    # level_achieved < level_requested. Report as internal error
    # rather than a misleading level-specific exit code.
    has_any_failure = any(
        r.status == CheckStatus.FAILED for r in check_result.results
    )
    if not has_any_failure:
        return ExitCode.INTERNAL

    if check_result.level_achieved < check_result.level_requested:
        return min(check_result.level_achieved + 1, ExitCode.COMPOSITIONAL)
    return ExitCode.STRUCTURAL  # default to structural
