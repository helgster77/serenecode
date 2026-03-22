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

_CLAUDE_MD_SECTIONS = {
    "default": """\
## Serenecode

All code in this project MUST follow the standards defined in SERENECODE.md. \
Read SERENECODE.md before writing or modifying any code. Every public function \
must have icontract preconditions and postconditions. Every class must have \
invariants. Follow the architectural patterns specified in SERENECODE.md.

### Verification

After each work iteration (implementing a feature, fixing a bug, refactoring), \
offer to run verification before considering the task complete.

**Quick structural check (seconds):**
```bash
serenecode check src/ --structural
```

**Full verification with property testing (minutes):**
```bash
serenecode check src/ --level 3
```

**Generate an HTML report:**
```bash
serenecode report src/ --format html --output report.html
```

If verification fails, read the error messages and fix the issues. Each failure \
includes the function name, file, line number, and a suggested fix. Iterate \
until all checks pass.
""",
    "strict": """\
## Serenecode (Strict Mode)

All code in this project MUST follow the standards defined in SERENECODE.md. \
Read SERENECODE.md before writing or modifying any code. Every function — \
public and private — must have icontract preconditions and postconditions. \
Every class must have invariants. No exemptions.

### Verification

After each work iteration (implementing a feature, fixing a bug, refactoring), \
you MUST run verification before considering the task complete. Do not skip this.

**Quick structural check (seconds):**
```bash
serenecode check src/ --structural
```

**Full verification with property testing (minutes):**
```bash
serenecode check src/ --level 3
```

**Full verification including symbolic and compositional (minutes):**
```bash
serenecode check src/ --level 5
```

**Generate an HTML report:**
```bash
serenecode report src/ --format html --output report.html
```

If verification fails, read the error messages and fix the issues. Each failure \
includes the function name, file, line number, and a suggested fix. Iterate \
until all checks pass. Do not commit code that fails verification.
""",
    "minimal": """\
## Serenecode

All code in this project should follow the conventions in SERENECODE.md. \
Public functions must have icontract preconditions and postconditions.

### Verification

After completing a feature or fix, consider running a quick check:

```bash
serenecode check src/ --structural
```

If issues are found, fix them before moving on.
""",
}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InitResult:
    """Result of project initialization.

    Indicates which files were created or updated during init.
    """

    serenecode_md_created: bool
    claude_md_created: bool
    claude_md_updated: bool
    template_used: str


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
def generate_serenecode_md(template: str) -> str:
    """Return the SERENECODE.md content for the given template name.

    Args:
        template: One of 'default', 'strict', or 'minimal'.

    Returns:
        The SERENECODE.md markdown content.
    """
    from serenecode.templates import content as template_content

    return template_content.get_template(template)


@icontract.require(
    lambda template: is_valid_template_name(template),
    "template must be a valid template name",
)
@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty string",
)
def generate_claude_md_section(template: str = "default") -> str:
    """Return the Serenecode directive section for CLAUDE.md.

    The section content varies by template — strict mode uses stronger
    language and requires verification before commits.

    Args:
        template: The template name ('default', 'strict', or 'minimal').

    Returns:
        The markdown section to add to CLAUDE.md.
    """
    return _CLAUDE_MD_SECTIONS[template]


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
        content = generate_serenecode_md(template)
        file_writer.write_file(serenecode_path, content)
        serenecode_md_created = True

    # Generate and write/update CLAUDE.md
    claude_section = generate_claude_md_section(template)
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
    )
