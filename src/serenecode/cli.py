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
from serenecode.source_discovery import build_source_files, find_serenecode_md

_TRUST_REQUIRED_MESSAGE = (
    "Levels 3-6 import and execute project modules. "
    "Re-run with --allow-code-execution only for trusted code."
)


@click.group()
@icontract.ensure(lambda result: result is None, "CLI entrypoint returns None")
def main() -> None:
    """Serenecode — formal verification for AI-generated Python code."""


@main.command()
@click.option("--strict", "template", flag_value="strict", help="Use strict template (all rules mandatory)")
@click.option("--minimal", "template", flag_value="minimal", help="Use minimal template (contracts + types only)")
@click.argument("path", default=".")
@icontract.require(
    lambda template: template is None or is_valid_template_name(template),
    "template must be a recognized template name when provided",
)
@icontract.require(lambda path: is_non_empty_string(path), "path must be a non-empty string")
@icontract.ensure(lambda result: result is None, "CLI commands return None")
def init(template: str | None, path: str) -> None:
    """Initialize a Serenecode project."""
    if template is None:
        template = "default"
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
    )

    if result.serenecode_md_created:
        click.echo(f"Created SERENECODE.md ({template} template)")
    if result.claude_md_created:
        click.echo("Created CLAUDE.md with Serenecode directive")
    if result.claude_md_updated:
        click.echo("Updated CLAUDE.md with Serenecode directive")

    click.echo("Serenecode project initialized.")


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
    result = run_pipeline(source_files, level=1, start_level=1, config=config)

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

    final_result = run_pipeline(
        source_files,
        level=level,
        start_level=1,
        config=config,
        type_checker=type_checker,
        coverage_analyzer=coverage_analyzer,
        property_tester=property_tester,
        symbolic_checker=symbolic_checker,
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
    if check_result.level_achieved < check_result.level_requested:
        return min(check_result.level_achieved + 1, ExitCode.COMPOSITIONAL)
    return ExitCode.STRUCTURAL  # default to structural
