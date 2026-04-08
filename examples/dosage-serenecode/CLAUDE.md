## Serenecode (Strict Mode)

All code in this project MUST follow the standards defined in SERENECODE.md. Read SERENECODE.md before writing or modifying any code. Every function — public and private — with caller-supplied inputs must have icontract preconditions, and every function must have postconditions. Every class must have invariants. No exemptions.

### Verification

After each work iteration (implementing a feature, fixing a bug, refactoring), you MUST run verification before considering the task complete. Do not skip this.

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

Levels 3-6 import and execute project modules. Only use `--allow-code-execution` for trusted code.

If verification fails, read the error messages and fix the issues. Each failure includes the function name, file, line number, and a suggested fix. Iterate until all checks pass. Do not commit code that fails verification.

### Testing

You MUST write tests for every function. Do not skip this.

- Unit tests for core functions in `tests/unit/`
- Integration tests for adapters in `tests/integration/`
- Property-based tests (Hypothesis) for pure functions

Run `pytest -q` before considering any task complete. Do not commit code without passing tests.

### Spec-Driven Workflow

This project has an existing requirements document (any name). Follow the Spec Traceability section in SERENECODE.md for the full workflow. The key steps are:

1. Read the narrative spec, SERENECODE.md, and SPEC.md before writing any code.
2. If SPEC.md is missing or not in SereneCode format (REQ-xxx headings and, for critical interactions, INT-xxx entries), rewrite the narrative document into SPEC.md following the "Preparing a SereneCode-Ready Spec" instructions in SERENECODE.md. A PRD or `*_SPEC.md` alone does not satisfy traceability — only SPEC.md does. Validate with `serenecode spec SPEC.md`.
3. Create an implementation plan mapping each REQ and each critical INT to functions, modules, and contracts. Get user approval before writing code.
4. Implement and tag with `Implements: REQ-xxx` / `Implements: INT-xxx`. Test and tag with `Verifies: REQ-xxx` / `Verifies: INT-xxx`.
5. Run `serenecode check src/ --spec SPEC.md` to verify full traceability.

Pre-existing `*_SPEC.md` or PRD files are narrative inputs only. Traceability and `serenecode check --spec` apply exclusively to SPEC.md (REQ/INT identifiers).
