# SERENECODE.md — Project Conventions

This file governs how all code in this project must be written. Any AI coding agent MUST read this file in its entirety before writing or modifying any code.

---

## Contract Standards

### Public Functions

Every public function MUST have both preconditions and postconditions using icontract decorators with description strings.

### Private/Helper Functions

Private helper functions SHOULD have contracts for non-trivial logic.

### Class Invariants

Every public domain class MUST have at least one `@icontract.invariant`.
Private helper mixins may omit invariants when icontract class wrapping is
incompatible with subclass invariants.

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

## Exemptions

- No path-level exemptions. Helper-class exceptions are documented above.
