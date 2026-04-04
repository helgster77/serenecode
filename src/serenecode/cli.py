"""CLI entry point for Serenecode.

This module is the composition root for the command-line interface.
It wires adapters to ports and delegates to core logic. As a thin
adapter layer, it is exempt from full contract requirements but
must have type annotations and pass mypy.
"""

from __future__ import annotations

import sys
import time

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
from serenecode.core.pipeline import run_pipeline
from serenecode.init import initialize_project
from serenecode.models import CheckResult, ExitCode
from serenecode.reporter import format_html, format_human, format_json
from serenecode.source_discovery import build_source_files, discover_test_file_stems, find_serenecode_md

_TRUST_REQUIRED_MESSAGE = (
    "Levels 3-6 import and execute project modules. "
    "Only run on trusted code with allow_code_execution=True / --allow-code-execution."
)


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
    click.echo("  [1] I already have a spec (SPEC.md)")
    click.echo("      Serenecode will help you turn it into a traceable")
    click.echo("      implementation plan and verify full coverage.")
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

    # Warning
    click.echo("Important: these choices will be written to SERENECODE.md.")
    click.echo("Serenecode does not support changing them once implementation")
    click.echo("has started. The conventions become the contract between you,")
    click.echo("your coding assistant, and the verification tool.")
    click.echo("")
    if not click.confirm("Proceed?", default=True):
        click.echo("Aborted.")
        return

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
    if spec_mode == "existing":
        click.echo("")
        click.echo("Place your spec in the project directory before starting.")
        click.echo("Your assistant will convert it to SereneCode format, validate")
        click.echo("it, create an implementation plan, and build from it.")


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

    Checks that the spec has well-formed REQ-xxx identifiers,
    no duplicates, no gaps, and descriptions on all requirements.
    Run this before starting implementation.
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
@click.option("--workers", type=int, default=4, show_default=True, help="Number of parallel workers for symbolic verification (Level 5)")
@click.option("--spec", "spec_path", default=None, help="Path to SPEC.md for traceability checking")
@click.option(
    "--allow-code-execution",
    is_flag=True,
    help="Allow Levels 3-6 to import and execute project modules",
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
    lambda workers: is_positive_int(workers),
    "workers must be at least 1",
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
    workers: int,
    spec_path: str | None,
    allow_code_execution: bool,
) -> None:
    """Run verification checks on Python source files."""
    wall_start = time.monotonic()
    reader = LocalFileReader()

    # Load config first (needed to resolve default level)
    serenecode_md_path = find_serenecode_md(path, reader)
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

    # Build source file objects
    try:
        source_files = build_source_files(files, reader, path)
    except Exception as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(ExitCode.INTERNAL)

    # Discover test files for L1 test-existence check
    test_stems = discover_test_file_stems(path, reader)

    # Read spec content for traceability checking
    spec_content: str | None = None
    test_sources: tuple[tuple[str, str], ...] = ()
    if spec_path is not None:
        try:
            spec_content = reader.read_file(spec_path)
        except Exception as exc:
            click.echo(f"Error reading spec: {exc}", err=True)
            sys.exit(ExitCode.INTERNAL)
        # Collect test sources for Verifies: tag scanning
        test_sources = _collect_test_sources(path, reader)

    # Wire up adapters for higher levels
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
            coverage_analyzer = CoverageAnalyzerAdapter(allow_code_execution=True)
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
    if final_result.passed:
        sys.exit(ExitCode.PASSED)
    else:
        # Find the lowest failing level from the results
        exit_code = _determine_exit_code(final_result)
        sys.exit(exit_code)


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
    result = run_pipeline(
        source_files, level=1, start_level=1, config=config,
        known_test_stems=test_stems,
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
    final_result = run_pipeline(
        source_files,
        level=level,
        start_level=1,
        config=config,
        type_checker=type_checker,
        coverage_analyzer=coverage_analyzer,
        property_tester=property_tester,
        symbolic_checker=symbolic_checker,
        known_test_stems=test_stems,
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
