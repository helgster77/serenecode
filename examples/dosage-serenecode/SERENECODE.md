# SERENECODE.md — Strict Project Conventions

This file governs how all code in this project must be written. Any AI coding agent MUST read this file in its entirety before writing or modifying any code.

---

## Contract Standards

### Public Functions

Every public function MUST have both preconditions and postconditions using icontract decorators with description strings.

### Private/Helper Functions

Private functions MUST have contracts for all non-trivial logic.

### Class Invariants

Every class MUST have at least one `@icontract.invariant`.

---

## Type Annotation Standards

- All function signatures MUST have complete type annotations.
- No use of `Any` anywhere.
- Generic types must be fully parameterized.

---

## Architecture Standards

Core modules MUST NOT import I/O libraries. Hexagonal architecture is mandatory.

---

## Error Handling Standards

Only domain-specific exceptions are permitted.

---

## Loop and Recursion Standards

- All loops MUST include invariant comments and assertions.
- All recursive functions MUST document the variant.
