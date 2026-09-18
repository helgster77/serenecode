# Changelog

Releases before 0.6.0 are recorded in the repository's
[commit history](https://github.com/helgster77/serenecode/commits/main) and
[tags](https://github.com/helgster77/serenecode/tags).

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
