"""Project initialization logic for Serenecode.

This module handles the `serenecode init` command logic. It generates
SERENECODE.md and CLAUDE.md files from templates and manages the
initialization workflow.

Core logic functions are pure (no I/O). The initialize_project
orchestrator uses ports for file system access.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import icontract

from serenecode.contracts.predicates import is_valid_template_name
from serenecode.ports.file_system import FileReader, FileWriter

# ---------------------------------------------------------------------------
# SERENECODE.md template content (embedded as string constants)
# ---------------------------------------------------------------------------

_SPEC_MD_PLACEHOLDER = """\
# SPEC.md — SereneCode traceability spec

**Source:** SPEC.source.md

> `serenecode check --spec` and REQ/INT tags apply only to this file. \
Set **Source:** to your narrative spec path(s), or merge requirements into SPEC.source.md.

---

### REQ-001: First testable requirement (replace)
Describe behavior, acceptance criteria, and edge cases.
"""

_SPEC_SOURCE_PLACEHOLDER = """\
# SPEC.source.md — narrative or upstream requirements (optional)

Free-form notes, or content merged from a PRD / `*_SPEC.md`. Keep SPEC.md **Source:** \
pointing here when this file is authoritative for narrative context. Convert behaviors \
into `### REQ-xxx` / `### INT-xxx` sections in SPEC.md per SERENECODE.md.
"""

_SPEC_WORKFLOW_EXISTING = """\

### Spec-Driven Workflow

This project has an existing requirements document (any name). Follow the Spec \
Traceability section in SERENECODE.md for the full workflow. The key steps are:

1. Read the narrative spec, SERENECODE.md, and SPEC.md before writing any code.
2. If SPEC.md is missing or not in SereneCode format (REQ-xxx headings and, \
for critical interactions, INT-xxx entries), rewrite the narrative document \
into SPEC.md following the "Preparing a SereneCode-Ready Spec" instructions \
in SERENECODE.md. A PRD or `*_SPEC.md` alone does not satisfy traceability — \
only SPEC.md does. Validate with `serenecode spec SPEC.md`.
3. Create an implementation plan mapping each REQ and each critical INT to \
functions, modules, and contracts. Get user approval before writing code.
4. Implement and tag with `Implements: REQ-xxx` / `Implements: INT-xxx`. \
Test and tag with `Verifies: REQ-xxx` / `Verifies: INT-xxx`.
5. Run the full verification command from the Verification section above \
(with `--spec SPEC.md` if not auto-discovered) to verify traceability and correctness together.
"""

_TRACEABILITY_REMINDER = """\

Pre-existing `*_SPEC.md` or PRD files are narrative inputs only. Traceability \
and `serenecode check --spec` apply exclusively to SPEC.md (REQ/INT identifiers).
"""

_SPEC_WORKFLOW_GENERATE = """\

### Spec-Driven Workflow

This project's spec will be written alongside the code. Follow the Spec \
Traceability section in SERENECODE.md for the full workflow. The key steps are:

1. Read SERENECODE.md before writing any code.
2. Write SPEC.md with the user following the format in SERENECODE.md. \
Each requirement must be a testable behavior with a REQ-xxx identifier, and \
critical interactions may be added as INT-xxx integration points. \
Validate with `serenecode spec SPEC.md`.
3. Create an implementation plan mapping each REQ and each critical INT to \
functions, modules, and contracts. Get user approval before writing code.
4. Implement and tag with `Implements: REQ-xxx` / `Implements: INT-xxx`. \
Test and tag with `Verifies: REQ-xxx` / `Verifies: INT-xxx`.
5. Run the full verification command from the Verification section above \
(with `--spec SPEC.md` if not auto-discovered) to verify traceability and correctness together.
"""

_CLAUDE_MD_BASE = {
    "default": """\
## Serenecode

All code in this project MUST follow the standards defined in SERENECODE.md. \
Read SERENECODE.md before writing or modifying any code. Every public function \
with caller-supplied inputs must have icontract preconditions, and every \
public function must have postconditions. Every class must have invariants. \
Follow the architectural patterns specified in SERENECODE.md.

### Verification

After each work iteration (implementing a feature, fixing a bug, refactoring), \
run verification before considering the task complete.

**Preferred — MCP while editing:** Register the Serenecode MCP server once, then \
call **`serenecode_check_function`** (or `serenecode_check_file`) on the code you \
just changed. Prefer this over shell `serenecode check` during active editing; \
use **`serenecode_check`** for full-tree or CI-style runs. Run **`serenecode doctor`** \
if the optional MCP install or IDE registration is unclear.

```bash
claude mcp add serenecode -- uv run serenecode mcp
```

Add `--allow-code-execution` to the command if you want Levels 3-6 \
(coverage, properties, symbolic, compositional) available to the agent. \
Also use `serenecode_suggest_contracts`, `serenecode_verify_fixed`, \
`serenecode_uncovered` in the inner loop; `serenecode_validate_spec`, \
`serenecode_req_status`, `serenecode_integration_status` for traceability; \
`serenecode_dead_code` for dead-code review.

**CLI — batch / CI (use the full command, not just structural):**

Full verification (required before considering any task complete):
```bash
serenecode check src/ --level 4 --allow-code-execution
```

Quick structural smoke test (seconds, use only during active iteration):
```bash
serenecode check src/ --structural
```

Levels 3-6 import and execute project modules. Only use \
`--allow-code-execution` for trusted code.

If verification fails, read the error messages and fix the issues. Each failure \
includes the function name, file, line number, and a suggested fix. Iterate \
until all checks pass.

### Testing

Write tests alongside code. Every new module must have a corresponding \
test file. Contracts verify invariants at runtime, but tests verify \
behavior — both are required.

Run `pytest -q` after writing tests. Do not consider a task complete \
until tests exist and pass.
""",
    "strict": """\
## Serenecode (Strict Mode)

All code in this project MUST follow the standards defined in SERENECODE.md. \
Read SERENECODE.md before writing or modifying any code. Every function — \
public and private — with caller-supplied inputs must have icontract \
preconditions, and every function must have postconditions. Every class must \
have invariants. No exemptions.

### Verification

After each work iteration (implementing a feature, fixing a bug, refactoring), \
you MUST run verification before considering the task complete. Do not skip this.

**MCP (required for the edit loop):** Register the Serenecode MCP server and \
call **`serenecode_check_function`** after every function you write or edit. \
Prefer MCP over shell `serenecode check` during active work; use the CLI for \
full-tree or CI runs. Run **`serenecode doctor`** if MCP install or registration \
is unclear.

```bash
claude mcp add serenecode -- uv run serenecode mcp --allow-code-execution
```

Use `serenecode_suggest_contracts`, `serenecode_verify_fixed`, \
`serenecode_uncovered`, `serenecode_req_status` / `serenecode_integration_status`, \
and `serenecode_dead_code` as documented in SERENECODE.md.

**CLI — batch / CI (use the full command, not just structural):**

Full verification (required before considering any task complete):
```bash
serenecode check src/ --level 6 --allow-code-execution
```

Quick structural smoke test (seconds, use only during active iteration):
```bash
serenecode check src/ --structural
```

Levels 3-6 import and execute project modules. Only use \
`--allow-code-execution` for trusted code.

If verification fails, read the error messages and fix the issues. Each failure \
includes the function name, file, line number, and a suggested fix. Iterate \
until all checks pass. Do not commit code that fails verification.

### Testing

You MUST write tests for every function. Do not skip this.

- Unit tests for core functions in `tests/unit/`
- Integration tests for adapters in `tests/integration/`
- Property-based tests (Hypothesis) for pure functions

Run `pytest -q` before considering any task complete. Do not commit \
code without passing tests.
""",
    "minimal": """\
## Serenecode

All code in this project should follow the conventions in SERENECODE.md. \
Public functions with caller-supplied inputs must have icontract \
preconditions, and public functions should have postconditions.

### Verification

After completing a feature or fix, verify your work.

**Preferred — MCP:** Register the Serenecode MCP server and use \
`serenecode_check_function` on symbols you change. Run `serenecode doctor` \
for setup hints.

```bash
claude mcp add serenecode -- uv run serenecode mcp
```

**CLI (use before considering any task complete):**

```bash
serenecode check src/
```

This runs the project's configured verification level. Add `--allow-code-execution` \
for deeper checks. Use `--structural` only as a quick smoke test during iteration.

If issues are found, fix them before moving on.

### Testing

Write basic tests for public functions. Run `pytest -q` to verify.
""",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@icontract.invariant(
    lambda self: not (self.claude_md_created and self.claude_md_updated),
    "CLAUDE.md cannot be both created and updated in the same operation",
)
@dataclass(frozen=True)
class InitResult:
    """Result of project initialization.

    Indicates which files were created or updated during init.
    """

    serenecode_md_created: bool
    claude_md_created: bool
    claude_md_updated: bool
    template_used: str
    spec_mode: str = "generate"
    spec_md_placeholder_created: bool = False
    spec_source_placeholder_created: bool = False


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------


@icontract.require(
    lambda template: is_valid_template_name(template),
    "template must be a valid template name",
)
@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty string",
)
def generate_serenecode_md(
    template: str,
    include_spec_traceability: bool = True,
) -> str:
    """Return the SERENECODE.md content for the given template name.

    Args:
        template: One of 'default', 'strict', or 'minimal'.
        include_spec_traceability: Whether to include the spec traceability section.

    Returns:
        The SERENECODE.md markdown content.
    """
    from serenecode.templates import content as template_content

    return template_content.get_template_with_options(
        template, include_spec_traceability=include_spec_traceability,
    )


@icontract.require(
    lambda template: is_valid_template_name(template),
    "template must be a valid template name",
)
@icontract.require(
    lambda spec_mode: spec_mode in ("existing", "generate"),
    "spec_mode must be 'existing' or 'generate'",
)
@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty string",
)
def generate_claude_md_section(
    template: str = "default",
    spec_mode: str = "generate",
) -> str:
    """Return the Serenecode directive section for CLAUDE.md.

    The section content varies by template and spec workflow.

    Args:
        template: The template name ('default', 'strict', or 'minimal').
        spec_mode: Either 'existing' (user has a spec) or 'generate'
            (user will write one with their coding assistant).

    Returns:
        The markdown section to add to CLAUDE.md.
    """
    base = _CLAUDE_MD_BASE[template]
    workflow = _SPEC_WORKFLOW_EXISTING if spec_mode == "existing" else _SPEC_WORKFLOW_GENERATE
    return base.rstrip() + "\n" + workflow + _TRACEABILITY_REMINDER


@icontract.require(
    lambda serenecode_section: isinstance(serenecode_section, str) and len(serenecode_section) > 0,
    "serenecode_section must be a non-empty string",
)
@icontract.ensure(
    lambda serenecode_section, result: serenecode_section in result,
    "result must contain the serenecode section",
)
def merge_claude_md(existing_content: str | None, serenecode_section: str) -> str:
    """Merge the Serenecode section into existing CLAUDE.md content.

    If the existing content already contains "## Serenecode", do not
    add a duplicate. Otherwise, append the section.

    Args:
        existing_content: Current CLAUDE.md content, or None if no file exists.
        serenecode_section: The Serenecode directive section to add.

    Returns:
        The merged CLAUDE.md content.
    """
    if existing_content is None:
        return serenecode_section

    if "## Serenecode" in existing_content:
        return existing_content

    return existing_content.rstrip() + "\n\n" + serenecode_section


# ---------------------------------------------------------------------------
# Orchestrator (uses ports)
# ---------------------------------------------------------------------------


@icontract.require(
    lambda template: is_valid_template_name(template),
    "template must be a valid template name",
)
@icontract.ensure(
    lambda result: result.serenecode_md_created or result.serenecode_md_existed,
    "initialization must either create or find existing SERENECODE.md",
)
def initialize_project(
    directory: str,
    template: str,
    file_reader: FileReader,
    file_writer: FileWriter,
    confirm_callback: Callable[[str], bool] | None = None,
    spec_mode: str = "generate",
) -> InitResult:
    """Initialize a Serenecode project in the given directory.

    Creates SERENECODE.md from the selected template and sets up
    CLAUDE.md with the Serenecode directive.

    Args:
        directory: Project root directory path.
        template: Template name ('default', 'strict', or 'minimal').
        file_reader: A FileReader implementation.
        file_writer: A FileWriter implementation.
        confirm_callback: Optional callback for user confirmation prompts.
            If None, proceeds without confirmation.
        spec_mode: Either 'existing' (user has a spec) or 'generate'
            (user will write one with their coding assistant).

    Returns:
        An InitResult describing what was created/modified.
    """
    import os

    serenecode_path = os.path.join(directory, "SERENECODE.md")
    claude_path = os.path.join(directory, "CLAUDE.md")

    serenecode_md_created = False
    claude_md_created = False
    claude_md_updated = False

    # Generate and write SERENECODE.md
    serenecode_exists = file_reader.file_exists(serenecode_path)
    should_write_serenecode = True
    if serenecode_exists and confirm_callback is not None:
        should_write_serenecode = confirm_callback(
            "SERENECODE.md already exists. Overwrite?"
        )

    if should_write_serenecode:
        # Best-effort backup of the existing file before overwrite
        if serenecode_exists:
            backup_path = serenecode_path + ".bak"
            # silent-except: best-effort backup, do not block init on backup failure
            try:
                existing_content = file_reader.read_file(serenecode_path)
                file_writer.write_file(backup_path, existing_content)
            except Exception:
                pass  # Best-effort — don't block init on backup failure
        content = generate_serenecode_md(template, include_spec_traceability=True)
        file_writer.write_file(serenecode_path, content)
        serenecode_md_created = True

    # Generate and write/update CLAUDE.md
    claude_section = generate_claude_md_section(template, spec_mode=spec_mode)
    claude_exists = file_reader.file_exists(claude_path)
    if claude_exists:
        existing = file_reader.read_file(claude_path)
        if "## Serenecode" not in existing:
            should_update = True
            if confirm_callback is not None:
                should_update = confirm_callback(
                    "Add Serenecode directive to existing CLAUDE.md?"
                )
            if should_update:
                merged = merge_claude_md(existing, claude_section)
                file_writer.write_file(claude_path, merged)
                claude_md_updated = True
    else:
        file_writer.write_file(claude_path, claude_section)
        claude_md_created = True

    spec_md_path = os.path.join(directory, "SPEC.md")
    spec_source_path = os.path.join(directory, "SPEC.source.md")
    spec_md_placeholder_created = False
    spec_source_placeholder_created = False
    if not file_reader.file_exists(spec_md_path):
        file_writer.write_file(spec_md_path, _SPEC_MD_PLACEHOLDER)
        spec_md_placeholder_created = True
    if not file_reader.file_exists(spec_source_path):
        file_writer.write_file(spec_source_path, _SPEC_SOURCE_PLACEHOLDER)
        spec_source_placeholder_created = True

    return InitResult(
        serenecode_md_created=serenecode_md_created,
        claude_md_created=claude_md_created,
        claude_md_updated=claude_md_updated,
        template_used=template,
        spec_mode=spec_mode,
        spec_md_placeholder_created=spec_md_placeholder_created,
        spec_source_placeholder_created=spec_source_placeholder_created,
    )
