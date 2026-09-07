## Serenecode (Strict Mode)

All code in this project MUST follow the standards defined in SERENECODE.md. Read SERENECODE.md before writing or modifying any code. Every function — public and private — with caller-supplied inputs must have icontract preconditions, and every function must have postconditions. Every class must have meaningful invariants when they have state. No preset path exemptions; test-specific rules and backend eligibility still apply.

### Verification

After each work iteration (implementing a feature, fixing a bug, refactoring), you MUST run verification before considering the task complete. Do not skip this.

**MCP (required for the edit loop):** Register the Serenecode MCP server and call **`serenecode_check_function`** after every function you write or edit. Prefer MCP over shell `serenecode check` during active work; use the CLI for full-tree or CI runs. Run **`serenecode doctor`** if MCP install or registration is unclear.

```bash
claude mcp add serenecode -- uv run serenecode mcp --allow-code-execution
```

Use `serenecode_suggest_contracts`, `serenecode_verify_fixed`, `serenecode_uncovered`, `serenecode_req_status` / `serenecode_integration_status`, and `serenecode_dead_code` as documented in SERENECODE.md.

**CLI — batch / CI (use the full command, not just structural):**

Full verification (required before considering any task complete):
```bash
serenecode check src/ --level 6 --allow-code-execution
```

Quick structural smoke test (seconds, use only during active iteration):
```bash
serenecode check src/ --structural
```

Levels 3-6 import and execute project modules. Only use `--allow-code-execution` for trusted code.

If verification fails, read the error messages and fix the issues. Each failure includes the function name, file, line number, and a suggested fix. Iterate until all checks pass. Do not commit code that fails verification.

### Testing

You MUST write tests for production functions. Do not skip this.

- Unit tests for core functions in `tests/unit/`
- Integration tests for adapters in `tests/integration/`
- Property-based tests (Hypothesis) for pure functions

Run `pytest -q` before considering any task complete. Do not commit code without passing tests.

### Spec-Driven Workflow

This project has an existing requirements document (any name). Follow the Spec Traceability section in SERENECODE.md for the full workflow. The key steps are:

1. Read the narrative spec, SERENECODE.md, and SPEC.md before writing any code.
2. If SPEC.md is missing or not in SereneCode format (REQ-xxx headings and, for critical interactions, INT-xxx entries), rewrite the narrative document into SPEC.md following the "Preparing a SereneCode-Ready Spec" instructions in SERENECODE.md. A PRD or `*_SPEC.md` alone does not satisfy traceability — use structured REQ/INT content. SPEC.md is auto-discovered; `--spec PATH` selects another structured spec. Validate with `serenecode spec SPEC.md`.
3. Create an implementation plan mapping each REQ and each critical INT to functions, modules, and contracts. Get user approval before writing code.
4. Implement and tag with `Implements: REQ-xxx` / `Implements: INT-xxx`. Test and tag with `Verifies: REQ-xxx` / `Verifies: INT-xxx`.
5. Run the full verification command from the Verification section above (with `--spec SPEC.md` if not auto-discovered) to check traceability, contracts, and behavior within the reported scope and bounds.

Pre-existing `*_SPEC.md` or PRD files are narrative inputs only. Traceability uses REQ/INT identifiers in the auto-discovered SPEC.md, or a structured spec selected with `--spec PATH`.
