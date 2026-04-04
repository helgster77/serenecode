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

_SPEC_WORKFLOW_EXISTING = """\

### Spec-Driven Workflow

This project has an existing spec document. Follow the Spec Traceability \
section in SERENECODE.md for the full workflow. The key steps are:

1. Read the existing spec and SERENECODE.md before writing any code.
2. If the spec is not already in SereneCode format (REQ-xxx headings), \
convert it into SPEC.md following the "Preparing a SereneCode-Ready Spec" \
instructions in SERENECODE.md. Validate with `serenecode spec SPEC.md`.
3. Create an implementation plan mapping each REQ to functions, modules, \
and contracts. Get user approval before writing code.
4. Implement and tag with `Implements: REQ-xxx`. Test and tag with \
`Verifies: REQ-xxx`.
5. Run `serenecode check src/ --spec SPEC.md` to verify full traceability.
"""

_SPEC_WORKFLOW_GENERATE = """\

### Spec-Driven Workflow

This project's spec will be written alongside the code. Follow the Spec \
Traceability section in SERENECODE.md for the full workflow. The key steps are:

1. Read SERENECODE.md before writing any code.
2. Write SPEC.md with the user following the format in SERENECODE.md. \
Each requirement must be a testable behavior with a REQ-xxx identifier. \
Validate with `serenecode spec SPEC.md`.
3. Create an implementation plan mapping each REQ to functions, modules, \
and contracts. Get user approval before writing code.
4. Implement and tag with `Implements: REQ-xxx`. Test and tag with \
`Verifies: REQ-xxx`.
5. Run `serenecode check src/ --spec SPEC.md` to verify full traceability.
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
offer to run verification before considering the task complete.

**Quick structural check (seconds):**
```bash
serenecode check src/ --structural
```

**Full verification with property testing (minutes):**
```bash
serenecode check src/ --level 4 --allow-code-execution
```

**Spec traceability check:**
```bash
serenecode check src/ --spec SPEC.md
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

**Quick structural check (seconds):**
```bash
serenecode check src/ --structural
```

**Full verification with property testing (minutes):**
```bash
serenecode check src/ --level 4 --allow-code-execution
```

**Full verification including symbolic and compositional (minutes):**
```bash
serenecode check src/ --level 6 --allow-code-execution
```

**Spec traceability check:**
```bash
serenecode check src/ --spec SPEC.md
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

After completing a feature or fix, run a quick check:

```bash
serenecode check src/ --structural
```

**Spec traceability check:**
```bash
serenecode check src/ --spec SPEC.md
```

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
    return base.rstrip() + "\n" + workflow


@icontract.require(
    lambda serenecode_section: isinstance(serenecode_section, str) and len(serenecode_section) > 0,
    "serenecode_section must be a non-empty string",
)
@icontract.ensure(
    lambda result: isinstance(result, str),
    "result must be a string",
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
    lambda result: isinstance(result, InitResult),
    "result must be an InitResult",
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

    return InitResult(
        serenecode_md_created=serenecode_md_created,
        claude_md_created=claude_md_created,
        claude_md_updated=claude_md_updated,
        template_used=template,
        spec_mode=spec_mode,
    )
