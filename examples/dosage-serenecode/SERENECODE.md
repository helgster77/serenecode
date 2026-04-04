# SERENECODE.md — Strict Project Conventions

This file governs how all code in this project must be written. Any AI coding agent MUST read this file in its entirety before writing or modifying any code. **No exemptions.** Every function — public and private — must have contracts.

Verified with: `serenecode check src/ --level 6 --allow-code-execution`

Levels 3-6 import and execute project modules. Only use `--allow-code-execution` for trusted code.

---

## Complete Example

This shows every pattern the checker enforces. Follow this exactly:

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


@icontract.require(lambda items: len(items) > 0, "items must not be empty")
@icontract.ensure(lambda items, result: min(items) <= result <= max(items), "result within range")
def compute_mean(items: list[float]) -> float:
    """Compute the arithmetic mean."""
    total = 0.0
    # Loop invariant: total is the sum of items[0..i]
    for item in items:
        total += item
    return total / len(items)


def _validate_positive(value: float) -> bool:
    """Check that a value is positive."""
    return value > 0
```

---

## Contract Standards

### Public Functions

Every public function MUST have `@icontract.require` and `@icontract.ensure` with description strings: `@icontract.require(lambda x: x > 0, "x must be positive")`

Functions with no meaningful parameters may omit `@icontract.require`.

### Private Functions

Private functions (prefixed with `_`) MUST have contracts for all non-trivial logic. Simple one-liner helpers may omit contracts but MUST have type annotations.

### Class Invariants

Every class MUST have `@icontract.invariant`. Invariants must constrain actual state — tautological invariants like `lambda self: True` provide no verification value. If a class is truly stateless (Protocol, stateless adapter), omit the invariant and document why.

---

## Type Annotation Standards

- All function signatures MUST have complete type annotations on every parameter kind (including positional-only, keyword-only, variadic, and private helper parameters) and the return type.
- No use of `Any` anywhere — use `Protocol`, `Union`, or generics.
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

- **Every function** — public and private — must have corresponding tests.
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

@given(items=st.lists(st.floats(allow_nan=False, allow_infinity=False), min_size=1))
@settings(max_examples=200, deadline=None)
def test_compute_mean_satisfies_contracts(items: list[float]) -> None:
    result = compute_mean(items)
    assert isinstance(result, float)
```

### Workflow

When writing any new function:
1. Write the function with contracts and type annotations.
2. Write the implementation.
3. Write tests that verify the function's behavior.
4. Run `pytest -q` and fix any failures.
5. Run `serenecode check src/ --level 6 --allow-code-execution` and fix findings.

Do not commit code without passing tests and verification.

---

## No Exemptions

Strict mode has NO exempt modules. Every module, including CLI and adapters, must follow all conventions above. Every module must have test coverage.

---

## Spec Traceability

This project uses requirement identifiers (REQ-xxx) to maintain traceability between the specification, implementation, and tests. Every requirement in SPEC.md must be implemented and tested.

### Preparing a SereneCode-Ready Spec

If the project has an existing spec, PRD, design document, or requirements list that is not yet in SereneCode format, convert it into SPEC.md before writing any code. Follow these steps:

1. Read the source document in its entirety.
2. Identify every distinct, testable requirement. Each requirement must describe a single behavior that can be verified — not a vague goal, a heading, or an implementation detail.
3. Write SPEC.md with one heading per requirement in this format:

```markdown
### REQ-001: Short description of the requirement
Detailed explanation of what the system must do. Include acceptance criteria, input constraints, expected outputs, and edge cases.
```

4. Number requirements sequentially with no gaps (REQ-001, REQ-002, ...). Use 3-digit zero-padded numbers (or 4-digit for larger specs).
5. If the source document contains non-functional requirements, constraints, or background context that is not directly testable, include it in SPEC.md as regular prose outside of REQ headings. Only testable behaviors get REQ identifiers.
6. Validate the spec before proceeding:

```bash
serenecode spec SPEC.md
```

This checks that all REQ identifiers are well-formed, sequential with no gaps, free of duplicates, and have descriptions. Do not proceed to implementation planning until `serenecode spec` passes.

### Implementation Planning

After the spec is validated, create an implementation plan before writing code. The plan must map every REQ-xxx to:

- The specific function or class that will implement it.
- The module it belongs in (e.g. `src/core/dosage.py`).
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

### Test Tagging

Tests that verify a requirement include a `Verifies:` tag in their docstring:

```python
def test_authenticate_user_with_valid_credentials():
    """Verify successful authentication.

    Verifies: REQ-001
    """
    ...
```

### Verification

Run spec traceability verification alongside structural checks:

```bash
serenecode check src/ --spec SPEC.md
```

This checks:
1. Every REQ in the spec has at least one `Implements:` tag in the code.
2. Every REQ in the spec has at least one `Verifies:` tag in the tests.
3. No orphan references (code/tests referencing non-existent REQs).

Do not consider implementation complete until traceability verification passes.
