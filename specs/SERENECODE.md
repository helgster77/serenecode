# SERENECODE.md — Serenecode Project Conventions

This file governs how all code in this project must be written. Any AI coding agent (Claude Code, GitHub Copilot, etc.) MUST read this file in its entirety before writing or modifying any code. Human contributors must also follow these conventions.

Serencode is a formal verification framework for AI-generated Python code. It is built using its own conventions — the standards defined here apply to this project's own codebase.

---

## Contract Standards

### Public Functions

Every public function MUST have both preconditions and postconditions using icontract decorators.

```python
import icontract

@icontract.require(lambda items: len(items) > 0, "items must not be empty")
@icontract.require(lambda items: all(isinstance(i, (int, float)) for i in items), "all items must be numeric")
@icontract.ensure(lambda result: result >= 0, "result must be non-negative")
@icontract.ensure(lambda items, result: min(items) <= result <= max(items), "result must be within input range")
def compute_mean(items: list[float]) -> float:
    """Compute the arithmetic mean of a non-empty list of numbers."""
    return sum(items) / len(items)
```

Rules:
- Every `@icontract.require` and `@icontract.ensure` MUST include a human-readable description string as the second argument.
- Preconditions define what the function expects from callers.
- Postconditions define what the function guarantees to callers.
- Postconditions may reference both the input parameters and `result` (the return value).
- Postconditions may reference `OLD` for capturing pre-call state when needed (e.g., `@icontract.ensure(lambda OLD, result: len(result) == OLD.len_items + 1)`).
- Contracts must be pure boolean expressions — no side effects, no I/O, no exceptions.

### Private/Helper Functions

Private functions (prefixed with `_`) SHOULD have contracts when the function contains non-trivial logic. Simple delegation or one-liner helpers may omit contracts but MUST have type annotations.

### Class Invariants

Every class MUST have at least one `@icontract.invariant` defining its representation invariant — the property that must hold after construction and after every public method call.

```python
import icontract

@icontract.invariant(lambda self: self.balance >= 0, "balance must never be negative")
@icontract.invariant(lambda self: len(self.transaction_log) >= 0, "transaction log must exist")
class Account:
    def __init__(self, initial_balance: float) -> None:
        self.balance = initial_balance
        self.transaction_log: list[str] = []
```

Rules:
- Invariants define what is always true about an instance of the class.
- If a class has no meaningful invariant, document why with a comment and use a trivial invariant (e.g., `lambda self: True`).

### Contract Helpers

When a contract expression is reused across multiple functions, extract it into a named predicate in the `contracts/` module.

```python
# In src/serenecode/contracts/predicates.py
def is_non_empty_string(value: str) -> bool:
    """Check that a string is non-empty and not just whitespace."""
    return isinstance(value, str) and len(value.strip()) > 0
```

These predicates must themselves be pure functions with no side effects.

---

## Type Annotation Standards

- All function signatures MUST have complete type annotations for all parameters and the return type.
- No use of `Any` in core modules (`src/serenecode/core/`, `src/serenecode/checker/`, `src/serenecode/models.py`). Use `Union`, `Optional`, generics, or `Protocol` instead.
- `Any` is permitted only in adapter modules or where interfacing with untyped external libraries.
- Use `typing.Protocol` for interface definitions (ports).
- Generic types must be fully parameterized (e.g., `list[str]` not `list`).
- Use modern type syntax (Python 3.10+): `X | None` instead of `Optional[X]`, `list[X]` instead of `List[X]`.
- The project must pass `mypy --strict` with zero errors.

---

## Architecture Standards

### Hexagonal Architecture

The project follows hexagonal architecture (ports and adapters). This separates pure, verifiable business logic from I/O and external dependencies.

```
src/serenecode/
├── core/        # Pure domain logic. No I/O. Fully verified.
├── ports/       # Protocol definitions (interfaces). Type-verified.
├── adapters/    # I/O implementations. Integration-tested.
├── checker/     # Verification engines. Verified where possible.
├── contracts/   # Shared contract predicates.
├── models.py    # Data models (dataclasses). Verified.
├── cli.py       # CLI entry point. Thin adapter layer.
└── __init__.py  # Public API surface.
```

### Core Modules (`core/`, `checker/`, `models.py`, `contracts/`)

- MUST NOT import any I/O libraries (`os`, `pathlib` for file I/O, `subprocess`, `requests`, `socket`, etc.).
- MUST NOT access global mutable state.
- MUST NOT perform side effects (printing, logging to files, network calls).
- All dependencies must be injected through function parameters or constructor arguments.
- Exception: `ast` module is permitted in `checker/` since it operates on in-memory strings, not files.

### Port Modules (`ports/`)

- Define interfaces using `typing.Protocol`.
- No implementations — only abstract contracts.
- Each port must document its contract (what implementations must guarantee).

```python
from typing import Protocol

class FileReader(Protocol):
    """Port for reading file contents. Implementations must handle encoding."""

    def read_file(self, path: str) -> str:
        """Read a file and return its contents as a string.

        Precondition: path is a valid file path.
        Postcondition: returns the full file contents as a UTF-8 string.
        """
        ...
```

### Adapter Modules (`adapters/`, `cli.py`)

- Implement the Protocols defined in `ports/`.
- Handle all I/O, subprocess calls, and external library integration.
- Are NOT subject to formal verification (Levels 3-5) but MUST have integration tests.
- MUST have type annotations and pass mypy.
- Keep adapters thin — minimal logic, mostly delegation to core.

### Dependency Injection

- Core functions and classes receive their dependencies as parameters.
- No module-level I/O. No global singletons.
- The CLI layer (`cli.py`) and `__init__.py` are the composition roots where dependencies are wired together.

---

## Error Handling Standards

- Core domain functions raise domain-specific exceptions defined in a `core/exceptions.py` module.
- Never raise bare `Exception`, `ValueError`, or `TypeError` in core modules. Define specific exception classes.
- Error conditions SHOULD be captured in postconditions where possible.
- Adapter layers catch external exceptions and translate them to domain exceptions.
- All custom exceptions must inherit from a base `SerenecodeError` class.

```python
class SerenecodeError(Exception):
    """Base exception for all Serencode errors."""
    pass

class StructuralViolationError(SerenecodeError):
    """Raised when code does not follow SERENCODE.md structural conventions."""
    pass

class VerificationError(SerenecodeError):
    """Raised when formal verification finds a counterexample."""
    pass
```

---

## Loop and Recursion Standards

- Loops MUST include a comment describing the loop invariant — what property holds at the start of each iteration.
- Loops SHOULD include an assertion of the invariant where practical and where it does not impact performance.
- Recursive functions MUST include a comment documenting the variant (decreasing measure) that guarantees termination.
- Prefer bounded iteration (`for x in collection`, `for i in range(n)`) over unbounded `while` loops.
- If a `while` loop is necessary, document the termination argument.

```python
def find_index(items: list[int], target: int) -> int:
    """Find the index of target in a sorted list using binary search."""
    low, high = 0, len(items) - 1
    # Loop invariant: if target is in items, it is in items[low..high]
    # Variant: high - low decreases each iteration
    while low <= high:
        mid = (low + high) // 2
        if items[mid] == target:
            return mid
        elif items[mid] < target:
            low = mid + 1
        else:
            high = mid - 1
    return -1
```

---

## Data Model Standards

- All data models MUST be `@dataclass` classes (or `@dataclass` with `frozen=True` for immutable data).
- Prefer immutable data models (`frozen=True`) in core modules.
- Mutable dataclasses must have class invariants via `@icontract.invariant`.
- No raw dictionaries for structured data in core modules — define a dataclass instead.
- Enum types must be used for fixed sets of values (e.g., verification levels, status codes).

```python
from dataclasses import dataclass
from enum import Enum

class VerificationLevel(Enum):
    STRUCTURAL = 1
    TYPES = 2
    PROPERTIES = 3
    SYMBOLIC = 4
    COMPOSITIONAL = 5

class CheckStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"

@dataclass(frozen=True)
class Detail:
    """A single verification finding."""
    level: VerificationLevel
    tool: str
    finding_type: str
    message: str
    counterexample: dict[str, object] | None
    suggestion: str | None
```

---

## Naming Conventions

- Modules: `snake_case.py`
- Classes: `PascalCase`
- Functions and methods: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private members: prefix with single underscore `_`
- No abbreviations in public APIs. Write `verification_level` not `ver_lvl`.
- Contract predicate functions: prefix with `is_` or `has_` (e.g., `is_non_empty_string`, `has_valid_contracts`).

---

## Documentation Standards

- Every public function, class, and module MUST have a docstring.
- Docstrings follow Google style.
- The docstring describes *what* the function does, not *how* (the contracts describe the formal behavior).
- Module-level docstrings describe the module's role in the architecture.

```python
"""Structural checker for Serencode conventions.

This module implements Level 1 verification: AST-based analysis that validates
Python source code follows the conventions defined in SERENECODE.md. It checks
for the presence of contracts, type annotations, and architectural compliance.

This is a core module — no I/O operations are permitted. Source code is received
as strings, not read from files.
"""
```

---

## Testing and Verification Standards

### Mandatory Verification for All Code

Every piece of code in this project MUST be tested and verified before it is considered complete. Writing code without corresponding tests and verification is never acceptable. "I'll add tests later" is not permitted — tests and verification are written alongside or before the implementation.

### Test Coverage Requirements

- Core modules (`core/`, `checker/`, `models.py`, `contracts/`): 100% line and branch coverage is the target. Any uncovered line must have a documented justification.
- Adapter modules (`adapters/`, `cli.py`): 90%+ line coverage through integration tests.
- Contract predicates (`contracts/`): every predicate must have both positive and negative test cases demonstrating it accepts valid inputs and rejects invalid inputs.

### Verification Tiers by Module Type

**Core modules** must pass ALL of the following:
1. Structural check — contracts present on all public functions and classes.
2. `mypy --strict` — zero errors.
3. Property-based tests via Hypothesis — auto-generated from icontract preconditions using `icontract-hypothesis`. Minimum 100 examples per function (configurable via Hypothesis settings).
4. Symbolic verification via CrossHair — prove postconditions hold for all inputs satisfying preconditions within analysis bounds.
5. Example-based unit tests for edge cases, boundary conditions, and known tricky inputs that complement the auto-generated tests.

**Adapter modules** must pass:
1. `mypy --strict` — zero errors.
2. Integration tests using `pytest` that exercise real I/O (file system, subprocess calls, etc.).
3. Error path testing — every adapter must be tested with invalid inputs, missing resources, permission errors, and other failure scenarios.

**CLI module** must pass:
1. `mypy --strict` — zero errors.
2. End-to-end tests that invoke the CLI as a subprocess and verify exit codes, stdout, and stderr output.
3. Tests for every command (`init`, `check`, `status`, `report`) with both passing and failing inputs.

### Test Structure

```
tests/
├── unit/                     # Unit tests for core modules
│   ├── test_models.py
│   ├── test_config.py
│   ├── checker/
│   │   ├── test_structural.py
│   │   ├── test_types.py
│   │   ├── test_properties.py
│   │   ├── test_symbolic.py
│   │   └── test_compositional.py
│   └── contracts/
│       └── test_predicates.py
├── integration/              # Integration tests for adapters
│   ├── test_file_adapter.py
│   ├── test_mypy_adapter.py
│   ├── test_crosshair_adapter.py
│   └── test_hypothesis_adapter.py
├── e2e/                      # End-to-end CLI tests
│   ├── test_init_command.py
│   ├── test_check_command.py
│   ├── test_status_command.py
│   └── test_report_command.py
├── fixtures/                 # Sample code for testing the checker
│   ├── valid/                # Code that passes all checks
│   │   ├── simple_function.py
│   │   ├── class_with_invariant.py
│   │   └── full_module.py
│   ├── invalid/              # Code that should fail specific checks
│   │   ├── missing_contracts.py
│   │   ├── missing_types.py
│   │   ├── io_in_core.py
│   │   └── broken_postcondition.py
│   └── edge_cases/           # Tricky or unusual code patterns
│       ├── nested_classes.py
│       ├── decorators.py
│       └── generics.py
└── conftest.py               # Shared fixtures and Hypothesis profiles
```

### Property-Based Testing Rules

- Every core function with icontract contracts MUST have a corresponding Hypothesis test that uses `icontract-hypothesis` to auto-derive input strategies from preconditions.
- Custom Hypothesis strategies SHOULD be defined for domain-specific types (e.g., `CheckResult`, `FunctionResult`) in `tests/conftest.py`.
- Hypothesis settings: use `@settings(max_examples=200, deadline=None)` for core verification functions to ensure thorough exploration.
- When Hypothesis finds a failing example, it MUST be added as an explicit regression test (using `@example` decorator) to prevent future regressions.

```python
from hypothesis import given, settings, example
from icontract_hypothesis import infer_strategy

from serenecode.core.some_module import compute_mean

@given(items=infer_strategy(compute_mean)["items"])
@settings(max_examples=200, deadline=None)
@example(items=[0.0])          # edge case: single zero
@example(items=[1e308, 1e308]) # edge case: near float overflow
def test_compute_mean_contract(items: list[float]) -> None:
    """Verify compute_mean satisfies its contracts for all valid inputs."""
    result = compute_mean(items)
    # Postconditions are checked automatically by icontract at runtime.
    # This test verifies they hold across a wide range of auto-generated inputs.
    assert isinstance(result, float)
```

### Symbolic Verification Rules

- Every core function MUST be verified with CrossHair after implementation.
- CrossHair timeout: 60 seconds per function. If verification does not complete within this time, the function must be simplified or its contracts tightened until verification succeeds.
- When CrossHair finds a counterexample, the fix loop is:
  1. Examine the counterexample.
  2. Determine if the contract is wrong (fix the contract) or the implementation is wrong (fix the code).
  3. Re-run CrossHair until it passes or reports "verified."
- Functions that cannot be symbolically verified (e.g., due to CrossHair limitations with certain Python features) must be documented with the reason and must have extra-thorough Hypothesis testing (minimum 500 examples).

### Test-Writing Workflow

When writing any new function or class, the AI agent MUST follow this sequence:

1. Write the function signature with type annotations.
2. Write the icontract preconditions and postconditions.
3. Write the implementation.
4. Write Hypothesis property-based tests using `icontract-hypothesis`.
5. Write edge-case example tests for known boundary conditions.
6. Run `mypy --strict` and fix any type errors.
7. Run `pytest` and fix any test failures.
8. Run CrossHair verification and fix any counterexamples.

Steps 1-3 may be done together, but steps 4-8 MUST NOT be skipped or deferred.

### Regression Testing

- Every bug fix MUST include a test that reproduces the bug before the fix and passes after.
- Every counterexample discovered by CrossHair or Hypothesis MUST be preserved as an explicit test case.
- The test suite MUST be run in full before any commit (once CI is set up).

### Test Quality Rules

- Tests must be deterministic. No reliance on wall-clock time, random seeds, or external services (except in integration tests with appropriate mocking).
- Tests must be independent. No test may depend on another test's side effects or execution order.
- Tests must be fast. Unit tests should complete in under 1 second each. Integration and e2e tests may be slower but should be marked with `@pytest.mark.slow`.
- Test names must describe the behavior being tested, not the implementation: `test_check_returns_failure_when_contracts_missing` not `test_check_1`.

---

## Import Standards

- Standard library imports first, then third-party, then local — separated by blank lines.
- No wildcard imports (`from x import *`).
- No circular imports between core modules.
- Adapters may import from core and ports. Core must never import from adapters.

```
# Dependency direction (arrows mean "may import from"):
# cli.py / __init__.py → adapters → ports ← core
#                                          ← checker
#                                          ← models
#                                          ← contracts
```

---

## Version Control Standards

- Commits must be atomic — one logical change per commit.
- Commit messages follow conventional commits format: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`.
- All code must pass `serenecode check --structural` before committing (once the tool is available).
- All code must pass `mypy --strict` before committing.

---

## Exemptions

The following modules are exempt from full contract requirements due to their nature:
- `cli.py` — Thin CLI layer, tested via integration tests.
- `adapters/` — I/O boundary code, tested via integration tests.
- `templates/` — Static markdown files, not code.
- `tests/fixtures/` — Intentionally broken or incomplete code used for testing the checker.

These modules MUST still have type annotations and pass mypy.
