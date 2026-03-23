<p align="center">
  <img src="serenecode.jpg" alt="SereneCode" width="500">
</p>

<h3 align="center">A Framework for AI-Driven Development of Verifiable Systems</h3>

SereneCode is a formal verification framework for AI-generated Python. It tells the AI *how* to write verifiable code, checks that the AI followed instructions, and then verifies the code against what it claims — using symbolic execution and SMT solvers, not just tests.

> **This framework was bootstrapped with AI under its own rules.** SereneCode's SERENECODE.md was written before the first line of code, and the codebase has been developed under those conventions from the start. The current tree passes its own `serenecode check src --level 5 --allow-code-execution`, an internal strict-config Level 5 self-check, `mypy src`, and the full `pytest` suite. The tool applies its verification workflow to itself.

---

## Why This Exists

AI writes code fast. But *fast* and *correct* aren't the same thing. When you're building a medical dosage calculator, a financial ledger, or an avionics controller, "it passed my tests" isn't enough. Tests check the inputs you thought of. Formal verification uses an SMT solver to search for *any* input that breaks your contracts.

The problem is that formal verification has always been expensive — too slow, too manual, too specialized. SereneCode makes it tractable by controlling the process from the start: a convention file tells the AI to write verification-ready code, a structural linter checks it followed the rules, and CrossHair + Z3 search for contract violations via symbolic execution.

SereneCode is designed for **building new verifiable systems from scratch with AI**, not for retrofitting verification onto large existing codebases. The conventions go in before the first line of code, and every module is written with verification in mind from day one. That's what makes it work.

---

## See It In Action: The Medical Dosage Calculator

We built the same medical dosage calculator twice from the same spec — once with plain AI, once with SereneCode — to show the difference.

Both versions implement four functions: dose calculation with weight-based dosing and max caps, renal function adjustment with tiered CrCl thresholds, daily safety checks with explicit total-versus-threshold calculations, and contraindication detection across current medications.

Both versions implement the same requirements, and the plain version passes its 59-test suite. Here's what SereneCode adds on top:

| What can you claim? | Plain AI | SereneCode |
|---|---|---|
| **Dose never exceeds maximum** | Covered by unit tests | Encoded as a postcondition; the example passes symbolic verification with no counterexample found |
| **Renal adjustment never increases a dose** | Covered by unit tests | `result <= dose_mg` is an executable contract, not just a test expectation |
| **Safety result is internally consistent** | No validation — you can construct `SafetyResult(total=9999, max=100, is_safe=True)` | Representation invariants make inconsistent `SafetyResult` states unconstructable |
| **Objects are truly immutable** | `frozen=True` with mutable `set` on Drug | `_Frozen` mixin + `frozenset` — fully locked down |
| **Boundary behavior (CrCl exactly 30.0)** | Covered by explicit tests | Boundary behavior is specified in contracts and checked symbolically within analysis bounds |
| **What if someone changes the code later?** | You rely on the tests you remembered to keep | Contracts stay attached to the code and keep checking every contracted call |
| **Can a solver verify it?** | No executable specification for a solver to target | 117 executable contracts and a clean `serenecode check ... --level 5 --allow-code-execution` run |
| **Confidence in a safety-critical setting** | Better than ad hoc code, but still test-shaped confidence | Much higher: behavior is specified, runtime-checked, and solver-checked within analysis bounds |

The plain version relies on 59 tests that check specific scenarios. The SereneCode version adds 117 executable contracts across its domain models and core dosage logic. Those contracts define *what correct means* in code, get checked at runtime, and give CrossHair/Z3 something precise to search against when looking for counterexamples within analysis bounds.

> Both examples live in [`examples/dosage-regular/`](examples/dosage-regular/) and [`examples/dosage-serenecode/`](examples/dosage-serenecode/). Read them side by side.

The Serenecode dosage example currently passes `serenecode check examples/dosage-serenecode/src --level 5 --allow-code-execution` with 21 functions checked, 21 passed, 0 failed, and 0 skipped. Its local `pytest` suite is also green with 72 passing tests.

---

## How It Works

### 1. SERENECODE.md — Your AI Writes Code That's Built for Verification

A markdown file in your project root that tells AI coding agents exactly how to write code: what contracts to include, what architecture to follow, what patterns to use. When Claude Code (or another agent) reads this before generating code, it has a concrete target for producing verification-friendly output from the first keystroke.

```bash
serenecode init              # balanced defaults — contracts on public functions, hexagonal architecture
serenecode init --strict     # maximum rigor — contracts on ALL functions (public and private), no exemptions
serenecode init --minimal    # lightweight — contracts on public functions only, relaxed architecture rules
```

This creates a SERENECODE.md tailored to your project and integrates with CLAUDE.md so Claude Code follows the conventions automatically. `--strict` is maximum rigor for mission-critical code. The default is the balanced starting point; `--minimal` is a good option if you're exploring or adopting the workflow incrementally. You write the rules once, and the agent has a stable spec to follow on every iteration.

### 2. The Checker — Instant Feedback

A lightweight AST-based linter that validates code follows SERENECODE.md conventions in seconds. Missing a postcondition? No class invariant? I/O imports in a core module? Caught before you waste time on heavy verification.

```bash
serenecode check src/ --structural    # seconds
```

### 3. The Verifier — Symbolic Verification

A five-level verification pipeline that escalates from fast checks to full symbolic verification:

| Level | What | Speed | Backend |
|-------|------|-------|---------|
| **L1** | Structural conventions | Seconds | AST analysis |
| **L2** | Type correctness | Seconds | mypy --strict |
| **L3** | Property-based testing | Seconds–minutes | Hypothesis |
| **L4** | Symbolic verification | Minutes | CrossHair / Z3 |
| **L5** | Cross-module verification | Seconds | Compositional analysis |

```bash
serenecode check src/ --level 4 --allow-code-execution  # verify it
```

The full pipeline is thorough but not instant. Larger systems will take longer, and the deepest runs may surface skipped items when Hypothesis cannot synthesize valid values for complex domain types or when CrossHair hits its time budget. By default, L4 focuses on contracted top-level functions defined in each module and skips modules or signatures that are currently poor fits for direct symbolic execution, such as adapter/composition-root code, helper predicate modules, and object-heavy APIs. Not everything needs L4/L5. Critical paths get full symbolic and compositional verification. Utility functions get property testing. You decide.

Levels 3-5 import and execute project modules so Hypothesis and CrossHair can exercise real code. Deep runs therefore require explicit `--allow-code-execution` and should only be used on trusted code.

Scoped targets keep their package/import context across verification levels. In practice that means commands like `serenecode check src/core/ --level 4 --allow-code-execution` and `serenecode check src/core/models.py --level 3 --allow-code-execution` use the same local import roots and architectural module paths as a project-wide run instead of breaking relative imports or scoped core-module rules.

---

## The AI Agent Loop

SereneCode is designed for AI agents that write code and fix their own mistakes:

```
AI reads SERENECODE.md           → knows how to write verification-ready code
AI generates code with contracts → preconditions, postconditions, invariants
serenecode check --structural                        → instant: did the AI follow the rules?
serenecode check --level 4 --allow-code-execution   → deep: can the solver find any counterexample?
AI reads counterexamples         → "input x=[-1] violates postcondition"
AI fixes the code                → adjusts implementation or contract
Repeat until verified            → no counterexample found, not just tested
```

AI-generated code won't always pass verification on the first try — and that's the point. SereneCode gives the coding agent structured feedback on exactly what failed and why: counterexamples, violated contracts, and suggested fixes. The agent uses that feedback to iterate until the code passes. The value isn't in one-shotting perfection — it's in the loop that converges on verified correctness.

Works in Claude Code, works in the terminal, works in CI:

```python
import serenecode

result = serenecode.check(path="src/", level=4, allow_code_execution=True)
for failure in result.failures:
    print(failure.counterexample)  # exact input that breaks the code
    print(failure.suggestion)      # proposed fix direction
```

---

## Built With Its Own Medicine

SereneCode isn't just a tool that *tells* you to write verified code. It *is* verified code.

The SERENECODE.md convention file was the first artifact created — before any Python was written. The framework has been developed under those conventions with AI as a first-class contributor, and the repository continuously checks itself with:

- `pytest` across the full suite (currently 534 passing tests, 14 skipped)
- `mypy --strict` on `src/`
- SereneCode's own structural, type, property, symbolic, and compositional passes

On the current tree, the repository passes its checked-in self-check cleanly: `serenecode check src --level 5 --allow-code-execution` reports 137 functions checked, 137 passed, 0 failed, and 0 skipped. The same source tree also passes an internal strict-config Level 5 run (`strict_config()`) with 301 functions checked, 301 passed, 0 failed, and 0 skipped.

At Level 4, CrossHair and Z3 search for counterexamples across the codebase's symbolic-friendly contracted top-level functions. Level 5 adds structural compositional analysis: dependency direction, circular dependency detection, interface compliance, contract presence at module boundaries, and architectural invariants. Together, they provide both deep per-function verification and system-level structural guarantees.

---

## Quick Start

```bash
# Clone and install from source
git clone https://github.com/helgster77/serenecode.git
cd serenecode
pip install -e ".[verify]"

# Or with uv:
# uv sync --extra verify

# Initialize a project with conventions
serenecode init

# Let your AI agent write code following SERENECODE.md...
# Then verify:
serenecode check src/ --structural

# Or go deep:
serenecode check src/core/ --level 4 --allow-code-execution --format json
```

When you verify a nested package or a single module, Serenecode now preserves the package root and module-path context used by mypy, Hypothesis, CrossHair, and the architectural checks. That lets package-local absolute imports, relative imports, and scoped core-module rules behave the same way they do in project-wide runs.

## CLI Reference

```bash
serenecode init [--strict | --minimal]                                  # set up conventions
serenecode check [<path>] [--level 1-5] [--allow-code-execution]        # run verification
serenecode check --structural [<path>]                                  # L1 only (fast)
serenecode check --verify [<path>] --allow-code-execution               # L3-5 (deep)
serenecode status [<path>]                                              # verification status
serenecode report [<path>] [--format html] [--allow-code-execution]     # generate reports
```

**Exit codes:** 0 = passed, 1 = structural, 2 = types, 3 = properties, 4 = symbolic, 5 = compositional, 10 = internal error or deep verification refused without explicit trust.

---

## Honest Limitations

SereneCode is honest about what it can and can't do:

**Symbolic verification is not mathematical proof.** CrossHair uses bounded symbolic execution backed by Z3 — it explores execution paths within time limits (default: 30 seconds per condition, 10 seconds per path, 300 seconds per module) and searches for counterexamples. If it finds none, that's strong evidence of correctness, but it's not an unbounded proof in the Coq/Lean sense. For pure functions with simple control flow, the coverage is often effectively exhaustive. For complex code, it's bounded.

**Contracts are only as good as you write them.** A function with weak postconditions will "verify" even if the implementation is subtly wrong. SereneCode checks that contracts exist and hold, but can't check that they fully capture your intent.

**Runtime checks can be disabled.** icontract decorators are checked on every call by default, but can be disabled via environment variables for performance in production. This is a feature, not a bug — but it means runtime guarantees depend on configuration.

**Not everything should be L4.** Symbolic verification can be expensive enough to matter. Use it for critical business logic, not CLI glue. Mixed verification levels exist for this reason.

**Levels 3-5 execute your code.** Property-based and symbolic verification import project modules and run their top-level code as part of analysis. Use `--allow-code-execution` or `allow_code_execution=True` only for code you trust.

**Deep runs can be incomplete by default.** A result can include skipped items even when there are no correctness failures: Hypothesis may not be able to derive strategies for some highly structured project-local types, and CrossHair can time out on solver-heavy modules. Increase `--per-condition-timeout`, `--per-path-timeout`, or `--module-timeout` when you want to push harder on L4.

**Level 5 is structural, not semantic.** Compositional verification (L5) checks that contracts *exist* at module boundaries, that dependency direction is correct, and that interfaces structurally match. It does not verify that postconditions *logically satisfy* preconditions across call chains — that would require symbolic reasoning across module boundaries, which is a planned future enhancement. L5 catches architectural violations and contract gaps, not logical insufficiency.

**icontract-hypothesis compatibility.** The `icontract-hypothesis` bridge is currently incompatible with Python 3.14. SereneCode derives Hypothesis strategies from type annotations as a workaround.

---

## Architecture

SereneCode follows hexagonal architecture — the same pattern it enforces on your code:

```
CLI / Library API           ← composition roots
    │
    ├──▸ Pipeline           ← orchestrates L1 → L2 → L3 → L4 → L5
    │       ├──▸ Structural Checker    (ast)
    │       ├──▸ Type Checker          (mypy)
    │       ├──▸ Property Tester       (Hypothesis)
    │       ├──▸ Symbolic Verifier     (CrossHair/Z3)
    │       └──▸ Compositional Checker (ast)
    │
    ├──▸ Reporter           ← human / JSON / HTML
    │
    └──▸ Adapters → Ports   ← Protocol interfaces for all I/O
```

Core logic is pure. All I/O goes through Protocol-defined ports. The verification engine itself is verifiable.

## License

MIT
