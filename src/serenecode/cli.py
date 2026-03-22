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

import click

from serenecode.adapters.local_fs import LocalFileReader, LocalFileWriter
from serenecode.config import parse_serenecode_md
from serenecode.core.pipeline import SourceFile, run_pipeline
from serenecode.init import initialize_project
from serenecode.models import ExitCode
from serenecode.reporter import format_html, format_human, format_json


@click.group()
def main() -> None:
    """Serenecode — formal verification for AI-generated Python code."""


@main.command()
@click.option("--strict", "template", flag_value="strict", help="Use strict template (all rules mandatory)")
@click.option("--minimal", "template", flag_value="minimal", help="Use minimal template (contracts + types only)")
@click.argument("path", default=".")
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
@click.option("--level", type=click.IntRange(1, 5), default=None, help="Verification level (1-5, default: from config template)")
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["human", "json"]),
    default="human",
    help="Output format",
)
@click.option("--structural", is_flag=True, help="Run only structural check (Level 1)")
@click.option("--verify", is_flag=True, help="Run Levels 3-5 only")
@click.option("--per-condition-timeout", type=int, default=120, show_default=True, help="Timeout in seconds per condition for symbolic verification (Level 4)")
@click.option("--per-path-timeout", type=int, default=60, show_default=True, help="Timeout in seconds per execution path for symbolic verification (Level 4)")
@click.option("--module-timeout", type=int, default=600, show_default=True, help="Timeout in seconds per module for symbolic verification (Level 4)")
@click.option("--workers", type=int, default=4, show_default=True, help="Number of parallel workers for symbolic verification (Level 4)")
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
) -> None:
    """Run verification checks on Python source files."""
    wall_start = time.monotonic()
    reader = LocalFileReader()

    # Load config first (needed to resolve default level)
    serenecode_md_path = _find_serenecode_md(path, reader)
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
    source_files = _build_source_files(files, reader)

    # Wire up adapters for higher levels
    type_checker = None
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
            from serenecode.adapters.hypothesis_adapter import HypothesisPropertyTester
            property_tester = HypothesisPropertyTester()
        except ImportError:
            click.echo("Warning: Hypothesis not available for Level 3 checks.", err=True)

    if level >= 4:
        try:
            from serenecode.adapters.crosshair_adapter import CrossHairSymbolicChecker
            symbolic_checker = CrossHairSymbolicChecker(
                per_condition_timeout=per_condition_timeout,
                per_path_timeout=per_path_timeout,
                module_timeout=module_timeout,
            )
        except ImportError:
            click.echo("Warning: CrossHair not available for Level 4 checks.", err=True)

    # Run pipeline with progress callback
    def _progress(msg: str) -> None:
        click.echo(msg, err=True)

    final_result = run_pipeline(
        source_files=source_files,
        level=level,
        config=config,
        type_checker=type_checker,
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
def status(path: str, output_format: str) -> None:
    """Show verification status of the codebase."""
    reader = LocalFileReader()

    # Load config
    serenecode_md_path = _find_serenecode_md(path, reader)
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

    source_files = _build_source_files(files, reader)
    result = run_pipeline(source_files, level=1, config=config)

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
def report(path: str, output_format: str, output_file: str | None) -> None:
    """Generate a verification report for the entire project."""
    reader = LocalFileReader()

    # Load config
    serenecode_md_path = _find_serenecode_md(path, reader)
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

    source_files = _build_source_files(files, reader)
    final_result = run_pipeline(source_files, level=1, config=config)

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


def _determine_exit_code(result: object) -> int:
    """Determine the CLI exit code from a failed CheckResult.

    Uses the verification level of the first failure to determine
    the appropriate exit code per spec Section 4.2.

    Args:
        result: A CheckResult with failures.

    Returns:
        An exit code integer (1-5 or 10).
    """
    from serenecode.models import CheckResult, CheckStatus, VerificationLevel

    check_result: CheckResult = result  # type: ignore[assignment]

    # Find the lowest failing level across all failed results
    min_level = 10  # start above any valid level
    # Loop invariant: min_level is the lowest failure level seen in results[0..i]
    for func_result in check_result.results:
        if func_result.status == CheckStatus.FAILED:
            # Loop invariant: checked details[0..j] for level
            for detail in func_result.details:
                level_val = detail.level.value
                if 1 <= level_val <= 5 and level_val < min_level:
                    min_level = level_val

    if min_level <= 5:
        return min_level
    return ExitCode.STRUCTURAL  # default to structural


def _build_source_files(
    file_paths: list[str],
    reader: LocalFileReader,
) -> tuple[SourceFile, ...]:
    """Build SourceFile objects from file paths.

    Args:
        file_paths: Paths to Python files.
        reader: File reader for reading contents.

    Returns:
        Tuple of SourceFile objects.
    """
    source_files: list[SourceFile] = []

    # Loop invariant: source_files contains SourceFile for file_paths[0..i]
    for fp in file_paths:
        try:
            source = reader.read_file(fp)
        except Exception:
            continue

        # Derive module path for architecture checks
        module_path = fp
        if "src/serenecode/" in fp:
            module_path = fp.split("src/serenecode/")[-1]

        # Derive importable module name
        importable = _derive_importable_module(fp)

        source_files.append(SourceFile(
            file_path=fp,
            module_path=module_path,
            source=source,
            importable_module=importable,
        ))

    return tuple(source_files)


def _derive_importable_module(file_path: str) -> str | None:
    """Derive an importable Python module path from a file path.

    Args:
        file_path: Path to a Python file.

    Returns:
        Importable module path, or None if it can't be determined.
    """
    # Normalize path
    fp = file_path.replace(os.sep, "/")

    # Handle src/ layout
    if "/src/" in fp:
        module_part = fp.split("/src/")[-1]
    elif fp.startswith("src/"):
        module_part = fp[4:]
    else:
        module_part = fp

    # Remove .py extension and convert to dotted path
    if module_part.endswith(".py"):
        module_part = module_part[:-3]
    else:
        return None

    # Replace / with .
    module_path = module_part.replace("/", ".")

    # Handle __init__
    if module_path.endswith(".__init__"):
        module_path = module_path[:-9]

    return module_path if module_path else None


def _find_serenecode_md(path: str, reader: LocalFileReader) -> str | None:
    """Find SERENECODE.md by searching up from the given path.

    Args:
        path: Starting path to search from.
        reader: File reader for checking existence.

    Returns:
        Path to SERENECODE.md if found, None otherwise.
    """
    # Check common locations
    candidates = [
        os.path.join(path, "SERENECODE.md"),
        "SERENECODE.md",
    ]

    # Also check parent directories
    current = os.path.abspath(path)
    # Loop invariant: candidates contains all checked paths so far
    for _ in range(10):  # max 10 levels up
        candidate = os.path.join(current, "SERENECODE.md")
        if candidate not in candidates:
            candidates.append(candidate)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    # Loop invariant: we've checked candidates[0..i] for existence
    for candidate in candidates:
        if reader.file_exists(candidate):
            return candidate

    return None
