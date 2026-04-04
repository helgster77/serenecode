<p align="center">
  <img src="serenecode.jpg" alt="SereneCode" width="500">
</p>

<h3 align="center">A Framework for AI-Driven Development of Verifiable Systems</h3>

SereneCode is a spec-to-verified-implementation framework for AI-generated Python. It ensures that every requirement in your spec is implemented, tested, and formally verified — closing the gap between what you asked for and what the AI built. The workflow starts from a spec with traceable requirements (REQ-xxx), enforces that the AI writes verifiable code with contracts and tests, then verifies at multiple levels — from structural checks and test coverage through property-based testing to symbolic execution with an SMT solver. You choose the verification depth during interactive setup: lightweight for internal tools, balanced for production systems, strict for safety-critical code. AI agents write code fast but can miss requirements and skip edge cases; SereneCode closes that gap with spec traceability, test-existence enforcement, and formal verification.

> **This framework was bootstrapped with AI under its own rules.** SereneCode's SERENECODE.md was written before the first line of code, and the codebase has been developed under those conventions from the start. The current tree passes its own `serenecode check src --level 6 --allow-code-execution`, an internal strict-config Level 6 self-check in the test suite, `mypy src examples/dosage-serenecode/src`, the shipped example's check, and the full `pytest` suite (769 passing tests, 16 skipped). The verification output is transparent about scope: exempt modules (adapters, CLI, ports) and functions excluded from deep verification (non-primitive parameter types) are reported as "exempt" rather than silently omitted.

---

## Why This Exists

AI writes code fast. But *fast* and *correct* aren't the same thing. When you're building a medical dosage calculator, a financial ledger, or an avionics controller, "it passed my tests" isn't enough. Tests check the inputs you thought of. Formal verification uses an SMT solver to search for *any* input that breaks your contracts.

The problem is that formal verification has always been expensive — too slow, too manual, too specialized. SereneCode makes it tractable by controlling the process from the start: a convention file tells the AI to write verification-ready code, a structural linter checks it followed the rules, and CrossHair + Z3 search for contract violations via symbolic execution.

SereneCode is designed for **building new verifiable systems from scratch with AI**, not for retrofitting verification onto large existing codebases. The conventions go in before the first line of code, and every module is written with verification in mind from day one. That's what makes it work. SereneCode is a best-effort tool, not a guarantee — see the [Disclaimer](#disclaimer) for important limitations on what it can and cannot assure.

### Choosing the Right Level

The cost of verification should be proportional to the cost of a bug. Each level generates a different SERENECODE.md with different requirements for the AI, so the choice shapes how code is *written*, not just how it's checked. You make this choice during `serenecode init` — it cannot be changed after implementation starts.

| | **Minimal** (Level 2) | **Default** (Level 4) | **Strict** (Level 6) |
|---|---|---|---|
| **Verifies through** | L2 (structure + types) | L4 (+ test coverage + properties) | L6 (+ symbolic + compositional) |
| **What the AI must write** | Contracts on public functions, type annotations | + description strings, class invariants, hexagonal architecture | + contracts on *all* functions, loop invariants, domain exceptions, no exemptions |
| **What catches bugs** | Runtime contract checks, mypy | + L3 surfaces untested code paths and generates test suggestions; L4 tests contracts against hundreds of random inputs | + SMT solver searches for *any* counterexample within analysis bounds |
| **Good for** | Internal tools, scripts, prototypes | Production APIs, business logic, data pipelines | Medical, financial, infrastructure, regulated systems |
| **The tradeoff** | Low ceremony, but contracts are only checked at the boundaries you wrote them | Moderate overhead; architecture rules keep core logic pure and testable | Significant overhead — every loop gets an invariant comment, every helper gets a contract. Justified when the cost of an undiscovered bug is measured in patient harm, financial loss, or regulatory failure |

Pick the level that matches the stakes. Safety-critical code should start at Strict.

---

## See It In Action: The Medical Dosage Calculator

We built the same medical dosage calculator twice from the same spec — once with plain AI, once with SereneCode — to show the difference.

Both versions implement four functions: dose calculation with weight-based dosing and max caps, renal function adjustment with tiered CrCl thresholds, daily safety checks with explicit total-versus-threshold calculations, and contraindication detection across current medications.

Both versions implement the same requirements, and the plain version passes its 59-test suite. Here's what SereneCode adds on top:

| What can you claim? | Plain AI | SereneCode |
|---|---|---|
| **Dose never exceeds maximum** | Covered by unit tests | Encoded as a postcondition; bounded symbolic search found no counterexample within analysis bounds |
| **Renal adjustment never increases a dose** | Covered by unit tests | `result <= dose_mg` is an executable contract, not just a test expectation |
| **Safety result is internally consistent** | No validation — you can construct `SafetyResult(total=9999, max=100, is_safe=True)` | Postcondition on `check_daily_safety` enforces `is_safe == (total <= max)` — inconsistent results cannot be produced through the contracted API |
| **Objects are truly immutable** | `frozen=True` with mutable `set` on Drug | `frozen=True` with class invariants enforcing valid state — mutations raise `FrozenInstanceError` and invariants guarantee internal consistency |
| **Boundary behavior (CrCl exactly 30.0)** | Covered by explicit tests | Boundary behavior is specified in contracts; bounded symbolic search found no counterexample |
| **What if someone changes the code later?** | You rely on the tests you remembered to keep | Contracts stay attached to the code and keep checking every contracted call |
| **Can a solver verify it?** | No executable specification for a solver to target | 42 executable contracts and a clean `serenecode check ... --level 6 --allow-code-execution` run |
| **Confidence in a safety-critical setting** | Better than ad hoc code, but still test-shaped confidence | Higher: behavior is formally specified, runtime-checked, and solver-checked within analysis bounds — but bounded search is not proof |

The plain version relies on 59 tests that check specific scenarios. The SereneCode version adds 42 executable contracts across its domain models and core dosage logic. Those contracts define *what correct means* in code, get checked at runtime, and give CrossHair/Z3 something precise to search against when looking for counterexamples within analysis bounds.

> Both examples live in [`examples/dosage-regular/`](examples/dosage-regular/) and [`examples/dosage-serenecode/`](examples/dosage-serenecode/). Read them side by side.

The Serenecode dosage example currently passes `serenecode check src/ --level 6 --allow-code-execution` from within the example directory. Its local `pytest` suite is also green with 67 passing tests.

---

## How It Works

### 1. Interactive Setup — `serenecode init`

Run `serenecode init` and answer two questions:

**Spec question:** Do you already have a spec, or will you write one with your coding assistant? Both options set up spec traceability with REQ-xxx requirement identifiers — the difference is the workflow your assistant follows.

**Verification level:** Minimal (L2), Default (L4), or Strict (L6). This determines what conventions your SERENECODE.md will require and cannot be changed after implementation starts.

```bash
serenecode init
```

This creates SERENECODE.md (project conventions including spec traceability) and CLAUDE.md (instructions for your AI coding assistant) tailored to your answers. The conventions become the contract between you, your coding assistant, and the verification tool. SERENECODE.md includes instructions for converting raw specs into SereneCode format (REQ-xxx identifiers), validating them with `serenecode spec SPEC.md`, creating an implementation plan, and building from it — the coding agent handles this workflow automatically.

### 2. The Checker — Structural Enforcement

A lightweight AST-based checker that validates code follows SERENECODE.md conventions in seconds. Missing a postcondition? No class invariant? No test file for a module? Caught before you waste time on heavy verification.

```bash
serenecode check src/ --structural          # structural conventions
serenecode check src/ --spec SPEC.md        # + spec traceability
```

The `--spec` flag verifies that every REQ in the spec has an `Implements: REQ-xxx` tag in the code and a `Verifies: REQ-xxx` tag in the tests. No requirement goes unimplemented or untested.

### 3. The Verifier — Deep Verification

A six-level verification pipeline that escalates from fast checks to full symbolic verification:

| Level | What | Speed | Backend |
|-------|------|-------|---------|
| **L1** | Structural conventions | Seconds | AST analysis |
| **L2** | Type correctness | Seconds | mypy --strict |
| **L3** | Test coverage analysis | Seconds–minutes | coverage.py |
| **L4** | Property-based testing | Seconds–minutes | Hypothesis |
| **L5** | Symbolic search (bounded) | Minutes | CrossHair / Z3 |
| **L6** | Cross-module verification | Seconds | Compositional analysis |

```bash
serenecode check src/ --level 6 --allow-code-execution  # verify it
```

**L3 Test Coverage** is where SereneCode checks that the AI's tests actually exercise the code it wrote. AI agents can be suboptimal at writing tests — they tend to cover the happy path, skip edge cases, and miss error branches. L3 runs your existing tests under coverage.py tracing, measures per-function line and branch coverage, and reports exactly which lines and branches are untested. For each coverage gap, it generates concrete test suggestions including mock necessity assessments: each dependency is classified as REQUIRED (external I/O — must mock) or OPTIONAL (internal code — consider using the real implementation). This gives the AI agent actionable feedback to improve its own tests rather than leaving coverage gaps undetected. When no tests exist for a module, L3 reports this as a failure — missing tests must be written. At L1, the structural checker also verifies that every non-exempt source module has a corresponding `test_<module>.py` file.

The full pipeline is thorough but not instant. Larger systems will take longer, and the deepest runs may surface skipped items when Hypothesis cannot synthesize valid values for complex domain types or when CrossHair hits its time budget. By default, L5 focuses on contracted top-level functions defined in each module and skips modules or signatures that are currently poor fits for direct symbolic execution, such as adapter/composition-root code, helper predicate modules, and object-heavy APIs. Not everything needs L5/L6. Critical paths get full symbolic and compositional verification. Utility functions get property testing. A Level 4 run only counts as achieved when at least one contracted property target was actually exercised.

Levels 3-6 import and execute project modules so coverage.py, Hypothesis, and CrossHair can exercise real code. Deep runs therefore require explicit `--allow-code-execution` and should only be used on trusted code.

Scoped targets keep their package/import context across verification levels. In practice that means commands like `serenecode check src/core/ --level 4 --allow-code-execution` and `serenecode check src/core/models.py --level 3 --allow-code-execution` use the same local import roots and architectural module paths as a project-wide run instead of breaking relative imports or scoped core-module rules. Those scoped core/exemption rules are matched on path segments, not raw substrings, so names like `notcli.py`, `viewmodels.py`, and `transports/` do not accidentally change policy classification. Standalone files with non-importable names are also targeted correctly for CrossHair via `file.py:line` references.

---

## The AI Agent Loop

SereneCode is designed for spec-driven development with AI agents:

```
serenecode init                  → interactive setup: spec mode + verification level
serenecode spec SPEC.md          → validate spec is ready (REQ-xxx format, no gaps)
AI reads SERENECODE.md + SPEC.md → knows the conventions and what to build
AI implements from spec          → Implements: REQ-xxx in docstrings, contracts, tests
serenecode check src/ --spec SPEC.md --structural   → did the AI follow rules? all REQs covered?
serenecode check src/ --level 5 --allow-code-execution --spec SPEC.md   → deep verification
AI reads findings                → missing REQs, counterexamples, untested paths
AI fixes the code                → adjusts implementation, adds tests, closes gaps
Repeat until verified            → all REQs implemented + tested + no counterexamples
```

AI-generated code won't always pass verification on the first try — and that's the point. SereneCode gives the coding agent structured feedback on exactly what failed and why: missing requirement implementations, counterexamples, violated contracts, untested modules, and suggested fixes. When there are many findings, SereneCode suggests the agent spawn subagents to address groups of related issues in parallel. **The value isn't in one-shotting perfection — it's in the loop that converges on verified completeness and correctness.**

Works in Claude Code, works in the terminal, works in CI:

```python
import serenecode

result = serenecode.check(path="src/", level=5, allow_code_execution=True)
for failure in result.failures:
    print(f"{failure.function} @ {failure.file}:{failure.line}")
    for detail in failure.details:
        if detail.counterexample is not None:
            print(detail.counterexample)  # exact input that breaks the code
        if detail.suggestion is not None:
            print(detail.suggestion)      # proposed fix direction
```

---

## Built With Its Own Medicine

SereneCode isn't just a tool that *tells* you to write verified code. It *is* verified code.

The SERENECODE.md convention file was the first artifact created — before any Python was written. The framework has been developed under those conventions with AI as a first-class contributor, and the repository continuously checks itself with:

- `pytest` across the full suite (currently 769 passing tests, 16 skipped)
- `mypy --strict` across `src/` and `examples/dosage-serenecode/src/`
- SereneCode's own structural, type, property, symbolic, and compositional passes

On the current tree, `serenecode check src --level 6 --allow-code-execution` runs all six verification levels. The exempt items include adapter modules (which handle I/O and are integration-tested), port interfaces (Protocols that define abstract contracts), CLI entry points, and functions whose parameter types are too complex for automated strategy generation or symbolic execution. Exempt items are visible in the output — they are not silently omitted.

At Level 5, CrossHair and Z3 search for counterexamples across the codebase's symbolic-friendly contracted top-level functions. Functions with non-primitive parameters (custom dataclasses, Protocol implementations, Callable types) are reported as exempt because the solver cannot generate inputs for them. Level 6 adds structural compositional analysis: dependency direction, circular dependency detection, interface compliance, contract presence at module boundaries, aliased cross-module call resolution, and architectural invariants. Interface compliance follows explicit `Protocol` inheritance and checks substitutability, including extra required parameters and incompatible return annotations. Together, they provide both deep per-function verification and system-level structural guarantees — but the structural checks at L6 verify contract *presence*, not logical *sufficiency* across call chains.

---

## Quick Start

```bash
# Install from PyPI
pip install serenecode

# Initialize — interactive setup (spec mode + verification level)
serenecode init

# Place your spec in the project directory, then start a coding session.
# Your agent reads SERENECODE.md, converts the spec to REQ-xxx format,
# validates it, creates an implementation plan, and builds from it.

# Verify structure + spec traceability:
serenecode check src/ --spec SPEC.md --structural

# Go deep — test coverage, property testing, symbolic verification:
serenecode check src/ --level 5 --allow-code-execution --spec SPEC.md
```

JSON output (via `--format json`) includes top-level `passed`, `level_requested`, and `level_achieved` fields alongside the summary and per-function results.

When you verify a nested package or a single module, Serenecode preserves the package root and module-path context used by mypy, Hypothesis, CrossHair, and the architectural checks. That lets package-local absolute imports, relative imports, and scoped core-module rules behave the same way they do in project-wide runs.

## CLI Reference

```bash
serenecode init [<path>]                                                # interactive setup
serenecode spec <SPEC.md>                                               # validate spec readiness
                [--format human|json]
serenecode check [<path>] [--level 1-6] [--allow-code-execution]        # run verification
                          [--spec SPEC.md]                              #   spec traceability
                          [--format human|json]                         #   output format
                          [--structural] [--verify]                     #   L1 only / L3-6 only
                          [--per-condition-timeout N]                   #   L5 CrossHair budgets
                          [--per-path-timeout N] [--module-timeout N]   #   (defaults: 30/10/300s)
                          [--workers N]                                 #   L5 parallel workers
serenecode status [<path>] [--format human|json]                        # verification status
serenecode report [<path>] [--format human|json|html]                   # generate reports
                           [--output FILE] [--allow-code-execution]     #   write to file
```

**Exit codes:** 0 = passed, 1 = structural, 2 = types, 3 = coverage, 4 = properties, 5 = symbolic, 6 = compositional, 10 = internal error or deep verification refused without explicit trust.

---

## Honest Limitations

SereneCode is honest about what it can and can't do:

**"No counterexample found" is not "proven correct."** CrossHair uses bounded symbolic execution backed by Z3 — it explores execution paths within time limits (default: 30 seconds per condition, 10 seconds per path, 300 seconds per module) and searches for counterexamples. When it reports "no counterexample found within analysis bounds," that's strong evidence of correctness for the explored paths, but it's not an unbounded proof in the Coq/Lean sense. For pure functions with simple control flow, the coverage is often effectively exhaustive. For complex code, it's bounded. The tool's output now uses this honest language rather than saying "verified."

**Contracts are only as good as you write them.** A function with weak postconditions will pass verification even if the implementation is subtly wrong. SereneCode checks that contracts exist and hold, but can't check that they fully capture your intent. Tautological contracts like `lambda self: True` are now flagged by the conventions and should not be used — they provide no verification value.

**Exempt items are visible, not hidden.** Modules exempt from structural checking (adapters, CLI, ports, `__init__.py`) and functions excluded from deep verification (non-primitive parameter types, adapter code) are reported as "exempt" in the output rather than being silently omitted. This makes the verification scope transparent: the tool reports passed, failed, skipped, and exempt counts separately so you can see exactly what was and wasn't deeply verified. Previous versions silently omitted these, inflating the apparent scope.

**Runtime checks can be disabled.** icontract decorators are checked on every call by default, but can be disabled via environment variables for performance in production. This is a feature, not a bug — but it means runtime guarantees depend on configuration.

**Not everything can be deeply verified.** Functions with complex domain-type parameters (custom dataclasses, Callable, Protocol implementations) are automatically excluded from L4/L5 because the tools cannot generate valid inputs for them — they show up as "exempt" in the output. See "Choosing the Right Level" above for guidance on which verification depth fits your system.

**Levels 3-6 execute your code.** Coverage analysis, property-based testing, and symbolic verification import project modules and run their top-level code as part of analysis. Module loading uses `compile()` + `exec()` on target source files and their transitive dependencies. There is no sandboxing or syscall filtering — a malicious `.py` file in the target directory gets full access to the host. Use `--allow-code-execution` or `allow_code_execution=True` only for code you trust. Subprocess-based backends (CrossHair, pytest/coverage) receive module paths and search paths from the source discovery layer; symlink-based directory traversal is blocked (`followlinks=False`), but the trust boundary ultimately relies on the `--allow-code-execution` gate.

**Deep runs can be incomplete by default.** A result can include skipped items even when there are no correctness failures: Hypothesis may not be able to derive strategies for some highly structured project-local types, and CrossHair can time out on solver-heavy modules once the module budget is exhausted. When a run exercises no property-testing targets at all, Serenecode does not claim L4 was achieved. When a scoped run produces no symbolic findings at all, Serenecode does not claim L5 was achieved. A verification level is only marked as achieved when results are non-empty with no failures and no skips — empty results from L3/L4/L5 backends mean "nothing was exercised," not "everything passed." Increase `--per-condition-timeout`, `--per-path-timeout`, or `--module-timeout` when you want to push harder on L5.

**Level 6 is structural, not semantic.** Compositional verification (L6) checks that contracts *exist* at module boundaries, that dependency direction is correct, and that interfaces structurally match, including explicit `Protocol` inheritance and signature-shape compatibility. It does not verify that postconditions *logically satisfy* preconditions across call chains — that would require symbolic reasoning across module boundaries, which is a planned future enhancement. L6 catches architectural violations and contract gaps, not logical insufficiency. Source files with syntax errors are now reported as skipped with an actionable message instead of silently producing an empty analysis.



---

## Architecture

SereneCode follows hexagonal architecture — the same pattern it enforces on your code:

```
CLI / Library API           ← composition roots (interactive init, spec validation)
    │
    ├──▸ Pipeline           ← orchestrates L1 → L2 → L3 → L4 → L5 → L6
    │       ├──▸ Structural Checker    (ast)
    │       ├──▸ Spec Traceability     (REQ-xxx → Implements/Verifies)
    │       ├──▸ Test Existence        (test_<module>.py discovery)
    │       ├──▸ Type Checker          (mypy)
    │       ├──▸ Coverage Analyzer     (coverage.py)
    │       ├──▸ Property Tester       (Hypothesis)
    │       ├──▸ Symbolic Checker      (CrossHair/Z3)
    │       └──▸ Compositional Checker (ast)
    │
    ├──▸ Reporter           ← human / JSON / HTML
    │
    └──▸ Adapters → Ports   ← Protocol interfaces for all I/O
```

Core logic is pure. All I/O goes through Protocol-defined ports. The verification engine itself is verifiable.

## Disclaimer

SereneCode is provided as-is, without warranty of any kind. It is a best-effort tool that helps surface defects through contracts, property-based testing, and bounded symbolic execution — but it cannot guarantee the absence of bugs. "No counterexample found" means the solver did not find one within its analysis bounds, not that none exists. Verification results depend on the quality of the contracts you write, the time budgets you configure, and the inherent limitations of the underlying tools.

Users are responsible for the correctness, safety, and regulatory compliance of their own systems. SereneCode is not a substitute for independent code review, domain-expert validation, or any certification process required by your industry. If you are building safety-critical software, use this framework as one layer of assurance among many — not as the only one.

## License

MIT
