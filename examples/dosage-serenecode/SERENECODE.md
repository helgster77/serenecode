# SERENECODE.md — Strict Project Conventions

This file governs how all code in this project must be written. Any AI coding agent MUST read this file in its entirety before writing or modifying any code. **No preset path exemptions.** Every production function — public and private — must have contracts. Test-specific rules and backend eligibility still apply.

Verification command: `serenecode check src/ --level 6 --allow-code-execution`

Levels 3-6 import and execute project modules. Only use `--allow-code-execution` for trusted code.

---

## Complete Example

This illustrates common patterns. Adapt the contracts and input bounds to the application:

```python
"""Module docstring describing purpose and architecture role.

This is a core module — no I/O operations are permitted.
"""

import icontract
from dataclasses import dataclass


@icontract.invariant(lambda self: self.balance >= 0, "balance must be non-negative")
@dataclass(frozen=True)
class Account:
    """An immutable account record."""

    name: str
    balance: float


@icontract.require(lambda items: 0 < len(items) <= 100 and all(-1000 <= x <= 1000 for x in items), "1 to 100 items, each between -1000 and 1000")
@icontract.ensure(lambda items, result: min(items) - 1e-9 <= result <= max(items) + 1e-9, "result within range, allowing rounding tolerance")
def compute_mean(items: list[float]) -> float:
    """Compute the arithmetic mean."""
    total = 0.0
    # Loop invariant: total is the sum of items[0..i]
    for item in items:
        total += item
    return total / len(items)
```

---

## Contract Standards

### Public Functions

Every non-exempt public production function MUST have `@icontract.require` and `@icontract.ensure` with description strings: `@icontract.require(lambda x: x > 0, "x must be positive")`

Functions with no meaningful parameters may omit `@icontract.require`.

### Private Functions

Private production functions (prefixed with `_`) MUST have contracts and type annotations, including one-line helpers. Functions without caller-supplied inputs may omit preconditions.

### Class Invariants

Every class with state MUST have `@icontract.invariant`. Invariants must constrain actual state — tautological invariants like `lambda self: True` provide no verification value. If a class is truly stateless (Protocol, stateless adapter), omit the invariant and document why.

---

## Type Annotation Standards

- All function signatures MUST have complete type annotations on every parameter kind (including positional-only, keyword-only, variadic, and private helper parameters) and the return type.
- No use of `Any` in core modules — use `Protocol`, `Union`, or generics.
- Generic types must be fully parameterized (`list[str]` not `list`).
- Use modern type syntax (Python 3.10+): `X | None` not `Optional[X]`.

---

## Documentation Standards

- Every module MUST have a module-level docstring.
- Every public function and class MUST have a docstring.

---

## Architecture Standards

```
src/yourproject/
├── core/        # Pure logic. No I/O. No os/pathlib/subprocess imports.
├── ports/       # Protocol interfaces only.
├── adapters/    # I/O implementations.
└── cli.py       # Thin entry point.
```

Core modules (`core/`, models, contracts, checkers) MUST NOT import I/O libraries (`os`, `pathlib`, `subprocess`, `requests`, `socket`, `shutil`, `tempfile`, `glob`). Inject dependencies through function parameters.

---

## Error Handling Standards

Only domain-specific exceptions permitted in core modules. Never raise bare `Exception`, `ValueError`, `TypeError`, `RuntimeError`, `KeyError`, `IndexError`, or `AttributeError` in core.

---

## Loop and Recursion Standards

- Every loop MUST include a comment describing the loop invariant.
- Recursive functions MUST document the variant (decreasing measure).
- Prefer bounded iteration over unbounded `while`.

---

## Naming Conventions

- Modules: `snake_case.py`. Classes: `PascalCase`. Functions: `snake_case`.

---

## Testing Standards

Contracts verify invariants at runtime. Tests verify behavior. Both are required — they are complementary, not substitutes.

### Required Tests

- **Every production function** — public and private — must have corresponding tests.
- **Core modules**: Unit tests and property-based tests (Hypothesis) for pure functions.
- **Adapters**: Integration tests covering success and failure paths.
- **Edge cases**: Boundary conditions and regression tests for every discovered bug.
- Test file convention: `tests/unit/test_<module>.py`, `tests/integration/test_<adapter>.py`.
- Test names must describe the behavior being tested.

### Property-Based Testing

Pure functions with contracts should have Hypothesis tests that verify contracts hold across a wide range of inputs:

```python
from hypothesis import given, settings
from hypothesis import strategies as st

@given(items=st.lists(st.floats(min_value=-1000, max_value=1000, allow_nan=False, allow_infinity=False), min_size=1, max_size=100))
@settings(max_examples=200, deadline=None)
def test_compute_mean_satisfies_contracts(items: list[float]) -> None:
    result = compute_mean(items)
    assert min(items) - 1e-9 <= result <= max(items) + 1e-9
```

### Workflow

When writing any new function:
1. Write the function with contracts and type annotations.
2. Write the implementation.
3. Write tests that verify the function's behavior.
4. Run `pytest -q` and fix any failures.
5. Run `serenecode check src/ --level 6 --allow-code-execution` and fix findings.

Do not commit code without passing tests and verification.

### Reading verification output

`serenecode check` runs at strict level can take several minutes — `mypy`, `coverage.py`, `Hypothesis`, `CrossHair`, and the compositional checker all execute against the full source tree. The output is correspondingly long. **You MUST read the entire output before acting** — never truncate, never re-run just to "see it again." Each finding includes a function name, file, line number, message, and suggestion; all of those are needed to fix the issue. Re-running the tool because you only read the first few findings wastes minutes of the user's time and produces the same output.

If the output is genuinely too long to fit in one read, use `--format json` and parse it programmatically — but the human-readable format is designed to be read in full. Process all findings in a single batch, group related ones, and address them together rather than starting a new check after each fix. Spawn subagents to fix groups of related findings in parallel when there are many.

---

## Module Health

Module health checks run in L1, including normal higher-level runs. Warnings are advisory. Errors block verification. Strict mode uses tighter thresholds.

| Metric              | Warning | Error | What to do                                         |
|---------------------|---------|-------|----------------------------------------------------|
| File length (lines) | 400     | 700   | Split into focused, single-responsibility modules  |
| Function length     | 30      | 60    | Extract helper functions for distinct steps         |
| Parameter count     | 4       | 6     | Group parameters into a dataclass or config         |
| Class methods       | 10      | 18    | Extract cohesive method groups into new classes     |

Skip all module health checks with `--skip-module-health`.

---

## No Preset Path Exemptions

Strict mode has no preset path exemptions. Production CLI and adapter modules follow the conventions above. Test files use test-quality rules; they do not require production contracts or tests of tests. Stateless classes, Protocols, explicit suppressions, and backend eligibility rules still have special handling.

---

## Verification Scope

These are project writing conventions, not a claim that verification has already
passed. Run the verification command and retain its output. Contract checks hold
only while enabled; Python optimization or explicit decorator settings can disable
them. Frozen dataclasses do not freeze contained lists or sets, and invariants do
not continuously monitor in-place mutations.

Test files receive applicable test-quality checks without production contracts,
annotations, docstrings, or tests of test files. A passing result record refers to
its own stage. `summary.total_functions` counts records, not distinct functions;
`advisory_count` is included in `exempt`. For nonempty source scopes, L3–L5 each
need at least one passing record and no failed or skipped records. Later stages
cannot erase an earlier gap. An empty source scope provides no execution evidence.

L3 requires a successful pytest run and adequate coverage. L4 samples a restricted
input domain with a default budget of up to 100 examples per eligible function.
L5 searches eligible functions within analysis budgets; no counterexample found
is not a proof. L6 checks architectural/interface structure and contract presence,
not logical compatibility of contracts. Strict does not eliminate backend exclusions.

Module health is omitted when L1 is skipped, such as with `--verify`. Thresholds
are exceeded strictly: equality alone does not trigger a finding. Editing threshold
tables does not configure overrides; the Markdown parser retains preset numbers.

## Code Quality Standards

Default and Strict enable syntax-based checks for stubs, mutable defaults,
production assertions, debug printing in core, recognized dangerous calls,
unfinished-work markers, assertion-free tests, and simple tautological contracts.
Minimal disables these extra rules by default. Read each finding's suggestion for
its supported opt-out marker and provide a reason when suppressing a finding.
A clean check does not establish the absence of equivalent, undetected patterns.

## MCP Integration

Install SereneCode with its `mcp` extra in the project's dependency environment.
`serenecode doctor` reports package availability and registration hints; it does
not execute all backends. Register `serenecode mcp` as a stdio server in your client.
Add `--allow-code-execution` only for trusted projects when L3–L6 are needed.

Use `serenecode_check_file` or `serenecode_check_function` for scoped feedback and
`serenecode_check` for project checks. Function names may be qualified, such as
`Calculator.total`; missing or ambiguous names fail. Function requests run the
file pipeline, and L3 may run the whole test suite. Coverage caching lasts within
a request, not across edits. Scope does not guarantee a fast execution time.

`serenecode_verify_fixed` confirms a fix only if the message substring is absent
and the scoped check passes. `serenecode_uncovered`, `serenecode_suggest_test`, and
`serenecode_suggest_contracts` provide findings or scaffolds to review. Spec tools
include `serenecode_validate_spec`, `serenecode_list_reqs`,
`serenecode_list_integrations`, `serenecode_req_status`,
`serenecode_integration_status`, and `serenecode_orphans`.
`serenecode_dead_code` and `serenecode_module_health` support maintenance review.

---

## Spec Traceability

If requirements live in another file (PRD, README, `*_SPEC.md`, etc.), that file is the narrative source — **not** the traceability spec. Create or update **SPEC.md** with `REQ-xxx` / `INT-xxx` identifiers. A root SPEC.md is auto-discovered; `serenecode check --spec PATH` can select a structured spec elsewhere. Narrative prose alone does not provide REQ/INT traceability. Use a `**Source:** …` line at the top of SPEC.md pointing to the narrative path(s), or `**Source:** none — this SPEC.md is authoritative`.

This project uses two identifier types in `SPEC.md`:

- `REQ-xxx` for behavioral requirements
- `INT-xxx` for explicit integration points between components

Every declared requirement and integration point must be implemented and tested.

### Preparing a SereneCode-Ready Spec

If the project has an existing spec, PRD, design document, or requirements list that is not yet in SereneCode format, convert it into SPEC.md before writing any code. Follow these steps:

1. Read the source document in its entirety.
2. Identify every distinct, testable requirement. Each requirement must describe a single behavior that can be verified — not a vague goal, a heading, or an implementation detail.
3. Add a traceability anchor line to SPEC.md, e.g. `**Source:** path/to/narrative_spec.md` (multiple paths allowed in prose), or `**Source:** none — this SPEC.md is authoritative` when there is no separate narrative file.
4. Write SPEC.md with one heading per requirement in this format:

```markdown
### REQ-001: Short description of the requirement
Detailed explanation of what the system must do. Include acceptance criteria, input constraints, expected outputs, and edge cases.
```

5. Number requirements sequentially with no gaps (REQ-001, REQ-002, ...). Use 3-digit zero-padded numbers (or 4-digit for larger specs).
6. If the source document contains non-functional requirements, constraints, or background context that is not directly testable, include it in SPEC.md as regular prose outside of REQ headings. Only testable behaviors get REQ identifiers.
7. For critical interactions that AI coding agents could easily wire up incorrectly, add explicit integration points in this format:

```markdown
### INT-001: Short description of the integration point
Kind: call
Source: CheckoutService.checkout
Target: PaymentGateway.charge
Supports: REQ-003, REQ-004
```

Supported kinds are `call` and `implements`.
8. Validate the spec before proceeding:

```bash
serenecode spec SPEC.md
```

This checks that REQ and INT identifiers are well-formed, sequential with no gaps, free of duplicates, have descriptions, and that every `INT-xxx` entry has the required fields. Do not proceed to implementation planning until `serenecode spec` passes.

### Implementation Planning

After the spec is validated, create an implementation plan before writing code. The plan maps every REQ-xxx and every critical `INT-xxx` to:

- The specific function or class that will implement it.
- The module it belongs in (e.g. `src/core/orders.py`).
- The key contracts (preconditions and postconditions) it needs.
- The test strategy (unit test, property test, or both).

Get user approval on the plan before proceeding. The plan is where traceability is designed — the tooling verifies it afterwards.

### Implementation Tagging

Functions that implement a requirement include an `Implements:` tag in their docstring:

```python
def authenticate_user(email: str, password: str) -> Session:
    """Authenticate a user with email and password.

    Implements: REQ-001
    """
    ...
```

A function may implement multiple requirements:

```python
def validate_and_create_session(email: str, password: str) -> Session:
    """Validate credentials and create an authenticated session.

    Implements: REQ-001, REQ-002
    """
    ...
```

The same `Implements:` tag is also used for integration points:

```python
def checkout(cart: Cart) -> Receipt:
    """Submit payment and persist the order.

    Implements: REQ-003, INT-001
    """
    ...
```

### Test Tagging

Tests that verify a requirement include a `Verifies:` tag in their docstring:

```python
def test_authenticate_user_with_valid_credentials():
    """Verify successful authentication.

    Verifies: REQ-001
    """
    ...
```

Tests may also verify integration points:

```python
def test_checkout_charges_gateway_before_persisting_order() -> None:
    """Verify the checkout integration.

    Verifies: INT-001
    """
    ...
```

### Verification

SereneCode automatically uses a project-root `SPEC.md` during normal verification runs when one is present. Use `--spec SPEC.md` if the spec lives in a non-standard location.

Run spec traceability verification alongside structural checks:

```bash
serenecode check src/ --spec SPEC.md
```

This checks:
1. Every REQ in the spec has at least one `Implements:` tag in the code.
2. Every REQ in the spec has at least one `Verifies:` tag in the tests.
3. Every INT in the spec has at least one `Implements:` tag in the code.
4. Every INT in the spec has at least one `Verifies:` tag in the tests.
5. No orphan references (code/tests referencing non-existent REQs or INTs).
6. At L6, declared integrations are checked against recognized call/type and inheritance structure. This does not prove runtime behavior, ordering, or logical compatibility between contracts. Tags establish references, not test adequacy.

### Dead Code Review

SereneCode also reports likely dead code as part of baseline verification. These findings are advisory review items, not automatic deletion commands.

When dead code is reported:

- Ask the user whether the code should be removed.
- If it must remain, suppress it explicitly with `# allow-unused: reason`.
- Do not delete suspected dead code without user confirmation.

Do not consider implementation complete until traceability verification passes.
