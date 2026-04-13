"""Report generation for Serenecode verification results.

This module provides pure formatting functions that convert CheckResult
objects into human-readable terminal output or JSON strings matching
the spec output format.

This is a core module — no I/O imports are permitted.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import icontract

from serenecode.models import ADVISORY_FINDING_TYPES, CheckResult, CheckStatus, FunctionResult

__all__ = [
    "format_human",
    "format_json",
    "format_html",
]

# Appended to human-formatted check output so CLI users see the MCP edit-loop path.
_MCP_EDITOR_HINT = (
    "Editor / AI loop: for per-symbol fixes without re-running the whole tree, use the "
    "SereneCode MCP tools in your IDE (e.g. serenecode_check_function). "
    "Run `serenecode doctor` to verify the optional MCP install and registration hints."
)


@icontract.require(
    lambda check_result: check_result.level_requested >= 1,
    "check_result must have a valid requested level",
)
@icontract.ensure(
    lambda check_result, result: ("PASSED" if check_result.passed else "FAILED") in result,
    "output must contain the correct status marker",
)
def format_human(check_result: CheckResult) -> str:
    """Format a CheckResult as human-readable terminal output.

    Implements: REQ-006

    Condenses passing files into a single summary line and only
    expands files that contain failures or skips with details.

    Args:
        check_result: The verification result to format.

    Returns:
        A formatted string suitable for terminal display.
    """
    lines: list[str] = []

    # Header
    status_marker = "PASSED" if check_result.passed else "FAILED"
    verdict = check_result.summary.verdict
    lines.append(f"Serenecode Check — {status_marker} (verdict: {verdict})")
    lines.append("=" * 50)
    lines.append("")

    by_file = _group_results_by_file(check_result)
    _format_human_file_sections(lines, by_file)
    _format_human_summary(lines, check_result.summary)

    return "\n".join(lines)


@icontract.require(
    lambda check_result: check_result.level_requested >= 1,
    "check_result must have a valid requested level",
)
@icontract.ensure(
    lambda result: result.startswith("{") and result.endswith("}"),
    "output must be a valid JSON object string",
)
def format_json(check_result: CheckResult) -> str:
    """Format a CheckResult as JSON matching the spec Section 4.3 format.

    Args:
        check_result: The verification result to format.

    Returns:
        A JSON string matching the specification output format.
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    base = check_result.to_dict()

    output: dict[str, object] = {
        "version": base["version"],
        "timestamp": timestamp,
        "passed": base["passed"],
        "level_requested": base["level_requested"],
        "level_achieved": base["level_achieved"],
        "summary": base["summary"],
        "results": base["results"],
    }

    return json.dumps(output, indent=2)


@icontract.require(
    lambda check_result: check_result.level_requested >= 1,
    "check_result must have a valid requested level",
)
@icontract.ensure(
    lambda result: result.startswith("<!DOCTYPE html>") and "</html>" in result,
    "output must be a complete HTML document",
)
def format_html(check_result: CheckResult) -> str:
    """Format a CheckResult as an HTML verification report.

    Implements: REQ-006

    Produces a self-contained HTML document with expandable sections,
    verification level badges, and styled results suitable for
    compliance documentation or CI/CD artifacts.

    Args:
        check_result: The verification result to format.

    Returns:
        A complete HTML document as a string.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    status_class = "passed" if check_result.passed else "failed"
    status_text = "PASSED" if check_result.passed else "FAILED"
    summary = check_result.summary

    by_file = _group_results_by_file(check_result)
    files_html = _build_html_file_sections(by_file)

    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Serenecode Verification Report</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; margin: 2rem; color: #24292f; background: #f6f8fa; }}
  .header {{ background: #fff; border: 1px solid #d0d7de; border-radius: 6px; padding: 1.5rem; margin-bottom: 1.5rem; }}
  .header h1 {{ margin: 0 0 0.5rem; font-size: 1.5rem; }}
  .status {{ display: inline-block; padding: 0.25rem 0.75rem; border-radius: 20px; font-weight: 600; font-size: 0.9rem; }}
  .status.passed {{ background: #dafbe1; color: #116329; }}
  .status.failed {{ background: #ffebe9; color: #82071e; }}
  .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 1rem; margin-top: 1rem; }}
  .summary-item {{ text-align: center; }}
  .summary-item .number {{ font-size: 2rem; font-weight: 700; }}
  .summary-item .label {{ color: #656d76; font-size: 0.85rem; }}
  .file-section {{ background: #fff; border: 1px solid #d0d7de; border-radius: 6px; margin-bottom: 0.75rem; }}
  .file-section summary {{ padding: 0.75rem 1rem; cursor: pointer; font-weight: 500; }}
  .file-section.failed summary {{ color: #82071e; }}
  .file-section.passed summary {{ color: #116329; }}
  .results-table {{ width: 100%; border-collapse: collapse; margin: 0; }}
  .results-table th {{ background: #f6f8fa; padding: 0.5rem; text-align: left; border-bottom: 1px solid #d0d7de; font-size: 0.85rem; }}
  .results-table td {{ padding: 0.5rem; border-bottom: 1px solid #eee; font-size: 0.85rem; vertical-align: top; }}
  .pass-row {{ background: #f0fff4; }}
  .fail-row {{ background: #fff5f5; }}
  .badge {{ display: inline-block; padding: 0.15rem 0.5rem; border-radius: 10px; font-size: 0.75rem; font-weight: 600; }}
  .badge-1 {{ background: #ddf4ff; color: #0550ae; }}
  .badge-2 {{ background: #dafbe1; color: #116329; }}
  .badge-3 {{ background: #fff8c5; color: #4d2d00; }}
  .badge-4 {{ background: #fbefff; color: #5e3a8a; }}
  .badge-5 {{ background: #ffebe9; color: #82071e; }}
  .badge-6 {{ background: #fff0f0; color: #6e0b14; }}
  .detail {{ margin: 0.25rem 0; }}
  .suggestion {{ color: #0550ae; font-style: italic; }}
  pre.suggestion {{ background: #f6f8fa; padding: 0.5rem; border-radius: 4px; font-size: 0.8rem; overflow-x: auto; white-space: pre-wrap; }}
  .counterexample {{ color: #82071e; font-family: monospace; font-size: 0.8rem; }}
  .footer {{ margin-top: 1.5rem; color: #656d76; font-size: 0.8rem; text-align: center; }}
</style>
</head>
<body>
<div class="header">
  <h1>Serenecode Verification Report</h1>
  <span class="status {status_class}">{status_text}</span>
  <span style="margin-left: 1rem; color: #656d76;">Verdict: {_escape_html(summary.verdict)}</span>
  <span style="margin-left: 1rem; color: #656d76;">Generated {timestamp}</span>
  <div class="summary">
    <div class="summary-item"><div class="number">{summary.total_functions}</div><div class="label">Total</div></div>
    <div class="summary-item"><div class="number" style="color:#116329">{summary.passed_count}</div><div class="label">Passed</div></div>
    <div class="summary-item"><div class="number" style="color:#82071e">{summary.failed_count}</div><div class="label">Failed</div></div>
    <div class="summary-item"><div class="number" style="color:#656d76">{summary.skipped_count}</div><div class="label">Skipped</div></div>
    <div class="summary-item"><div class="number" style="color:#8b6914">{summary.exempt_count}</div><div class="label">Exempt</div></div>
    <div class="summary-item"><div class="number" style="color:#8b6914">{summary.advisory_count}</div><div class="label">Advisory</div></div>
    <div class="summary-item"><div class="number">{summary.duration_seconds:.2f}s</div><div class="label">Duration</div></div>
  </div>
</div>
{files_html}
<div class="footer">
  Serenecode v{_escape_html(check_result.version)} &mdash; Formal verification for AI-generated Python code
</div>
</body>
</html>"""


def _group_results_by_file(
    check_result: CheckResult,
) -> dict[str, list[FunctionResult]]:
    """Group function results by file path."""
    by_file: dict[str, list[FunctionResult]] = {}
    # Loop invariant: by_file contains results grouped for results[0..i]
    for func_result in check_result.results:
        by_file.setdefault(func_result.file, []).append(func_result)
    return by_file


def _format_human_file_sections(
    lines: list[str],
    by_file: dict[str, list[FunctionResult]],
) -> None:
    """Append human-formatted file sections to lines."""
    # Loop invariant: lines contains formatted output for all files processed so far
    for file_path, func_results in sorted(by_file.items()):
        passed = [r for r in func_results if r.status == CheckStatus.PASSED]
        failed = [r for r in func_results if r.status == CheckStatus.FAILED]
        skipped = [r for r in func_results if r.status == CheckStatus.SKIPPED]
        advisories = [
            r for r in func_results
            if r.status == CheckStatus.EXEMPT
            and any(detail.finding_type in ADVISORY_FINDING_TYPES for detail in r.details)
        ]
        exempt = [
            r for r in func_results
            if r.status == CheckStatus.EXEMPT
            and not any(detail.finding_type in ADVISORY_FINDING_TYPES for detail in r.details)
        ]

        if not failed and not skipped and not advisories and not exempt:
            lines.append(f"  {file_path} — {len(passed)} passed")
            continue
        if not failed and not skipped and not passed and not advisories:
            lines.append(f"  {file_path} — exempt")
            continue

        parts = []
        if passed:
            parts.append(f"{len(passed)} passed")
        if failed:
            parts.append(f"{len(failed)} failed")
        if skipped:
            parts.append(f"{len(skipped)} skipped")
        if advisories:
            parts.append(f"{len(advisories)} advisory")
        if exempt:
            parts.append(f"{len(exempt)} exempt")
        lines.append(f"  {file_path} — {', '.join(parts)}")

        _format_human_func_results(lines, func_results)
        lines.append("")


def _format_human_func_results(
    lines: list[str],
    func_results: list[FunctionResult],
) -> None:
    """Append non-passing function results with details to lines."""
    # Loop invariant: lines contains output for non-passing func_results[0..j]
    for func_result in func_results:
        is_dead_code_advisory = (
            func_result.status == CheckStatus.EXEMPT
            and any(detail.finding_type in ADVISORY_FINDING_TYPES for detail in func_result.details)
        )
        if func_result.status == CheckStatus.PASSED:
            continue
        if func_result.status == CheckStatus.EXEMPT and not is_dead_code_advisory:
            continue
        if func_result.status == CheckStatus.FAILED:
            marker = "FAIL"
        elif func_result.status == CheckStatus.SKIPPED:
            marker = "SKIP"
        else:
            marker = "NOTE"
        lines.append(f"    [{marker}] {func_result.function} (line {func_result.line})")

        # Loop invariant: lines contains all details for details[0..k]
        for detail in func_result.details:
            lines.append(f"           {detail.message}")
            if detail.suggestion:
                lines.append(f"           -> {detail.suggestion}")
            if detail.counterexample:
                lines.append(f"           counterexample: {detail.counterexample}")


def _format_human_summary(lines: list[str], summary: object) -> None:
    """Append summary section and MCP hint to lines."""
    lines.append("-" * 50)
    summary_parts = [
        f"{summary.total_functions} checked",
        f"{summary.passed_count} passed",
        f"{summary.failed_count} failed",
        f"{summary.skipped_count} skipped",
    ]
    if summary.exempt_count > 0:
        summary_parts.append(f"{summary.exempt_count} exempt")
    if summary.advisory_count > 0:
        summary_parts.append(f"{summary.advisory_count} advisory")
    lines.append(", ".join(summary_parts))
    lines.append(f"Duration: {summary.duration_seconds:.3f}s")

    if summary.failed_count >= 5:
        lines.append("")
        lines.append(
            f"Tip: {summary.failed_count} findings to address. "
            "Consider spawning subagents to fix groups of related "
            "findings in parallel."
        )

    lines.append("")
    lines.append(_MCP_EDITOR_HINT)


def _build_html_file_sections(
    by_file: dict[str, list[FunctionResult]],
) -> str:
    """Build the HTML file sections for the report."""
    file_sections: list[str] = []
    # Loop invariant: file_sections contains HTML for files processed so far
    for file_path, func_results in sorted(by_file.items()):
        file_passed = all(r.status in (CheckStatus.PASSED, CheckStatus.EXEMPT) for r in func_results)
        file_class = "passed" if file_passed else "failed"
        file_icon = "&#x2714;" if file_passed else "&#x2718;"

        rows = _build_html_rows(func_results)
        table_html = "\n".join(rows)
        file_sections.append(
            f'<details class="file-section {file_class}">'
            f"<summary>{file_icon} {_escape_html(file_path)}</summary>"
            f'<table class="results-table">'
            f"<thead><tr><th>Function</th><th>Line</th><th>Level</th><th>Status</th><th>Details</th></tr></thead>"
            f"<tbody>{table_html}</tbody>"
            f"</table>"
            f"</details>"
        )

    return "\n".join(file_sections)


def _build_html_rows(func_results: list[FunctionResult]) -> list[str]:
    """Build HTML table rows for a file's function results."""
    rows: list[str] = []
    # Loop invariant: rows contains table rows for func_results[0..j]
    for fr in func_results:
        is_dead_code_advisory = (
            fr.status == CheckStatus.EXEMPT
            and any(detail.finding_type in ADVISORY_FINDING_TYPES for detail in fr.details)
        )
        if fr.status == CheckStatus.EXEMPT and not is_dead_code_advisory:
            continue

        row_class = "pass-row" if fr.status in (CheckStatus.PASSED, CheckStatus.EXEMPT) else "fail-row"
        status_badge = _level_badge(fr.level_achieved)
        detail_html = _build_html_detail(fr.details) if fr.details else ""

        rows.append(
            f'<tr class="{row_class}">'
            f'<td>{_escape_html(fr.function)}</td>'
            f"<td>{fr.line}</td>"
            f"<td>{status_badge}</td>"
            f"<td>{'advisory' if is_dead_code_advisory else fr.status.value}</td>"
            f"<td>{detail_html}</td>"
            f"</tr>"
        )
    return rows


def _build_html_detail(details: tuple) -> str:
    """Build HTML for a function result's detail entries."""
    detail_parts: list[str] = []
    # Loop invariant: detail_parts contains detail HTML for details[0..k]
    for d in details:
        part = f'<div class="detail">{_escape_html(d.message)}'
        if d.suggestion:
            escaped_suggestion = _escape_html(d.suggestion)
            if "\n" in d.suggestion:
                part += f'<br><pre class="suggestion">{escaped_suggestion}</pre>'
            else:
                part += f'<br><span class="suggestion">&#x27A1; {escaped_suggestion}</span>'
        if d.counterexample is not None and d.counterexample:
            try:
                ce_text = json.dumps(d.counterexample, default=str)
            except (TypeError, ValueError):
                ce_text = str(d.counterexample)
            part += f'<br><span class="counterexample">Counterexample: {_escape_html(ce_text)}</span>'
        part += "</div>"
        detail_parts.append(part)
    return "".join(detail_parts)


@icontract.require(lambda level: level >= 0, "level must be non-negative")
@icontract.ensure(lambda result: '<span class="badge' in result, "result must be an HTML badge element")
def _level_badge(level: int) -> str:
    """Generate an HTML badge for a verification level.

    Args:
        level: The verification level (0-5).

    Returns:
        An HTML span element with the level badge.
    """
    level_names = {
        0: "None",
        1: "L1 Structural",
        2: "L2 Types",
        3: "L3 Coverage",
        4: "L4 Properties",
        5: "L5 Symbolic",
        6: "L6 Compositional",
    }
    name = level_names.get(level, f"L{level}")
    badge_class = f"badge-{min(level, 6)}" if level > 0 else "badge-1"
    return f'<span class="badge {badge_class}">{name}</span>'


@icontract.require(lambda text: text is not None, "text must be provided")
@icontract.ensure(lambda result: "<" not in result and ">" not in result, "escaped text must not contain raw angle brackets")
def _escape_html(text: str) -> str:
    """Escape HTML special characters.

    Args:
        text: Raw text to escape.

    Returns:
        HTML-safe text.
    """
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )
