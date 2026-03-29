"""Embedded template content for SERENECODE.md files.

This module stores template content as string constants so that
the init module can generate SERENECODE.md without file I/O.
"""

from __future__ import annotations

import icontract

from serenecode.contracts.predicates import is_valid_template_name

_DEFAULT_TEMPLATE = """\
# SERENECODE.md — Project Conventions

This file governs how all code in this project must be written. Any AI coding \
agent MUST read this file in its entirety before writing or modifying any code.

Verified with: `serenecode check src/ --level 4 --allow-code-execution`

Levels 3-6 import and execute project modules. Only use \
`--allow-code-execution` for trusted code.

---

## Complete Example

This shows every pattern the checker enforces. Follow this exactly:

```python
\"\"\"Module docstring describing purpose and architecture role.\"\"\"

import icontract
from dataclasses import dataclass


@icontract.invariant(lambda self: self.balance >= 0, "balance must be non-negative")
@dataclass(frozen=True)
class Account:
    \"\"\"An immutable account record.\"\"\"

    name: str
    balance: float


@icontract.require(lambda items: len(items) > 0, "items must not be empty")
@icontract.ensure(lambda items, result: min(items) <= result <= max(items), "result within range")
def compute_mean(items: list[float]) -> float:
    \"\"\"Compute the arithmetic mean.\"\"\"
    return sum(items) / len(items)
```

---

## Contract Standards

### Public Functions

Every public function MUST have `@icontract.require` (preconditions) and \
`@icontract.ensure` (postconditions) using icontract decorators.

- Every contract decorator MUST include a human-readable description string \
as the second argument: `@icontract.require(lambda x: x > 0, "x must be positive")`
- Functions with no meaningful parameters may omit `@icontract.require`.
- Contracts must be pure boolean expressions — no side effects.

### Private/Helper Functions

Private functions (prefixed with `_`) SHOULD have contracts when the function \
contains non-trivial logic.

### Class Invariants

Every class MUST have at least one `@icontract.invariant` defining its \
representation invariant. Invariants must constrain actual state — \
tautological invariants like `lambda self: True` provide no verification \
value and should not be used. If a class is truly stateless (e.g. a \
Protocol or a stateless adapter), omit the invariant and document why.

---

## Type Annotation Standards

- All function signatures MUST have complete type annotations on every \
parameter kind (including positional-only, keyword-only, variadic, and private \
helper parameters) and the return type.
- No use of `Any` in core modules. Use `Protocol`, `Union`, or generics.
- Generic types must be fully parameterized (`list[str]` not `list`).
- Use modern type syntax (Python 3.10+): `X | None` not `Optional[X]`.

---

## Documentation Standards

- Every module MUST have a module-level docstring.
- Every public function and class MUST have a docstring.

---

## Architecture Standards

### Hexagonal Architecture

```
src/yourproject/
├── core/        # Pure logic. No I/O. No os/pathlib/subprocess imports.
├── ports/       # Protocol interfaces only.
├── adapters/    # I/O implementations.
└── cli.py       # Thin entry point.
```

Core modules (`core/`, models, contracts) MUST NOT import I/O libraries \
(`os`, `pathlib`, `subprocess`, `requests`, `socket`, `shutil`, `tempfile`, `glob`). \
Inject dependencies through function parameters.

---

## Naming Conventions

- Modules: `snake_case.py`. Classes: `PascalCase`. Functions: `snake_case`.

---

## Exemptions

The following are exempt from full contract requirements:
- `cli.py`, `__init__.py` — Composition roots.
- `adapters/` — I/O boundary code.
- `ports/` — Protocol definitions.
- `templates/`, `tests/fixtures/`, `exceptions.py`

These MUST still have type annotations.
"""

_STRICT_TEMPLATE = """\
# SERENECODE.md — Strict Project Conventions

This file governs how all code in this project must be written. Any AI coding \
agent MUST read this file in its entirety before writing or modifying any code. \
**No exemptions.** Every function — public and private — must have contracts.

Verified with: `serenecode check src/ --level 6 --allow-code-execution`

Levels 3-6 import and execute project modules. Only use \
`--allow-code-execution` for trusted code.

---

## Complete Example

This shows every pattern the checker enforces. Follow this exactly:

```python
\"\"\"Module docstring describing purpose and architecture role.

This is a core module — no I/O operations are permitted.
\"\"\"

import icontract
from dataclasses import dataclass


@icontract.invariant(lambda self: self.balance >= 0, "balance must be non-negative")
@dataclass(frozen=True)
class Account:
    \"\"\"An immutable account record.\"\"\"

    name: str
    balance: float


@icontract.require(lambda items: len(items) > 0, "items must not be empty")
@icontract.ensure(lambda items, result: min(items) <= result <= max(items), "result within range")
def compute_mean(items: list[float]) -> float:
    \"\"\"Compute the arithmetic mean.\"\"\"
    total = 0.0
    # Loop invariant: total is the sum of items[0..i]
    for item in items:
        total += item
    return total / len(items)


def _validate_positive(value: float) -> bool:
    \"\"\"Check that a value is positive.\"\"\"
    return value > 0
```

---

## Contract Standards

### Public Functions

Every public function MUST have `@icontract.require` and `@icontract.ensure` \
with description strings: `@icontract.require(lambda x: x > 0, "x must be positive")`

Functions with no meaningful parameters may omit `@icontract.require`.

### Private Functions

Private functions (prefixed with `_`) MUST have contracts for all non-trivial \
logic. Simple one-liner helpers may omit contracts but MUST have type annotations.

### Class Invariants

Every class MUST have `@icontract.invariant`. Invariants must constrain \
actual state — tautological invariants like `lambda self: True` provide no \
verification value. If a class is truly stateless (Protocol, stateless adapter), \
omit the invariant and document why.

---

## Type Annotation Standards

- All function signatures MUST have complete type annotations on every \
parameter kind (including positional-only, keyword-only, variadic, and private \
helper parameters) and the return type.
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

Core modules (`core/`, models, contracts, checkers) MUST NOT import I/O \
libraries (`os`, `pathlib`, `subprocess`, `requests`, `socket`, `shutil`, \
`tempfile`, `glob`). Inject dependencies through function parameters.

---

## Error Handling Standards

Only domain-specific exceptions permitted in core modules. Never raise bare \
`Exception`, `ValueError`, `TypeError`, `RuntimeError`, `KeyError`, \
`IndexError`, or `AttributeError` in core.

---

## Loop and Recursion Standards

- Every loop MUST include a comment describing the loop invariant.
- Recursive functions MUST document the variant (decreasing measure).
- Prefer bounded iteration over unbounded `while`.

---

## Naming Conventions

- Modules: `snake_case.py`. Classes: `PascalCase`. Functions: `snake_case`.

---

## No Exemptions

Strict mode has NO exempt modules. Every module, including CLI and adapters, \
must follow all conventions above.
"""

_MINIMAL_TEMPLATE = """\
# SERENECODE.md — Minimal Project Conventions

This file defines minimal code conventions for this project. AI coding agents \
MUST read this before writing any code.

Verified with: `serenecode check src/`

---

## Contract Standards

Every public function MUST have `@icontract.require` (preconditions) and \
`@icontract.ensure` (postconditions). Type annotations on all parameters \
and return values.

```python
import icontract

@icontract.require(lambda items: len(items) > 0)
@icontract.ensure(lambda items, result: min(items) <= result <= max(items))
def compute_mean(items: list[float]) -> float:
    return sum(items) / len(items)
```

Functions with no meaningful parameters may omit `@icontract.require` but \
MUST still have `@icontract.ensure`.

---

## Exemptions

- `cli.py` — Thin CLI layer.
- `adapters/` — I/O boundary code.
- `templates/` — Static files.
- `tests/fixtures/` — Test fixtures.
"""

_TEMPLATES = {
    "default": _DEFAULT_TEMPLATE,
    "strict": _STRICT_TEMPLATE,
    "minimal": _MINIMAL_TEMPLATE,
}


@icontract.require(
    lambda template_name: is_valid_template_name(template_name),
    "template_name must be a valid template name",
)
@icontract.ensure(
    lambda result: isinstance(result, str) and len(result) > 0,
    "result must be a non-empty string",
)
def get_template(template_name: str) -> str:
    """Return the template content for a named template.

    Args:
        template_name: One of 'default', 'strict', or 'minimal'.

    Returns:
        The full SERENECODE.md template content.
    """
    return _TEMPLATES[template_name]
