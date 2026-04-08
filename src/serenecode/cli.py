"""CLI entry point for Serenecode.

This module is the composition root for the command-line interface.
It wires adapters to ports and delegates to core logic. As a thin
adapter layer, it is exempt from full contract requirements but
must have type annotations and pass mypy.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Callable

import click
import icontract

from serenecode.adapters.local_fs import LocalFileReader, LocalFileWriter
from serenecode.config import parse_serenecode_md
from serenecode.contracts.predicates import (
    is_non_empty_string,
    is_positive_int,
    is_valid_exit_code,
    is_valid_template_name,
    is_valid_verification_level,
)
from serenecode.core.exceptions import ConfigurationError
from serenecode.core.pipeline import run_pipeline
from serenecode.init import initialize_project
from serenecode.models import CheckResult, ExitCode
from serenecode.ports.dead_code_analyzer import DeadCodeAnalyzer
from serenecode.reporter import format_html, format_human, format_json
from serenecode.source_discovery import (
    build_source_files,
    determine_context_root,
    discover_narrative_spec_paths,
    discover_test_file_stems,
    find_serenecode_md,
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


@click.group()
@icontract.ensure(lambda result: result is None, "CLI entrypoint returns None")
def main() -> None:
    """Serenecode — formal verification for AI-generated Python code."""


@main.command()
@click.argument("path", default=".")
@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.ensure(lambda result: result is None, "CLI commands return None")
def init(path: str) -> None:
    """Initialize a Serenecode project."""
    click.echo("")
    click.echo("Welcome to Serenecode!")
    click.echo("")

    # Question 1: Spec
    click.echo("Will you be building this project from a spec?")
    click.echo("")
    click.echo("  [1] I already have requirements in a document (any name)")
    click.echo("      Narrative PRDs and *_SPEC.md are inputs only. You must still")
    click.echo("      produce SPEC.md with REQ/INT identifiers — that is the sole")
    click.echo("      traceability spec for SereneCode.")
    click.echo("")
    click.echo("  [2] I'll write the spec with my coding assistant (recommended)")
    click.echo("      Your assistant will help you write SPEC.md with")
    click.echo("      requirement identifiers, then implement from it.")
    click.echo("")
    spec_choice = click.prompt("Choose", type=click.IntRange(1, 2), default=2)
    spec_mode = "existing" if spec_choice == 1 else "generate"
    click.echo("")

    # Question 2: Level
    click.echo("What verification level would you like?")
    click.echo("")
    click.echo("  [1] Minimal  (Level 2)")
    click.echo("      Contracts and types only. Fast structural checks.")
    click.echo("      Best for: prototypes, scripts, small utilities.")
    click.echo("")
    click.echo("  [2] Default  (Level 4)")
    click.echo("      Contracts + types + test coverage + property testing.")
    click.echo("      Best for: most production projects. (recommended)")
    click.echo("")
    click.echo("  [3] Strict   (Level 6)")
    click.echo("      All of the above + symbolic + compositional verification.")
    click.echo("      Best for: safety-critical or high-assurance code.")
    click.echo("")
    level_choice = click.prompt("Choose", type=click.IntRange(1, 3), default=2)
    template = {1: "minimal", 2: "default", 3: "strict"}[level_choice]
    click.echo("")

    # Question 3: MCP server
    click.echo("Set up the Serenecode MCP server for your AI coding assistant?")
    click.echo("")
    click.echo("  The MCP server lets your assistant call Serenecode tools while")
    click.echo("  it writes code — verifying contracts, running tests, and catching")
    click.echo("  findings inside its edit loop instead of waiting until the end.")
    click.echo("  Works with Claude Code, Cursor, Cline, Continue, and any other")
    click.echo("  MCP client. Highly recommended for AI-driven development.")
    click.echo("")
    setup_mcp = click.confirm("Set up MCP?", default=True)
    click.echo("")

    # Final notice before writing files. Existing SERENECODE.md / CLAUDE.md
    # are protected by the confirm_callback inside initialize_project — this
    # text is informational, not a separate confirmation step.
    click.echo("Note: your choices will be written to SERENECODE.md and become the")
    click.echo("contract between you, your coding assistant, and the verification")
    click.echo("tool. Serenecode does not support changing them once implementation")
    click.echo("has started.")
    click.echo("")

    reader = LocalFileReader()
    writer = LocalFileWriter()

    def confirm(message: str) -> bool:
        return click.confirm(message, default=True)

    result = initialize_project(
        directory=path,
        template=template,
        file_reader=reader,
        file_writer=writer,
        confirm_callback=confirm,
        spec_mode=spec_mode,
    )

    click.echo("")
    if result.serenecode_md_created:
        click.echo(f"Created SERENECODE.md ({template} template)")
    if result.claude_md_created:
        click.echo("Created CLAUDE.md with Serenecode directive")
    if result.claude_md_updated:
        click.echo("Updated CLAUDE.md with Serenecode directive")

    click.echo("")
    click.echo("Ready. Start a coding session — your assistant will read")
    click.echo("SERENECODE.md and follow the spec-driven workflow automatically.")
    click.echo("Tip: run `serenecode doctor` to confirm the MCP optional install and")
    click.echo("      registration commands if you use an AI client with MCP.")
    if spec_mode == "existing":
        click.echo("")
        click.echo("Place narrative requirements in the project (or link them from SPEC.md).")
        click.echo("Your assistant will rewrite them into SPEC.md with REQ/INT, validate,")
        click.echo("plan, and build — traceability always targets SPEC.md, not *_SPEC.md alone.")

    if setup_mcp:
        _print_mcp_setup_snippet(click.echo)


@main.command()
@click.argument("spec_file", type=click.Path(exists=True))
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["human", "json"]),
    default="human",
    help="Output format",
)
@icontract.require(lambda spec_file: is_non_empty_string(spec_file), "spec_file must be a non-empty string")
@icontract.ensure(lambda result: result is None, "CLI commands return None")
def spec(spec_file: str, output_format: str) -> None:
    """Validate a SPEC.md file for SereneCode readiness.

    Checks that the spec has well-formed REQ-xxx and INT-xxx identifiers,
    no duplicates, no gaps, descriptions on all headings, and valid
    integration-point structure. Run this before starting implementation.
    """
    reader = LocalFileReader()
    try:
        content = reader.read_file(spec_file)
    except Exception as exc:
        click.echo(f"Error reading {spec_file}: {exc}", err=True)
        sys.exit(ExitCode.INTERNAL)

    from serenecode.checker.spec_traceability import validate_spec

    result = validate_spec(content)

    if output_format == "json":
        click.echo(format_json(result))
    else:
        click.echo(format_human(result))

    if result.passed:
        sys.exit(ExitCode.PASSED)
    else:
        sys.exit(ExitCode.STRUCTURAL)


@main.command()
@icontract.ensure(lambda result: result is None, "CLI commands return None")
def doctor() -> None:
    """Show MCP and optional-dependency setup hints (install + IDE registration)."""
    click.echo("")
    click.echo("Serenecode doctor")
    click.echo("-----------------")
    click.echo("")
    if _mcp_extra_installed():
        click.echo("OK: MCP Python package is available (`mcp` import succeeds).")
    else:
        click.echo("NOT FOUND: MCP extra is not installed — AI tools cannot load the server.")
        click.echo("  Install one of:")
        click.echo("    uv add 'serenecode[mcp]'")
        click.echo("    pip install 'serenecode[mcp]'")
        click.echo("    # from a Serenecode git clone: uv sync --extra mcp")
    click.echo("")
    click.echo("Register the stdio server once in your IDE (examples):")
    click.echo("  claude mcp add serenecode -- uv run serenecode mcp --allow-code-execution")
    click.echo("  # Cursor / VS Code: Settings → MCP → add the same command.")
    click.echo("")
    click.echo("Workflow: prefer MCP tools (especially serenecode_check_function) while")
    click.echo("editing; use `serenecode check` in CI or for full-tree batch runs.")
    click.echo("")
    reader = LocalFileReader()
    _print_spec_status_for_doctor(reader)


@main.command()
@click.option(
    "--allow-code-execution",
    is_flag=True,
    help="Permit Levels 3-6 tools (which import and execute project modules)",
)
@click.option(
    "--project-root",
    "project_root",
    type=click.Path(file_okay=False, dir_okay=True),
    default=None,
    help="Default project root used when a tool call doesn't include a path",
)
@icontract.require(
    lambda allow_code_execution: isinstance(allow_code_execution, bool),
    "allow_code_execution must be a bool",
)
@icontract.require(
    lambda project_root: project_root is None or isinstance(project_root, str),
    "project_root must be None or a string",
)
@icontract.ensure(lambda result: result is None, "CLI commands return None")
def mcp(allow_code_execution: bool, project_root: str | None) -> None:
    """Boot the Serenecode MCP server over stdio.

    Exposes the verification pipeline as MCP tools an AI agent can call
    mid-edit. Register with Claude Code via:

        claude mcp add serenecode -- uv run serenecode mcp

    Tool paths and ``--project-root`` are not sandboxed: the client can
    point verification at any directory the user can read. With
    ``--allow-code-execution``, Levels 3-6 run project code like a local
    ``pytest``/import (see docs/SECURITY.md). Without that flag the server
    stays read-only for those levels.
    """
    try:
        from serenecode.mcp.server import run_stdio_server
    except ImportError as exc:
        click.echo(
            f"Error: the 'mcp' optional dependency is not installed.\n"
            f"Install with: uv add 'mcp>=1.0' or pip install 'serenecode[mcp]'\n"
            f"Underlying error: {exc}",
            err=True,
        )
        sys.exit(ExitCode.INTERNAL)
    run_stdio_server(
        project_root=project_root,
        allow_code_execution=allow_code_execution,
    )


@main.command()
@click.argument("path", default=".")
@click.option("--level", type=click.IntRange(1, 6), default=None, help="Verification level (1-6, default: from config template)")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["human", "json"]),
    default="human",
    help="Output format",
)
@click.option("--structural", is_flag=True, help="Run only structural check (Level 1)")
@click.option("--verify", is_flag=True, help="Run Levels 3-6 only")
@click.option("--per-condition-timeout", type=int, default=30, show_default=True, help="Timeout in seconds per condition for symbolic verification (Level 5)")
@click.option("--per-path-timeout", type=int, default=10, show_default=True, help="Timeout in seconds per execution path for symbolic verification (Level 5)")
@click.option("--module-timeout", type=int, default=300, show_default=True, help="Timeout in seconds per module for symbolic verification (Level 5)")
@click.option("--coverage-timeout", type=int, default=600, show_default=True, help="Timeout in seconds for the L3 coverage subprocess (whole pytest run, cached per project)")
@click.option("--workers", type=int, default=4, show_default=True, help="Number of parallel workers for symbolic verification (Level 5); SERENECODE_MAX_WORKERS overrides when set")
@click.option("--spec", "spec_path", default=None, help="Path to SPEC.md for traceability checking")
@click.option(
    "--project-root",
    "project_root",
    type=click.Path(file_okay=False, dir_okay=True, path_type=str),
    default=None,
    help=(
        "Repository root for module paths, SERENECODE.md lookup, and spec discovery. "
        "Defaults to PATH. Use when PATH is a subfolder but the project root is elsewhere."
    ),
)
@click.option(
    "--fail-on-advisory",
    is_flag=True,
    help="Exit 11 if dead-code advisories remain (even when verification passed).",
)
@click.option(
    "--allow-code-execution",
    is_flag=True,
    help=(
        "Allow Levels 3-6 to import and execute project code in-process (same trust as "
        "running pytest or `python -m` on the project). Not a sandbox — see docs/SECURITY.md."
    ),
)
@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(
    lambda level: level is None or is_valid_verification_level(level),
    "level must be between 1 and 6 when provided",
)
@icontract.require(
    lambda output_format: output_format in {"human", "json"},
    "output_format must be human or json",
)
@icontract.require(
    lambda per_condition_timeout: is_positive_int(per_condition_timeout),
    "per_condition_timeout must be at least 1",
)
@icontract.require(
    lambda per_path_timeout: is_positive_int(per_path_timeout),
    "per_path_timeout must be at least 1",
)
@icontract.require(
    lambda module_timeout: is_positive_int(module_timeout),
    "module_timeout must be at least 1",
)
@icontract.require(
    lambda coverage_timeout: is_positive_int(coverage_timeout),
    "coverage_timeout must be at least 1",
)
@icontract.require(
    lambda workers: is_positive_int(workers),
    "workers must be at least 1",
)
@icontract.require(
    lambda project_root: project_root is None or isinstance(project_root, str),
    "project_root must be None or a string",
)
@icontract.require(
    lambda fail_on_advisory: isinstance(fail_on_advisory, bool),
    "fail_on_advisory must be a bool",
)
@icontract.ensure(lambda result: result is None, "CLI commands return None")
def check(
    path: str,
    level: int | None,
    output_format: str,
    structural: bool,
    verify: bool,
    per_condition_timeout: int,
    per_path_timeout: int,
    module_timeout: int,
    coverage_timeout: int,
    workers: int,
    spec_path: str | None,
    project_root: str | None,
    fail_on_advisory: bool,
    allow_code_execution: bool,
) -> None:
    """Run verification checks on Python source files."""
    wall_start = time.monotonic()
    reader = LocalFileReader()

    workers = min(_env_int_or("SERENECODE_MAX_WORKERS", workers), 32)
    coverage_timeout = _env_int_or("SERENECODE_COVERAGE_TIMEOUT", coverage_timeout)

    config_search_root = project_root if project_root is not None else path

    # Load config first (needed to resolve default level)
    serenecode_md_path = find_serenecode_md(config_search_root, reader)
    if serenecode_md_path:
        config_content = reader.read_file(serenecode_md_path)
        config = parse_serenecode_md(config_content)
    else:
        from serenecode.config import default_config
        config = default_config()
        click.echo("Warning: No SERENECODE.md found, using default configuration.", err=True)

    # Determine effective level
    if structural:
        effective_level = 1
    elif level is not None:
        effective_level = level
        if verify:
            effective_level = max(effective_level, 3)
    else:
        effective_level = config.recommended_level
        if verify:
            effective_level = max(effective_level, 3)
    level = effective_level
    start_level = 3 if verify and not structural else 1

    if level >= 3 and not allow_code_execution:
        click.echo(f"Error: {_TRUST_REQUIRED_MESSAGE}", err=True)
        sys.exit(ExitCode.INTERNAL)

    # List files
    try:
        files = reader.list_python_files(path)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(ExitCode.INTERNAL)

    if not files:
        click.echo("No Python files found.")
        sys.exit(ExitCode.PASSED)

    module_search_root = project_root if project_root is not None else path

    # Build source file objects
    try:
        source_files = build_source_files(files, reader, module_search_root)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(ExitCode.INTERNAL)

    # Discover test files for L1 test-existence check
    test_stems = discover_test_file_stems(module_search_root, reader)

    # Read spec content for traceability checking
    try:
        spec_content, test_sources = _load_spec_inputs(module_search_root, spec_path, reader)
    except ConfigurationError as exc:
        click.echo(f"Error reading spec: {exc}", err=True)
        _echo_spec_traceability_hints(
            module_search_root, reader, spec_explicit_path=spec_path,
        )
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

    # Wire up adapters for higher levels
    type_checker = None
    coverage_analyzer = None
    property_tester = None
    symbolic_checker = None
    dead_code_analyzer: DeadCodeAnalyzer | None = None

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
        dead_code_analyzer = VultureDeadCodeAnalyzer()
    except ImportError:
        click.echo("Warning: vulture not available for dead-code analysis.", err=True)

    # Run pipeline with progress callback
    def _progress(msg: str) -> None:
        click.echo(msg, err=True)

    final_result = run_pipeline(
        source_files=source_files,
        level=level,
        start_level=start_level,
        config=config,
        type_checker=type_checker,
        coverage_analyzer=coverage_analyzer,
        property_tester=property_tester,
        symbolic_checker=symbolic_checker,
        dead_code_analyzer=dead_code_analyzer,
        progress=_progress,
        max_workers=workers,
        known_test_stems=test_stems,
        spec_content=spec_content,
        test_sources=test_sources,
    )

    # Format and output
    if output_format == "json":
        click.echo(format_json(final_result))
    else:
        click.echo(format_human(final_result))

    wall_elapsed = time.monotonic() - wall_start
    minutes, seconds = divmod(wall_elapsed, 60)
    if minutes >= 1:
        click.echo(f"Total wall time: {int(minutes)}m {seconds:.1f}s", err=True)
    else:
        click.echo(f"Total wall time: {seconds:.1f}s", err=True)

    # Exit with appropriate code
    if not final_result.passed:
        exit_code = _determine_exit_code(final_result)
        sys.exit(exit_code)

    if fail_on_advisory and final_result.summary.advisory_count > 0:
        click.echo(
            f"Exiting: {final_result.summary.advisory_count} dead-code advisory(ies) "
            "(--fail-on-advisory).",
            err=True,
        )
        sys.exit(ExitCode.ADVISORY)

    sys.exit(ExitCode.PASSED)


@main.command()
@click.argument("path", default=".")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["human", "json"]),
    default="human",
    help="Output format",
)
@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(
    lambda output_format: output_format in {"human", "json"},
    "output_format must be human or json",
)
@icontract.ensure(lambda result: result is None, "CLI commands return None")
def status(path: str, output_format: str) -> None:
    """Show verification status of the codebase."""
    reader = LocalFileReader()

    # Load config
    serenecode_md_path = find_serenecode_md(path, reader)
    if serenecode_md_path:
        config_content = reader.read_file(serenecode_md_path)
        config = parse_serenecode_md(config_content)
    else:
        from serenecode.config import default_config
        config = default_config()

    # List and check files
    try:
        files = reader.list_python_files(path)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(ExitCode.INTERNAL)

    if not files:
        click.echo("No Python files found.")
        return

    try:
        source_files = build_source_files(files, reader, path)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(ExitCode.INTERNAL)
    test_stems = discover_test_file_stems(path, reader)
    try:
        spec_content, test_sources = _load_spec_inputs(path, None, reader)
    except Exception as exc:
        click.echo(f"Error reading spec: {exc}", err=True)
        sys.exit(ExitCode.INTERNAL)
    result = run_pipeline(
        source_files, level=1, start_level=1, config=config,
        known_test_stems=test_stems,
        dead_code_analyzer=_maybe_make_dead_code_analyzer(),
        spec_content=spec_content,
        test_sources=test_sources,
    )

    if output_format == "json":
        click.echo(format_json(result))
    else:
        click.echo(format_human(result))


@main.command()
@click.argument("path", default=".")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["human", "json", "html"]),
    default="human",
    help="Report format",
)
@click.option("--output", "output_file", default=None, help="Write report to file")
@click.option(
    "--allow-code-execution",
    is_flag=True,
    help="Allow deep reports to import and execute project modules",
)
@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.require(
    lambda output_format: output_format in {"human", "json", "html"},
    "output_format must be human, json, or html",
)
@icontract.require(
    lambda output_file: output_file is None or is_non_empty_string(output_file),
    "output_file must be a non-empty string when provided",
)
@icontract.ensure(lambda result: result is None, "CLI commands return None")
def report(
    path: str,
    output_format: str,
    output_file: str | None,
    allow_code_execution: bool,
) -> None:
    """Generate a verification report for the entire project."""
    reader = LocalFileReader()

    # Load config
    serenecode_md_path = find_serenecode_md(path, reader)
    if serenecode_md_path:
        config_content = reader.read_file(serenecode_md_path)
        config = parse_serenecode_md(config_content)
    else:
        from serenecode.config import default_config
        config = default_config()

    # List and check files
    try:
        files = reader.list_python_files(path)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(ExitCode.INTERNAL)

    if not files:
        click.echo("No Python files found.")
        return

    try:
        source_files = build_source_files(files, reader, path)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(ExitCode.INTERNAL)
    # Reports use the project's recommended verification depth rather than
    # silently truncating to structural checks only.
    level = config.recommended_level
    if level >= 3 and not allow_code_execution:
        click.echo(f"Error: {_TRUST_REQUIRED_MESSAGE}", err=True)
        sys.exit(ExitCode.INTERNAL)
    type_checker = None
    coverage_analyzer = None
    property_tester = None
    symbolic_checker = None
    dead_code_analyzer = _maybe_make_dead_code_analyzer()

    if level >= 2:
        try:
            from serenecode.adapters.mypy_adapter import MypyTypeChecker
            type_checker = MypyTypeChecker()
        except ImportError:
            pass

    if level >= 3:
        try:
            from serenecode.adapters.coverage_adapter import CoverageAnalyzerAdapter
            coverage_analyzer = CoverageAnalyzerAdapter(allow_code_execution=True)
        except ImportError:
            click.echo("Warning: coverage not available for Level 3 checks.", err=True)

    if level >= 4:
        try:
            from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
            property_tester = HypothesisPropertyTester(allow_code_execution=True)
        except ImportError:
            pass

    if level >= 5:
        try:
            from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
            symbolic_checker = CrossHairSymbolicChecker(allow_code_execution=True)
        except ImportError:
            pass

    test_stems = discover_test_file_stems(path, reader)
    try:
        spec_content, test_sources = _load_spec_inputs(path, None, reader)
    except Exception as exc:
        click.echo(f"Error reading spec: {exc}", err=True)
        sys.exit(ExitCode.INTERNAL)
    final_result = run_pipeline(
        source_files,
        level=level,
        start_level=1,
        config=config,
        type_checker=type_checker,
        coverage_analyzer=coverage_analyzer,
        property_tester=property_tester,
        symbolic_checker=symbolic_checker,
        dead_code_analyzer=dead_code_analyzer,
        known_test_stems=test_stems,
        spec_content=spec_content,
        test_sources=test_sources,
    )

    # Format output
    if output_format == "json":
        formatted = format_json(final_result)
    elif output_format == "html":
        formatted = format_html(final_result)
    else:
        formatted = format_human(final_result)

    # Write to file or stdout
    if output_file:
        writer = LocalFileWriter()
        writer.write_file(output_file, formatted)
        click.echo(f"Report written to {output_file}")
    else:
        click.echo(formatted)


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

    try:
        test_files = reader.list_python_files(tests_dir)
    except Exception:
        return ()

    sources: list[tuple[str, str]] = []
    # Loop invariant: sources contains (path, content) for test_files[0..i]
    for test_file in test_files:
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
