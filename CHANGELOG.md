# Changelog

Releases before 0.6.0 are recorded in the repository's
[commit history](https://github.com/helgster77/serenecode/commits/main) and
[tags](https://github.com/helgster77/serenecode/tags).

## Unreleased

These changes make several verdicts stricter and one category of failure
visible for the first time. **A project that passed on 0.6.0 can fail after
this change**; each item below says when.

### Fixed — verdicts that were wrong

- **Contracts were switched off for the whole process at Levels 5 and 6.**
  Importing CrossHair replaces icontract's runtime checks with no-ops, and the
  CrossHair adapter imported it into the SereneCode process. Level 4 running
  inside a Level 5 or Level 6 pipeline therefore could not observe a
  postcondition violation, and a function that called `sys.exit(0)` ended the
  run with exit code 0 and no report. CrossHair is now imported only inside
  the spawned Level 5 worker. *A project whose Level 4 findings were hidden
  this way now fails at Level 4.* SereneCode's own source had six such
  findings — callers admitting inputs their callees reject — which are fixed
  here.
- **A project checked out below a directory named `tests` was treated as a test
  suite**: no contract rules, no test-presence check, no dead-code analysis.
  Test files are now recognised by their path inside the project.
- **Packages named `build`, `dist`, `env` or `venv` were silently skipped.**
  Build output is now skipped only beside packaging metadata, and virtual
  environments are recognised by `pyvenv.cfg` / `conda-meta`.
- **`sys.exit` in checked code ended the run** with the target's exit code.
  It is now a Level 4 `crash` finding, or an import failure when it happens at
  import time.
- **A function with no measurable body lines passed Level 3 at 100%** — the
  `def` line runs at import. It is now `exempt` (`no_measurable_lines`).
- **Level 6 reported a missing precondition on a function carrying the
  `# no-precondition:` waiver**, and required class invariants when the
  configuration did not. A project could pass Levels 1–5 and fail Level 6 on
  a rule it had been told how to satisfy.
- **Level 6 required contracts on test functions** when the checked path
  included the tests, so `serenecode check . --level 6` on a project root
  failed on every test that called a contracted function. Level 6 now skips
  test files for contract presence, assume-guarantee and data flow, as Level 1
  does.
- **A mypy fatal error or timeout raised a contract violation** instead of
  being reported. It is now a failed `<mypy>` record.
- **An empty `SPEC.md` ended the run with a traceback.** It is now the
  `no_requirements` finding.
- **Text printed by project code could corrupt `--format json` output** and the
  MCP stdio stream. It now goes to stderr.
- **SereneCode's own contracts had gaps that disabled enforcement was hiding.**
  With contracts live, Level 4 on SereneCode itself found inputs its own
  preconditions admitted and its invariants then rejected: out-of-range levels
  and negative durations in `make_check_result`, blank or NUL-carrying paths in
  `SourceFile` and `check_compositional`, and whitespace-only names in the
  Level 6 parsing dataclasses. The preconditions and invariants now state the
  domain the code relies on. Two tailored Level 4 strategies for SereneCode's
  own types matched module names those types no longer have and are removed;
  the generic derivation was already what ran.

### Changed — Level 1 rejects more

- A contract decorator with `enabled=False` is reported as disabled.
- More always-true conditions are reported as tautologies: truthy constants,
  `not False`, `... or True`, comparisons of two literals, `x is x`,
  `isinstance(x, object)`. `x == x` stays accepted (it is false for NaN).
- A condition passed as `condition=` gets the same binding and tautology checks
  as a positional one.
- Functions and classes defined inside a module-level `if`, `try` or `with`
  are checked like top-level definitions.
- The suggestion on a missing-contract finding no longer proposes
  `isinstance(result, <annotation>)`, which raised `TypeError` for `-> None`.

### Changed — Level 2 and Level 6

- Level 2 does not start mypy without `--allow-code-execution` when the
  project's mypy configuration sets `plugins` or `python_executable`; both
  make mypy run code chosen by the project. It reports which file and setting.
- The Level 6 layer rule reads imports inside functions, `try` and `if` blocks,
  `from package import name` forms, and string-literal dynamic imports.

### Changed — packaging

- The `mcp` extra requires `mcp>=1.2,<2`. `mcp.server.fastmcp` does not exist
  before 1.2 and is a raising stub in 2.x. A failed import of the MCP server
  now reports the underlying error.

### Added

- An agent skill at `skills/serenecode/SKILL.md` that packages the
  spec-first, contract, and verification workflow for SKILL.md-compatible
  agents. A unit test checks every MCP tool, CLI command, flag, and preset
  depth that the skill names against the server, the CLI, and the configs.

## 0.6.0 — 2026-09-18

Level 1 and Level 4 both changed behaviour in ways that can move a project's
verdict in either direction. Read **Upgrading** before adopting this release in
CI.

### Upgrading

**A project that passed on 0.5.1 can fail on 0.6.0**, because Level 1 now
rejects contracts it previously accepted:

- A `require`/`ensure` condition over `*args` or `**kwargs`. icontract never
  binds the variadic tuple or the keyword mapping, so the condition was either
  evaluated against one positional argument or raised `TypeError` on every
  call. Rewrite it over icontract's `_ARGS` / `_KWARGS` placeholders, or over
  explicit parameters.
- A condition naming a parameter the signature does not have. icontract raises
  `TypeError` when such a condition runs. This also catches a decorator stack
  separated from its `def` by an inserted function.
- `@icontract.ensure(lambda result: ...)` on a function annotated `-> None`,
  where `result` is bound to `None`. `result is None` guards are still
  accepted.
- An `Implements:` or `Verifies:` tag in a *module* docstring, which is read by
  nothing. Move it to the implementing or testing symbol, or keep a
  module-level index under a heading that does not read as a tag.

None of these were enforceable as written, so each finding names code that was
already broken; the checks are exact rather than heuristic, because icontract
itself fails on the same inputs.

**A project can also newly pass**, where 0.5.1 reported a defect that was not
one:

- Level 4 derives generator domains from numeric preconditions correctly.
  `0.0 < x < 1.0` previously produced a domain disjoint from the contract and
  failed as unsatisfiable; the documented workaround was to widen a correct
  contract. Open intervals, decimal literals, negative literals, bounds written
  on either side of the parameter, and chained comparisons all work now.
- Level 3 no longer treats a `typing.Protocol` method whose body is exactly
  `...` as a coverage target.
- Dead-code advisories are suppressed for definitions whose callers a
  name-based scan cannot see.

**Output changes that affect tooling:**

- The human summary renders advisories inside the exempt figure —
  `57 exempt (33 advisory)` — because advisories are a subset of the exempt
  count, not an addend. The JSON schema is unchanged.
- Dead-code findings now carry the path spelling the caller passed in. A single
  report previously mixed relative paths from the structural checker with
  absolute paths from the dead-code backend.
- Hypothesis `Unsatisfiable` is reported as a `skipped` record rather than a
  `crash` failure. It states that nothing was verified, so the level is still
  not achieved, but it no longer reads as a defect or suggests weakening the
  contract.

### Added

- `# no-precondition: <reason>` waives the precondition requirement for one
  function, for parameters whose annotated domain is already entirely valid.
  The reason is mandatory; a bare marker waives nothing. (REQ-050)
- `serenecode init` accepts `--level {minimal,default,strict}`,
  `--spec {existing,generate}`, `--mcp/--no-mcp` and `--yes`, and completes
  without reading stdin once every answer is supplied. Overwriting an existing
  `SERENECODE.md` or `CLAUDE.md` requires `--yes`. (REQ-044)
- Level 1 contract-binding checks. (REQ-037 – REQ-042, INT-005)
- Traceability tag placement check. (REQ-052)
- `Implements:`/`Verifies:` accepting several comma-separated identifiers is
  now specified and documented; the behaviour itself is unchanged. (REQ-053)

### Changed

- Dead-code advisories are suppressed for functions carrying a registering
  decorator (`@app.get`, `@router.post`, `@asynccontextmanager`, …) and for
  methods overriding a base declared within the scanned sources or marked
  `@override`. Variables, classes, imports, and definitions wearing only inert
  decorators are unaffected. (REQ-045)
- `typing.Protocol` methods with a `...` body are no longer coverage targets.
  (REQ-051)
- The exempt/advisory summary format. (REQ-046)
- `pytest` and `pytest-cov` are runtime dependencies, so the Level 3 coverage
  runner works from a plain install.

### Fixed

- Level 4 numeric bound derivation, which truncated decimal literals, applied
  an integer offset to float bounds, and ignored bounds written to the left of
  the parameter. (REQ-048)
- `Unsatisfiable` reported as a crash with advice to weaken the contract.
  (REQ-049)
- Dead-code finding paths. (REQ-047)
- `--format json` writing only the JSON document to stdout is now specified and
  covered by a regression test that pipes stdout straight into a parser. The
  behaviour was already correct in 0.5.1. (REQ-043)
