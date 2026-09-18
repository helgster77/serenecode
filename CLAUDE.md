## Serenecode

All code in this project MUST follow the same standards SereneCode ships to users: the embedded templates in `src/serenecode/templates/content.py` (default / strict / minimal) define the conventions the structural checker enforces. Read the relevant template before writing or modifying any code. Non-exempt public production functions with caller-supplied inputs must have icontract preconditions, and public production functions must have postconditions. Classes with state need meaningful invariants. Test functions follow test-quality rules without production contract, annotation, or docstring requirements. Follow the architectural patterns specified there.

Pre-existing `*_SPEC.md` or PRD files are narrative inputs; project-root `SPEC.md` with REQ/INT identifiers is auto-discovered for traceability; `--spec PATH` can select a structured spec elsewhere. `Implements:` and `Verifies:` tags are read from function and class docstrings only, and one tag may list several comma-separated identifiers; a tag in a module docstring is inert and is reported as misplaced.

### Verification (prefer MCP while editing)

After each work iteration (implementing a feature, fixing a bug, refactoring), run verification before considering the task complete.

**Preferred — MCP tools in the IDE (per-symbol, fast feedback):** use **`serenecode_check_function`** (or `serenecode_check_file`) on the code you just changed. Prefer this over shell `serenecode check` during active editing. Reserve **`serenecode_check`** for whole-tree / CI-style runs. If MCP wiring is unclear, run `serenecode doctor` for install and registration hints.

**CLI — batch / CI (when not using MCP for this step):**

Quick structural check (seconds):
```bash
serenecode check src/ --structural
```

Full verification with coverage and property testing (minutes):
```bash
serenecode check src/ --level 4 --allow-code-execution
```

Full verification including symbolic and compositional (minutes):
```bash
serenecode check src/ --level 6 --allow-code-execution
```

### Reading Verification Output

Each finding includes function name, file path, line number, a message, and a suggestion. The output summary uses four statuses:

- **passed** — this record passed its own check stage, within that backend’s scope and bounds.
- **failed** — a violation was found. Read the message and suggestion to fix it.
- **skipped** — the tool could not run (e.g. tool not installed, module not importable). Investigate why.
- **exempt** — excluded from this check stage or reported as a nonblocking advisory. Review the reason and whether deeper evidence is needed. `advisory_count` is included in the exempt count, and the human summary renders it that way — for example, `57 exempt (33 advisory)`.

`summary.total_functions` counts records, not unique functions. Read the aggregate verdict and achieved level as well as individual records. Function-scoped MCP requests run the file pipeline; L3 can run all project tests. Missing or ambiguous targets fail, and `verify_fixed` requires a passing scoped check. See [verification semantics](docs/VERIFICATION_LEVELS.md) and the dated [verification record](docs/VERIFICATION_STATUS.md).

### Fixing Failures by Level

**Level 1 (structural)** — Missing contracts or annotations. The suggestion names the specific parameters or return type. Add the missing decorator. Level 1 also fails on:
  - A contract that cannot be enforced as written — a condition over `*args`/`**kwargs`, a condition naming a parameter the signature does not have, or `lambda result: ...` on a function annotated `-> None`. Fix the contract; do not delete it.
  - A precondition that is genuinely unnecessary because the parameter's annotated domain is already fully valid: waive it with `# no-precondition: <reason>`, never with a type-shaped tautology.
  - An `Implements:`/`Verifies:` tag in a module docstring, which nothing reads. Move it to the implementing or testing symbol.

**Level 2 (types)** — mypy type errors. The suggestion includes the mypy error code and a fix direction. Fix the type annotation or the expression.

**Level 3 (coverage)** — Tests failed, collection/execution failed, or coverage is below threshold. Passing coverage does not excuse failing tests. The output shows:
  - Which functions have insufficient coverage and their exact uncovered lines
  - Suggested test code for each uncovered path
  - Mock assessment: dependencies receive heuristic REQUIRED/OPTIONAL mock suggestions; review whether a mock or real integration is appropriate
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

A condition can only name parameters the decorated signature supplies.
Level 1 rejects conditions over `*args` / `**kwargs` (use icontract's `_ARGS`
and `_KWARGS` placeholders instead), conditions naming a parameter that does
not exist, and `lambda result: ...` on a function annotated `-> None` — none
of these is enforceable as written.

Where a parameter's annotated domain is already entirely valid, waive the
precondition with `# no-precondition: <reason>` above the `def` or the topmost
decorator rather than writing a type-shaped tautology. The reason is mandatory.

Protocol classes and stateless adapters do not need `@icontract.invariant`. Add `# no-invariant: <reason>` above the class definition if the class has no state to constrain.

### Verification Scope

The output shows what was and wasn't checked. Exempt items (adapters, ports, non-primitive signatures) are visible in the output — not silently omitted. If verification fails, read the error messages and fix the issues. Iterate until all checks pass.
