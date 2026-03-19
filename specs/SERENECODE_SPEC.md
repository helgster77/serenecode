# Serenecode — Project Specification

**Version:** 0.1.0 (Draft)
**Date:** March 19, 2026
**License:** MIT

---

## 1. Vision

Serenecode is a Python framework that enables developers to build mission-critical systems using AI-generated code with formal correctness guarantees. It bridges the gap between the speed of AI code generation and the rigor of formal verification by controlling *how* AI writes code and then *proving* that code is correct.

The core philosophy: if you control the development process, verification becomes tractable. Rather than verifying arbitrary code after the fact, Serenecode instructs the AI to produce verification-ready code from the start, then runs automated proofs against it.

**Dogfooding principle:** Serenecode is built using its own conventions. The SERENECODE.md file is created *before* any code is written, and all code in the project — including the tool itself — must follow its standards. This means the project's own SERENECODE.md serves as both the template for users and the living specification that governs Serenecode's development. Any AI agent writing code for Serenecode must read and follow SERENECODE.md first.

---

## 2. Core Concepts

### 2.1 The Three Pillars

Serencode is built on three components:

1. **SERENECODE.md** — A convention file that lives in the project root. It dictates how the AI must write code: what annotations to include, what architectural patterns to follow, what contract format to use. This is the source of truth for code generation standards.

2. **The Checker** — A fast structural linter that validates AI-generated code actually follows the conventions defined in SERENECODE.md. It runs in seconds and catches issues like missing contracts, absent invariants, or architectural violations before any heavy verification begins.

3. **The Verifier** — The formal verification engine that takes well-formed, annotated code and proves it correct using symbolic execution (CrossHair), SMT solving (Z3), and property-based testing (Hypothesis).

### 2.2 The Verification Pipeline

Code flows through escalating levels of verification:

- **Level 1 — Structural Check:** Does the code follow SERENECODE.md conventions? Are contracts present on all public functions? Are class invariants defined? Are I/O boundaries properly separated? (Fast — seconds)
- **Level 2 — Type & Static Analysis:** Does the code pass mypy in strict mode? Are there obvious issues caught by static analysis? (Fast — seconds)
- **Level 3 — Property-Based Testing:** Hypothesis generates randomized test cases against the contracts. Finds counterexamples quickly through fuzzing. (Medium — seconds to minutes)
- **Level 4 — Symbolic Verification:** CrossHair performs symbolic execution backed by Z3 to prove contracts hold for all inputs within bounds, or finds counterexamples. (Slower — minutes)
- **Level 5 — Compositional Verification:** Module-level verification that checks component interactions, interface contracts, and system-level properties. (Slowest — minutes to hours, depending on system size)

Each function/class in the verified codebase carries a **verification level** indicating the highest level it has passed.

---

## 3. SERENECODE.md — The Convention File

### 3.1 Purpose

SERENECODE.md is a markdown file in the project root that instructs AI coding agents on how to write code for the project. It is both human-readable documentation and machine-readable instruction.

### 3.2 Default Template

When a user runs `serenecode init`, a default SERENECODE.md is generated. It covers:

**Contract Standards:**
- Every public function MUST have `@icontract.require` (preconditions) and `@icontract.ensure` (postconditions) decorators.
- Every class MUST have `@icontract.invariant` decorators defining its representation invariant.
- Private/helper functions SHOULD have contracts where non-trivial.
- All contracts must be expressed as pure Python boolean expressions — no side effects.

**Architectural Standards:**
- The project follows hexagonal architecture (ports and adapters).
- All I/O operations (file system, network, database, hardware) MUST go through Protocol-defined interfaces (ports).
- The core domain logic MUST be pure — no side effects, no I/O, no global state.
- Adapter implementations of ports are NOT subject to formal verification but MUST have integration tests.
- Dependencies are injected, never imported directly in core modules.

**Type Annotation Standards:**
- All function signatures MUST have complete type annotations.
- Use `typing.Protocol` for interface definitions.
- No use of `Any` in core modules.
- Generic types must be fully parameterized.

**Loop and Recursion Standards:**
- Loops SHOULD include a comment or assertion describing the loop invariant.
- Recursive functions MUST include a variant (decreasing measure) documented in a comment or enforced via assertion.
- Prefer bounded iteration over unbounded where possible.

**Error Handling Standards:**
- Core domain functions raise domain-specific exceptions, never bare `Exception`.
- Error conditions should be captured in postconditions where possible.
- Adapter layers handle translation between external errors and domain errors.

**Module Structure Standards:**
- `core/` — Pure domain logic. Fully verified.
- `ports/` — Protocol definitions (interfaces). Verified for type correctness.
- `adapters/` — I/O implementations. Integration-tested.
- `contracts/` — Shared contract helpers and custom predicates.

### 3.3 Customization

Users can modify SERENECODE.md to:
- Add domain-specific conventions (e.g., "all monetary values must use Decimal, never float").
- Relax standards for specific modules (e.g., "scripts/ directory is exempt from contract requirements").
- Add project-specific safety properties (e.g., "no function may allocate more than 1MB of memory").
- Specify additional verification targets or custom Hypothesis strategies.

### 3.4 Integration with CLAUDE.md

During `serenecode init`, the tool:

1. Checks if a `CLAUDE.md` file exists in the project root.
2. If it exists, prompts the user for confirmation, then appends a directive:
   ```
   ## Serenecode
   All code in this project MUST follow the standards defined in SERENECODE.md. Read SERENECODE.md before writing or modifying any code. Every public function must have icontract preconditions and postconditions. Every class must have invariants. Follow the architectural patterns specified in SERENECODE.md.
   ```
3. If no `CLAUDE.md` exists, creates one containing the above directive.
4. The user retains full control of their CLAUDE.md — Serenecode only adds its section once and does not modify it again on subsequent runs.

---

## 4. CLI Interface

### 4.1 Commands

```
serenecode init [--template <template>]
```
Initialize a new Serenecode project. Generates SERENECODE.md with the selected template, sets up the project structure, integrates with CLAUDE.md (with user confirmation), and installs verification dependencies.

Templates:
- `default` — Standard conventions as described in Section 3.2.
- `strict` — All standards are mandatory with no exemptions.
- `minimal` — Contracts on public functions only, relaxed architectural requirements.
- Custom templates can be defined as SERENECODE.md files and referenced by path.

```
serenecode check [<path>] [--level <1-5>] [--format <human|json>]
```
Run verification up to the specified level. Defaults to the full pipeline (level 5). The `path` argument targets specific files or directories. The `--format json` flag outputs structured results for programmatic consumption (used by AI agents).

```
serenecode check --structural [<path>]
```
Run only the structural checker (Level 1). Fast feedback on whether code follows SERENECODE.md conventions.

```
serenecode check --verify [<path>]
```
Run Levels 3-5 (property-based testing + symbolic verification + compositional checks). Assumes structural check has already passed.

```
serenecode status [<path>]
```
Show the current verification status of the codebase. Displays each module/function with its highest achieved verification level.

```
serenecode report [--format <human|json|html>]
```
Generate a verification report for the entire project. Suitable for compliance documentation, code review, or CI/CD artifacts.

### 4.2 Exit Codes

- `0` — All checks passed at the requested level.
- `1` — Structural violations found (Level 1 failures).
- `2` — Type/static analysis failures (Level 2).
- `3` — Property-based testing found counterexamples (Level 3).
- `4` — Symbolic verification found counterexamples or could not prove correctness (Level 4).
- `5` — Compositional verification failures (Level 5).
- `10` — Internal error in Serenecode itself.

### 4.3 Output Format (JSON)

When `--format json` is used, the output is structured for machine consumption:

```json
{
  "version": "0.1.0",
  "timestamp": "2026-03-19T14:30:00Z",
  "summary": {
    "total_functions": 42,
    "passed": 38,
    "failed": 3,
    "skipped": 1
  },
  "results": [
    {
      "function": "core.pricing.calculate_total",
      "file": "src/core/pricing.py",
      "line": 15,
      "level_requested": 4,
      "level_achieved": 4,
      "status": "passed",
      "details": []
    },
    {
      "function": "core.inventory.reserve_stock",
      "file": "src/core/inventory.py",
      "line": 42,
      "level_requested": 4,
      "level_achieved": 3,
      "status": "failed",
      "details": [
        {
          "level": 4,
          "tool": "crosshair",
          "type": "counterexample",
          "message": "Postcondition violated: result >= 0",
          "counterexample": {
            "quantity": -1,
            "available": 5
          },
          "suggestion": "Add precondition: quantity >= 0"
        }
      ]
    }
  ]
}
```

This format is designed so that an AI coding agent (e.g., Claude Code) can parse the output, understand exactly what failed and why, and generate a fix.

---

## 5. Python Library API

### 5.1 Purpose

The library API enables programmatic access to all Serenecode functionality. This is how AI coding agents like Claude Code interact with the tool — they import the library, run verification, inspect structured results, and act on failures automatically.

### 5.2 Core API

```python
import serenecode

# Initialize a project programmatically
serenecode.init(path=".", template="default")

# Run the full verification pipeline
result = serenecode.check(path="src/", level=5)

# Inspect results
result.passed          # bool
result.summary         # dict with counts
result.results         # list of FunctionResult objects
result.to_json()       # JSON string (same format as CLI)
result.failures        # list of only failed FunctionResults

# Run individual verification levels
structural = serenecode.check_structural(path="src/")
types = serenecode.check_types(path="src/")
properties = serenecode.check_properties(path="src/")
symbolic = serenecode.check_symbolic(path="src/")
compositional = serenecode.check_compositional(path="src/")

# Get verification status
status = serenecode.status(path="src/")
for func in status.functions:
    print(f"{func.name}: Level {func.verification_level}")
```

### 5.3 Result Objects

```python
@dataclass
class CheckResult:
    passed: bool
    summary: dict
    results: list[FunctionResult]
    failures: list[FunctionResult]
    level_requested: int
    level_achieved: int
    duration_seconds: float

    def to_json(self) -> str: ...
    def to_dict(self) -> dict: ...

@dataclass
class FunctionResult:
    function: str          # Fully qualified function name
    file: str              # File path
    line: int              # Line number
    level_requested: int
    level_achieved: int
    status: str            # "passed", "failed", "skipped"
    details: list[Detail]  # Failure details with counterexamples

@dataclass
class Detail:
    level: int
    tool: str              # "structural", "mypy", "hypothesis", "crosshair"
    type: str              # "violation", "counterexample", "timeout", "error"
    message: str           # Human-readable description
    counterexample: dict | None  # Variable assignments that trigger the failure
    suggestion: str | None       # Suggested fix
```

### 5.4 Claude Code Integration Pattern

When running inside Claude Code or any AI coding agent, the expected workflow is:

```python
import serenecode

# Agent generates code following SERENECODE.md conventions
# ...

# Agent runs verification
result = serenecode.check(path="src/core/pricing.py", level=4)

if not result.passed:
    for failure in result.failures:
        # Agent reads the counterexample and suggestion
        # Agent modifies the code to fix the issue
        # Agent re-runs verification
        ...
```

The key design principle: **every failure returns enough context for an AI agent to fix the issue without human intervention.** Counterexamples show exactly what input breaks the code. Suggestions propose a fix direction. The agent can iterate until verification passes.

---

## 6. Verification Backends

### 6.1 Level 1 — Structural Checker

**Implementation:** Custom AST-based analyzer.

Walks the Python AST and checks:
- Presence of `@icontract.require` and `@icontract.ensure` on public functions.
- Presence of `@icontract.invariant` on classes.
- Type annotations on all function signatures.
- Architectural compliance: no I/O imports in `core/` modules, Protocol usage in `ports/`.
- Adherence to any custom rules defined in SERENECODE.md.

**Dependencies:** Python standard library (`ast` module) only.

### 6.2 Level 2 — Type & Static Analysis

**Implementation:** Wraps `mypy` in strict mode.

Runs mypy with:
- `--strict` flag enabled.
- Custom plugins for icontract-aware type narrowing if needed.
- Additional static analysis rules as configured.

**Dependencies:** `mypy`

### 6.3 Level 3 — Property-Based Testing

**Implementation:** Wraps `hypothesis` with `icontract-hypothesis`.

The `icontract-hypothesis` library automatically derives Hypothesis strategies from icontract preconditions. This means tests are generated directly from the contracts — no manual strategy writing needed.

For each function with contracts:
1. Derive input strategies from `@icontract.require` preconditions.
2. Generate randomized test cases.
3. Check that all `@icontract.ensure` postconditions hold.
4. Report counterexamples on failure.

**Dependencies:** `hypothesis`, `icontract-hypothesis`

### 6.4 Level 4 — Symbolic Verification

**Implementation:** Wraps `crosshair`.

CrossHair performs symbolic execution using Z3 as the SMT backend. It explores execution paths symbolically rather than with concrete inputs, providing stronger guarantees than testing.

For each function:
1. Symbolically execute with unconstrained inputs satisfying preconditions.
2. Check that all postconditions hold on all feasible execution paths.
3. Return counterexamples if a violation is found.
4. Report "verified" if no violation exists within the analysis bounds.

Configuration options:
- `per_condition_timeout` — Maximum time to spend verifying each condition (default: 30s).
- `per_path_timeout` — Maximum time per execution path (default: 10s).

**Dependencies:** `crosshair-tool`

### 6.5 Level 5 — Compositional Verification

**Implementation:** Custom analysis combining CrossHair with module-level reasoning.

This level verifies that components work correctly together:
1. **Interface Compliance:** Implementations satisfy their Protocol contracts.
2. **Assume-Guarantee Reasoning:** Module A's postconditions satisfy Module B's preconditions where A's output feeds B's input.
3. **System Invariants:** Global properties that must hold across module boundaries (defined in SERENECODE.md).
4. **Data Flow Verification:** Values flowing through the system maintain their contracts at each boundary.

This is the most experimental level and will evolve as the project matures.

**Dependencies:** `crosshair-tool`, custom analysis engine.

---

## 7. Project Structure

```
serenecode/
├── pyproject.toml
├── LICENSE (MIT)
├── README.md
├── SERENECODE.md              # The tool's own convention file (dogfooding)
├── src/
│   └── serenecode/
│       ├── __init__.py        # Public API surface
│       ├── cli.py             # CLI entry point (click or typer)
│       ├── init.py            # Project initialization logic
│       ├── config.py          # SERENECODE.md parser and configuration
│       ├── checker/
│       │   ├── __init__.py
│       │   ├── structural.py  # Level 1: AST-based structural checker
│       │   ├── types.py       # Level 2: mypy wrapper
│       │   ├── properties.py  # Level 3: Hypothesis + icontract-hypothesis
│       │   ├── symbolic.py    # Level 4: CrossHair wrapper
│       │   └── compositional.py  # Level 5: Module-level verification
│       ├── models.py          # CheckResult, FunctionResult, Detail dataclasses
│       ├── reporter.py        # Human, JSON, HTML report generation
│       └── templates/
│           ├── default.md     # Default SERENECODE.md template
│           ├── strict.md      # Strict template
│           └── minimal.md     # Minimal template
├── tests/
│   ├── test_structural.py
│   ├── test_types.py
│   ├── test_properties.py
│   ├── test_symbolic.py
│   ├── test_compositional.py
│   └── fixtures/              # Sample code for testing the tool itself
└── docs/
    ├── getting-started.md
    ├── serenecode-md-reference.md
    └── claude-code-integration.md
```

---

## 8. Dependencies

### Required
- Python >= 3.10
- `icontract` — Design-by-contract decorators
- `crosshair-tool` — Symbolic execution with Z3
- `hypothesis` — Property-based testing
- `icontract-hypothesis` — Bridge between icontract and Hypothesis
- `mypy` — Static type checking
- `click` or `typer` — CLI framework

### Bundled (no external dependency)
- Structural checker (uses Python `ast` module)
- SERENECODE.md parser
- Report generator

---

## 9. Development Roadmap

### Phase 0 — SERENECODE.md First
Before any code is written, the project's own SERENECODE.md is created. This file governs how all subsequent code is written — by AI agents or humans. Any AI coding agent (Claude Code, Copilot, etc.) working on Serenecode MUST read SERENECODE.md before generating any code. This phase also includes creating CLAUDE.md with a directive pointing to SERENECODE.md, and setting up the project skeleton (pyproject.toml, directory structure, dependencies).

### Phase 1 — Foundation (MVP)
- `serenecode init` with default template and CLAUDE.md integration.
- Structural checker (Level 1).
- Mypy integration (Level 2).
- JSON output format.
- Python library API with `check_structural()` and `check_types()`.
- **Testing:** Full Hypothesis property-based tests for all core checker logic. CrossHair verification of all core functions. Example-based edge case tests for AST analysis. E2E tests for `serenecode init` and `serenecode check --structural`. All code must pass `mypy --strict` before the phase is considered complete.

### Phase 2 — Core Verification
- Hypothesis + icontract-hypothesis integration (Level 3).
- CrossHair integration (Level 4).
- Full `serenecode check` pipeline.
- `serenecode status` command.
- Counterexample reporting with fix suggestions.
- **Testing:** Property-based tests for the verification pipeline orchestration. CrossHair verification of result aggregation logic. Integration tests for Hypothesis and CrossHair wrapper adapters with real sample code (valid and invalid fixtures). E2E tests for all verification levels with known-good and known-bad inputs. Regression tests for every counterexample discovered during development.

### Phase 3 — Compositional & Reporting
- Compositional verification (Level 5).
- `serenecode report` with HTML output.
- Verification level badges/annotations.
- CI/CD integration examples (GitHub Actions, etc.).
- **Testing:** Integration tests for compositional verification using multi-module fixture projects. E2E tests for `serenecode report` output in all formats (human, JSON, HTML). Snapshot tests for report output stability. CI pipeline that runs the full Serenecode verification suite on itself (dogfood in CI).

### Phase 4 — Ecosystem
- Custom SERENECODE.md rule definitions.
- Plugin system for additional verification backends.
- Community template library.
- IDE integration (VS Code extension for verification status).
- Performance optimization and caching of verification results.
- **Testing:** Plugin system tests with mock backends. Performance benchmarks with large codebases (1000+ functions). Cache correctness tests verifying that cached results are invalidated when source changes.

---

## 10. Design Principles

1. **Convention over configuration.** Sensible defaults that work out of the box. Customization is possible but not required.

2. **Fail fast, fail clearly.** The structural checker catches 80% of issues in seconds. Don't waste time on symbolic verification if contracts are missing.

3. **AI-agent-first output.** Every error message includes enough context for an AI to fix the problem. Counterexamples, file locations, and fix suggestions are always present.

4. **Progressive verification.** Not everything needs Level 5. The framework supports mixed verification levels across a codebase. Critical paths get full proofs; utilities get property testing.

5. **Dogfooding.** Serenecode's own codebase follows SERENECODE.md conventions and is verified by itself.

6. **Respect the developer.** SERENECODE.md is readable, editable, and under version control. No opaque configuration. The developer always understands what the tool expects and why.
