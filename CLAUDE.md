## Serenecode

All code in this project MUST follow the standards defined in SERENECODE.md. Read SERENECODE.md before writing or modifying any code. Every public function must have icontract preconditions and postconditions. Every class with state must have invariants. Follow the architectural patterns specified in SERENECODE.md.

### Verification Commands

After each work iteration (implementing a feature, fixing a bug, refactoring), offer to run verification before considering the task complete.

**Quick structural check (seconds):**
```bash
serenecode check src/ --structural
```

**Full verification with coverage and property testing (minutes):**
```bash
serenecode check src/ --level 4 --allow-code-execution
```

**Full verification including symbolic and compositional (minutes):**
```bash
serenecode check src/ --level 6 --allow-code-execution
```

### Reading Verification Output

Each finding includes function name, file path, line number, a message, and a suggestion. The output summary uses four statuses:

- **passed** — verified at the requested level.
- **failed** — a violation was found. Read the message and suggestion to fix it.
- **skipped** — the tool could not run (e.g. tool not installed, module not importable). Investigate why.
- **exempt** — intentionally excluded from this check level (adapter code, Protocol classes, functions with non-primitive parameters). No action needed unless the function should be verified.

### Fixing Failures by Level

**Level 1 (structural)** — Missing contracts or annotations. The suggestion names the specific parameters or return type. Add the missing decorator.

**Level 2 (types)** — mypy type errors. The suggestion includes the mypy error code and a fix direction. Fix the type annotation or the expression.

**Level 3 (coverage)** — Test coverage is below threshold. The output shows:
  - Which functions have insufficient coverage and their exact uncovered lines
  - Suggested test code for each uncovered path
  - Mock assessment: each dependency is classified as REQUIRED (external I/O — must mock) or OPTIONAL (internal code — consider using the real implementation)
  - If "no tests found", write tests first. Coverage analysis measures existing test quality.

**Level 4 (properties)** — Hypothesis found inputs that violate a postcondition. The counterexample shows the exact failing inputs (e.g. `x=-1, result=-2`). Either:
  1. Fix the implementation so the postcondition holds for these inputs, OR
  2. Add a `@icontract.require` precondition to exclude these inputs if they are not valid.

**Level 5 (symbolic)** — CrossHair found a counterexample via symbolic execution. Same fix pattern as Level 4, but the counterexample comes from the solver rather than random testing.

**Level 6 (compositional)** — Cross-module architectural violations. Fix the dependency direction, add missing contracts at module boundaries, or correct interface mismatches.

### Writing Contracts

When adding contracts, write meaningful conditions that constrain behavior:

```python
# GOOD — constrains real behavior
@icontract.require(lambda items: len(items) > 0, "items must not be empty")
@icontract.ensure(lambda items, result: min(items) <= result <= max(items), "result within range")

# BAD — tautological, verifies nothing
@icontract.ensure(lambda result: True, "always passes")
```

Protocol classes and stateless adapters do not need `@icontract.invariant`. Add `# no-invariant: <reason>` above the class definition if the class has no state to constrain.

### Verification Scope

The output shows what was and wasn't checked. Exempt items (adapters, ports, non-primitive signatures) are visible in the output — not silently omitted. If verification fails, read the error messages and fix the issues. Iterate until all checks pass.
