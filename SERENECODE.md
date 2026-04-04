# SERENECODE.md — Serenecode Project Conventions

This file governs how all code in this project must be written. Any AI coding agent (Claude Code, GitHub Copilot, etc.) MUST read this file in its entirety before writing or modifying any code. Human contributors must also follow these conventions.

Serenecode is a self-hosting formal verification framework for AI-generated Python code. The standards defined here are not generic aspirations: they exist to keep this repository aligned with its purpose as both a verification tool and a reference implementation of verification-first development.

---

## Project Purpose

This repository has two jobs at the same time:

1. It must be a useful verification product for other projects.
2. It must be a credible example of the development style it promotes.

That means contributors must optimize for more than “the tests pass”:

- Preserve trust in the verification pipeline itself. Changes to checking, reporting, module loading, source discovery, or configuration must keep the tool's own verification results meaningful.
- Preserve trust in path-scoped policy decisions. Core-module and exemption rules must match on real path segments, not accidental filename substrings.
- Preserve trust in interface-compliance reporting. Explicit `Protocol` inheritance and substitutability claims must be checked directly, not guessed only from naming overlap.
- Preserve trust in the project's claims. README examples, SERENECODE.md guidance, CLI help text, and example-project assertions must stay aligned with current behavior and runnable evidence.
- Preserve trust in verification-level reporting. If a verification stage produced no real evidence, the tool must not overstate that stage as achieved.
- Prefer designs that stay analyzable. Simpler, verification-friendly code is usually better than clever abstractions that weaken property testing, symbolic checking, or compositional analysis.

---

## Contract Standards

### Public Functions

Every public function MUST have postconditions using icontract decorators. Public functions with caller-supplied inputs MUST also have preconditions.

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
- Functions with no meaningful parameters may omit `@icontract.require` or use a trivial one.
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
- Invariants must constrain actual state — tautological invariants like `lambda self: True` provide no verification value and should not be used. If a class is truly stateless (e.g., a Protocol or a stateless adapter), omit the invariant and document why with a comment.

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

- All function signatures MUST have complete type annotations for all parameters and the return type, including positional-only parameters, keyword-only parameters, variadic parameters, and private helpers.
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
├── models.py    # Data models. Verified.
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
- If an adapter claims to implement a `Protocol`, it must remain substitutable for that port: explicit protocol inheritance counts, extra required parameters are forbidden, and return annotations must stay compatible with the port surface.
- Handle all I/O, subprocess calls, and external library integration.
- Are exempt from the same contract-completeness expectations as pure core modules, but they are still part of the shipped product and must not break repo-wide verification.
- MUST have type annotations and pass mypy.
- MUST have strong integration and end-to-end coverage around success paths and failure paths.
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
    """Base exception for all Serenecode errors."""
    pass

class StructuralViolationError(SerenecodeError):
    """Raised when code does not follow SERENECODE.md structural conventions."""
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

- All data models MUST be `@dataclass` classes, or equivalent explicitly typed classes when custom initialization, constructor validation, or immutability enforcement makes dataclasses impractical.
- Prefer immutable data models (`frozen=True`) or classes whose stored fields are themselves immutable values in core modules.
- Mutable dataclasses or mutable classes must have class invariants via `@icontract.invariant`.
- No raw dictionaries for structured data in core modules — define a typed data model instead.
- Enum types must be used for fixed sets of values (e.g., verification levels, status codes).

```python
from dataclasses import dataclass
from enum import Enum

class VerificationLevel(Enum):
    STRUCTURAL = 1
    TYPES = 2
    COVERAGE = 3
    PROPERTIES = 4
    SYMBOLIC = 5
    COMPOSITIONAL = 6

class CheckStatus(Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    EXEMPT = "exempt"

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
- Public-facing documentation must describe current behavior, not desired future behavior.
- Strong product claims in docs must be backed by runnable evidence in the repository.
- When commands, verification results, example numbers, or limitations change, update the relevant docs in the same change.
- If CLI report or JSON output fields change, update README examples and schema expectations in the same change.

```python
"""Structural checker for Serenecode conventions.

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

Every meaningful code change in this project MUST come with verification. Writing code first and promising to “tighten the checks later” is not acceptable for a self-hosting verification tool.

### Repository-Level Targets

- The checked-in project configuration should stay clean under `uv run serenecode check src --level 5 --allow-code-execution`.
- The shipped SereneCode dosage example should stay clean under `uv run serenecode check examples/dosage-serenecode/src --level 5 --allow-code-execution`.
- The repository should continue to pass `uv run mypy src`, and shipped examples should stay clean under their documented mypy invocation when applicable.
- The repository should continue to pass `uv run pytest -q`.
- The shipped SereneCode dosage example should continue to pass `uv run pytest -q` from `examples/dosage-serenecode/`.
- If a change affects verification semantics, discovery, loading, reporting, or example claims, verify those paths explicitly rather than assuming the full suite is enough.
- Levels 3-5 import and execute project modules. Use those levels only on trusted code, and pass `--allow-code-execution` or `allow_code_execution=True` explicitly.

### Verification Tiers by Module Type

**Pure core modules** (`core/`, `checker/`, `models.py`, `contracts/`, `config.py`, `reporter.py`) should remain friendly to Serenecode's full pipeline:
1. Structural check — required contracts present on public functions and classes.
2. `mypy --strict` — zero errors.
3. Test coverage analysis through Serenecode's coverage adapter.
4. Property-based verification through Serenecode's Hypothesis adapter, plus explicit property tests where they add signal.
5. Symbolic verification through CrossHair for symbolic-friendly contracted top-level functions within analysis bounds.
6. Example-based unit tests for edge cases, boundary conditions, regressions, and behavior that is important but awkward for automated strategy generation.

**Infrastructure modules** (`source_discovery.py`) use filesystem operations to locate and prepare source files. They are not pure core modules but should maintain contracts and full test coverage.

**Adapter and composition-root modules** (`adapters/`, `cli.py`, `__init__.py`, `init.py`) must pass:
1. `mypy --strict` — zero errors.
2. Integration or end-to-end tests that exercise real file system, subprocess, and CLI behavior.
3. Error-path coverage for invalid inputs, missing resources, backend failures, and stale-state regressions.
4. Repo-wide checks must remain green even if some adapter code is intentionally excluded from direct symbolic targeting.

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

- Property-based verification in this repo is primarily driven by Serenecode's own Hypothesis adapter, not `icontract-hypothesis`.
- If a new domain type or function signature is hard for the adapter to generate, extend the strategy derivation in the adapter or add a focused explicit Hypothesis test.
- Use explicit Hypothesis tests for reusable predicates, strategy builders, and bug-prone helpers where they give clearer regression coverage than only relying on the pipeline.
- When Hypothesis finds a failing example, preserve it as a regression test or a dedicated strategy/example in the relevant test suite.
- Do not claim Level 4 was achieved for a run that produced no property-testing findings at all.

```python
from hypothesis import example, given, settings
from hypothesis import strategies as st

from serenecode.core.some_module import compute_mean

@given(items=st.lists(st.floats(allow_nan=False, allow_infinity=False), min_size=1))
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

- Levels 3-5 load project modules into a real Python interpreter. Treat deep verification as code execution, not as a passive static analysis step.
- Design top-level contracted functions so they remain as symbolic-friendly as practical: pure inputs, explicit contracts, and minimal hidden state.
- Standalone files and non-package modules must remain verifiable. If a backend needs file-and-line targeting instead of a dotted import path, use it.
- Keep the default CrossHair budgets in mind: 30 seconds per condition, 10 seconds per path, 300 seconds per module.
- The module timeout is a true whole-module budget, including the CLI fallback path.
- Do not claim Level 5 was achieved for a scoped run that produced no symbolic findings at all.
- When CrossHair finds a counterexample, the fix loop is:
  1. Examine the counterexample.
  2. Determine if the contract is wrong (fix the contract) or the implementation is wrong (fix the code).
  3. Re-run CrossHair until it passes or reports "verified."
- If a function or module is a poor fit for direct symbolic verification, keep the exclusion narrow, document the reason in code or tests, and add compensating regression/property coverage.

### Test-Writing Workflow

When writing any new function or class, the AI agent MUST follow this sequence:

1. Write the function signature with type annotations.
2. Write the required icontract postconditions and any necessary preconditions.
3. Write the implementation.
4. Add the most appropriate verification coverage:
   - explicit unit/integration/e2e tests,
   - focused Hypothesis tests,
   - or adapter strategy support if the pipeline needs new input generation help.
5. Write edge-case regression tests for known boundary conditions or discovered counterexamples.
6. Run `uv run mypy src` and fix any type errors.
7. Run `uv run pytest -q` and fix any test failures.
8. Run Serenecode verification at the deepest level justified by the change, and fix any findings or skips you introduced.

Steps 1-3 may be done together, but steps 4-8 MUST NOT be skipped or deferred.

### Regression Testing

- Every bug fix MUST include a test that reproduces the bug before the fix and passes after.
- Every counterexample discovered by CrossHair or Hypothesis MUST be preserved as an explicit test case.
- If a bug was caused by strategy generation, module loading, source discovery, or enum/module identity issues, add a regression test at that integration boundary rather than only at the surface symptom.
- The relevant verification commands for the changed area MUST be run before considering the task complete.

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
- All code must pass `uv run serenecode check src --structural` before committing.
- All code must pass `uv run mypy src` before committing.
- Changes that affect the verification engine, source discovery, configuration, docs claims, or shipped examples should keep `uv run serenecode check src --level 6 --allow-code-execution` green and preserve the shipped example's strict Level 6 check.

---

## Exemptions

The following modules are exempt from full contract requirements due to their nature:
- `cli.py` — Thin CLI layer, tested via integration tests.
- `__init__.py` — Composition roots, tested via integration tests.
- `init.py` — Composition root for project initialization, tested via e2e tests.
- `adapters/` — I/O boundary code, tested via integration tests.
- `templates/` — Static markdown files, not code.
- `tests/fixtures/` — Intentionally broken or incomplete code used for testing the checker.
- `ports/` — Protocol definitions with no implementations to contract.
- `exceptions.py` — Exception class hierarchy, no meaningful invariants.

These modules MUST still have type annotations and pass mypy.
