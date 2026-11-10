# Verification levels — setup and semantics

**How to run checks:** For interactive editing with an AI assistant, prefer the **MCP tools** (`serenecode_check_function`, `serenecode_check_file`) scoped to what you just changed. Use the **`serenecode check` CLI** for CI, release gates, and full-tree batch runs. Run **`serenecode doctor`** to inspect backend availability and get MCP registration instructions. CLI output ends with a one-line reminder pointing at MCP for per-symbol follow-up.

SereneCode checks stack from fast structural rules (Level 1) through types, coverage, property tests, symbolic search, and compositional analysis. Higher levels depend on how you lay out packages and how you write `SPEC.md` integrations. This page is a short “before you invest in Level 4 or 6” checklist so later stages do not surprise you.

The achieved level is the highest contiguous completed level in the requested
run. A later successful stage cannot erase an earlier failure, skip, or lack
of execution evidence. L3 records pytest's outcome as well as coverage: failing
tests block verification even when every line was exercised. Coverage report
paths are interpreted relative to the project where pytest ran.

Scoped checks resolve requirements against the project's implementation context.
MCP function checks accept qualified names such as `Calculator.total`; ambiguous
or missing names fail explicitly. Responses retain blockers that prevented the
requested verification, and `serenecode_verify_fixed` confirms a fix only after
a passing check. Test files receive applicable test-quality checks without
production contract requirements or requests for tests of test files.

## Scope, verdicts, and configuration

Normal CLI checks start at L1. `--verify` starts at L3; direct `run_pipeline`
callers can select another `start_level`. Reaching L6 from L4 establishes only
L4–L6 completion in that run. For nonempty source scopes, L3, L4, and L5 each
require at least one passing record and no failed or skipped records. Exclusively
exempt results do not establish execution. Empty source scopes can pass without
execution and should not be treated as verification evidence.

`summary.total_functions` counts result records, including repeated symbols at
different levels and module/spec records. It is not a unique-function count.
Advisories have `exempt` status; `advisory_count` is included in `exempt`.
MCP summaries count ordinary exemptions, but its `findings` list omits them and
passing records. CLI JSON `results` retains those records and their details.

Function-scoped MCP requests execute the file pipeline and filter its results.
L3 can run the whole project's tests; coverage caching is per request, not across
edits. Module/type/spec blockers can therefore remain in a function response.
`serenecode_verify_fixed` also requires the selected message substring to be absent.

`SERENECODE.md` selects a preset and supported prose overrides. Changing a
verification level does not select a different preset. Strict has no preset path
exemptions, but backend eligibility, test-specific rules, and explicit suppressions
still apply. Module-health numeric overrides are not parsed from Markdown tables;
use `SerenecodeConfig` in the Python API for explicit thresholds.

## Level 1 (structure and contracts)

- **What counts as a contract.** A decorator carrying a literal `enabled=False`
  is never evaluated by icontract and is reported as a disabled contract. A
  lambda condition that cannot be false is reported as a tautology: a truthy
  constant, `not False`, an `or` with a constant-true operand, a comparison of
  two literals, `x is x`, `isinstance(x, object)`. `x == x` is accepted — it is
  false for NaN. The condition is read from the first positional argument or
  from `condition=`. A condition that only restates a type annotation
  (`isinstance(result, int)` on `-> int`) is **not** detected; it satisfies the
  rule and constrains nothing, so review for it.
- **Which definitions are checked.** Module-level functions and classes, their
  methods, and definitions inside a module-level `if`, `try` or `with` block
  (including `if TYPE_CHECKING:` and both branches of a version check).
  Closures inside function bodies are not checked.
- **Test files** are recognised by their path *inside the project*: a `tests`
  directory segment, a `test_*.py` name, or `conftest.py`. A project that
  happens to be checked out below a directory called `tests` is still checked
  as production code.
- **Directories skipped during discovery:** hidden directories, `__pycache__`,
  `node_modules`, any directory containing `pyvenv.cfg` or `conda-meta`, and
  `build` / `dist` only when they sit beside a `pyproject.toml`, `setup.py` or
  `setup.cfg`. A package that is itself named `build`, `dist`, `env` or `venv`
  is checked.
- **Test-file presence** is checked only when the project has at least one
  `tests/test_*.py` file. A project with no tests at all gets no
  `No test file found` findings from Level 1; Level 3 is where missing tests
  block.
- An empty `SPEC.md` is reported as a spec with no requirements.

## Level 2 (types)

mypy runs in a subprocess with `--strict`. A fatal mypy error or a timeout has
no source line and is reported as a failed `<mypy>` record at line 1. When the
project's mypy configuration sets `plugins` or `python_executable`, Level 2
refuses to start mypy without `--allow-code-execution`
(see [SECURITY.md](SECURITY.md)).

## Level 3 (test execution and coverage)

The default threshold is 80% for both per-function line and branch coverage.
Pytest collection errors and test failures block L3 independently of coverage.
A `typing.Protocol` method whose body is exactly `...` (with or without a
docstring) is not a coverage target at all: that body never executes, so its
figure would record whether the module was imported — 0% when nothing imports
it, 100% when something does — and never whether an implementation was
exercised. A Protocol method with a real body, and a `...` body in a class
that is not a Protocol, both still count.
A function with no measured body line — the body is under
`# pragma: no cover`, is a bare stub, or sits on the `def` line — is reported
as `exempt` with type `no_measurable_lines`. The `def` line runs at import, so
it alone used to read as 100% coverage; it is now no evidence either way.
Install the project's dependencies in the interpreter shown by `serenecode doctor`.
Doctor discovers backend packages; it does not run each backend as a health check.

## Level 4 (Hypothesis / property tests)

- **Domain model sampling:** Built-in strategies handle selected SereneCode types from `serenecode.models`. Separate strategies for example-style `Patient` and `Drug` models apply in `core.models` or a dotted path ending in `.core.models` (for example `myproject.core.models`). Other layouts may work through generic constructor sampling. There is no public strategy-registration hook in SereneCode. For unsupported domains, write explicit Hypothesis tests in the project test suite; L3 runs those tests. Do not weaken valid input contracts solely to satisfy the sampler.
- **Preconditions narrow the domain:** A precondition comparing a numeric parameter against a numeric literal is turned into a bounded strategy instead of a filter over the default domain. Both orientations (`x > 0`, `0 < x`) and chained comparisons (`0.0 < x < 1.0`) are read, decimal literals keep their fractional part, and a strict inequality on a `float` excludes that endpoint exactly, so an open interval is generated directly rather than sampled-and-filtered. Conditions that are not comparisons against a literal — `len(x) > 0`, a call to a predicate, a relation between two parameters — still fall back to filtering the default domain, which can exhaust the budget for a narrow domain.
- **“Skipped” / precondition messages:** If property testing reports that inputs could not be generated or preconditions filter almost everything, read the finding text and check whether the built-in strategies cover the required inputs. This is a limit of the derived strategy and reports that nothing was verified; it is not evidence of a defect, and the remedy is an explicit Hypothesis test in the project suite, not a looser contract.

The default generation budget is `max_examples=100` per eligible function; it is
an upper bound, not a recorded count of actual examples. Basic strategies sample
integers from −1000 to 1000, finite floats from −1,000,000 to 1,000,000, and strings
or bytes of length 0–100. Other type-specific strategies can use different domains.
NaN, infinity, extreme integers, and arbitrary-length strings are therefore not
covered by these basic strategies. Hypothesis runs in-process with `deadline=None`;
a hanging target has no general per-request wall-clock limit.

Contracts are enforced for the whole run at every level, including the
contracts of functions the target calls. A violation of a *callee's*
precondition is therefore reported against the target: it means the target's
own preconditions admit an input its implementation cannot handle. A target
that calls `sys.exit` (or raises another non-`Exception` exit) is reported as a
`crash` finding; a module that exits while being imported is reported as an
import failure. Neither ends the run.

## Level 5 (bounded symbolic search)

CrossHair searches eligible contracted top-level functions. The CLI defaults are
30 seconds per condition, 10 seconds per path, and 300 seconds per module. Backend
heuristics exclude some modules and signatures even under Strict. Exempt targets
were not solver-checked; a pass on another target does not cover them. A successful
search reports that no counterexample was found within the configured analysis
bounds, not a proof over all Python values or paths.

## Level 6 (compositional) — `INT-xxx` with `Kind: call`

- **What “call” means:** The checker looks at the **body** of the tagged function or class (the symbol that carries `Implements: INT-xxx`). It looks for:
  - a **call** whose callee matches the `Target` string (simple name, dotted name, or suffix match on the last segment), or
  - an **`isinstance(..., Type)`** where the type expression matches that same target (including `pkg.sub.Type` and tuple-of-types forms).
- **Comma-separated targets:** If `Target` contains commas, **every** listed target must appear (logical **AND**). Prefer one integration per boundary if that keeps the spec easier to read.
- **Markdown in fields:** `Source`, `Target`, and related INT lines may use backticks; the parser strips them so `` `module.Type` `` matches `module.Type`.

The dependency-direction rule reads every import in a core or ports module,
wherever the statement is written: top level, inside a function, a `try`, or an
`if` (including `if TYPE_CHECKING:`). `from package import name` counts as an
import of `package.name` too, and a string literal passed to
`importlib.import_module` or `__import__` counts as an import of that module.
A module name computed at runtime cannot be seen. Cycle detection uses
top-level imports only. Contract presence at this level follows Level 1: a
`# no-precondition: <reason>` waiver is honoured, class invariants are
required only when the configuration requires them, and test files are not
asked for contracts.

L6 checks recognized AST structure, explicit Protocol inheritance/signature
compatibility, dependency direction, and contract presence. It does not establish
logical compatibility of preconditions and postconditions across a call chain.
Integration tags and a syntactically matching call are not proof of correct
runtime ordering, data flow, or requirement behavior.

## Spec ergonomics

- **Where tags are read:** `Implements:` and `Verifies:` are recognised on function and class docstrings. One tag may list several identifiers, comma-separated, mixing `REQ-` and `INT-`. A tag in a *module* docstring is not read; it is reported as a misplaced tag rather than silently ignored, because the only other symptom is the item being reported as having no implementation and no test.
- **Narrative vs traceability:** PRDs, `README` sections, and `*_SPEC.md` files are inputs. REQ/INT traceability and `serenecode check --spec` use project-root **SPEC.md** by default; `--spec PATH` explicitly selects a structured spec elsewhere. A structured spec must include a `**Source:** …` line (see the Spec Traceability section in your project's `SERENECODE.md` from `serenecode init`, or the embedded templates in `src/serenecode/templates/content.py`). Run `serenecode doctor` to see whether SPEC.md and narrative-looking files were detected at the project root.
- Use **one primary target per comma segment**; avoid stuffing unrelated names into a single `Target` line unless you intend AND semantics.
- Align **dotted names** with how types appear in code (`from pkg import X as Y` is easier to reason about when `Target` uses the same simple name the implementation calls).

## Related reading

- [SECURITY.md](SECURITY.md) — trust model for `--allow-code-execution` (required for Levels 3–6 as implemented today).
- Your project's `SERENECODE.md` (from `serenecode init`) — conventions the structural checker enforces; source templates live in `src/serenecode/templates/content.py`.
