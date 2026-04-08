# Implementation Plan — Integration Traceability and Dead Code Review

This plan implements the requirements in [SPEC.md](/Users/helgster/Projects/serenecode/SPEC.md) for `INT-xxx` integration-point support, semantic integration verification, dead-code reporting, and the corresponding documentation and MCP rollout.

---

## Guiding Decisions

### 1. Keep the current traceability model and extend it additively

We will not replace the current REQ workflow. We will extend it so the system understands two identifier namespaces:

- `REQ-xxx` for behavioral requirements
- `INT-xxx` for integration points

The existing `Implements:` and `Verifies:` docstring tags remain the only tagging mechanism. This keeps authored code simple and preserves backward compatibility.

### 2. Split integration checking into two layers

- Level 1 remains responsible for baseline spec validation, integration traceability, and dead-code review.
- Those baseline checks therefore appear in every run whose requested maximum level is 1 through 6.
- Higher levels may add stronger evidence.
- Level 6 adds the deepest semantic validation of declared integrations.

This keeps reporting honest:

- missing tag coverage is a traceability problem
- broken or absent actual interaction is an integration-semantics problem

### 3. Make dead-code analysis advisory, not auto-remedial

Dead-code findings should tell the coding agent to ask the user whether to remove or allowlist the code. The checker should never frame these findings as unconditional auto-delete actions.

### 4. Prefer additive MCP changes over breaking renames

Existing REQ-oriented MCP tools should keep working. New integration and dead-code capabilities should be added through backward-compatible extensions or new tools/resources.

---

## Workstreams

## Workstream A — Spec Parsing and Traceability Core

**Goal:** Generalize the current REQ-only traceability engine so it can validate and report both `REQ-xxx` and `INT-xxx`.

**Primary files**

- [src/serenecode/checker/spec_traceability.py](/Users/helgster/Projects/serenecode/src/serenecode/checker/spec_traceability.py)
- [src/serenecode/contracts/predicates.py](/Users/helgster/Projects/serenecode/src/serenecode/contracts/predicates.py)
- [src/serenecode/models.py](/Users/helgster/Projects/serenecode/src/serenecode/models.py)

**Test files**

- [tests/unit/checker/test_spec_traceability.py](/Users/helgster/Projects/serenecode/tests/unit/checker/test_spec_traceability.py)
- [tests/e2e/test_check_command.py](/Users/helgster/Projects/serenecode/tests/e2e/test_check_command.py)

**Planned changes**

- Add parsing helpers for `INT-xxx` identifiers and mixed `REQ`/`INT` references.
- Introduce pure data structures for parsed integration declarations.
- Extend spec validation to check:
  - `INT-xxx` headings
  - sequential numbering
  - duplicate identifiers
  - required `Kind`, `Source`, and `Target` fields
  - supported `Kind` values
  - `Supports:` references to valid `REQ-xxx` identifiers
- Extend implementation and verification extraction so `Implements:` and `Verifies:` can carry both REQ and INT identifiers.
- Extend traceability results so integration points get the same coverage-state reporting as requirements.
- Preserve the current public helpers for REQ workflows where possible, adding new helpers rather than breaking signatures.

**Requirements covered**

- REQ-001 through REQ-011
- REQ-024
- REQ-027

**Acceptance criteria**

- A REQ-only spec still validates and reports exactly as before.
- A mixed REQ/INT spec validates correctly.
- Missing, untested, or orphaned `INT-xxx` references produce structured findings at Level 1.
- Those Level 1 findings are therefore visible in every `serenecode check` run from Level 1 through Level 6.

---

## Workstream B — Semantic Integration Verification

**Goal:** Verify that declared integrations are actually present in the implementation, not just tagged.

**Primary files**

- [src/serenecode/checker/compositional.py](/Users/helgster/Projects/serenecode/src/serenecode/checker/compositional.py)
- [src/serenecode/checker/spec_traceability.py](/Users/helgster/Projects/serenecode/src/serenecode/checker/spec_traceability.py)
- [src/serenecode/core/pipeline.py](/Users/helgster/Projects/serenecode/src/serenecode/core/pipeline.py)

**Test files**

- [tests/unit/checker/test_compositional.py](/Users/helgster/Projects/serenecode/tests/unit/checker/test_compositional.py)
- [tests/unit/checker/test_spec_traceability.py](/Users/helgster/Projects/serenecode/tests/unit/checker/test_spec_traceability.py)
- [tests/integration/test_checkers_real_code.py](/Users/helgster/Projects/serenecode/tests/integration/test_checkers_real_code.py)

**Planned changes**

- Reuse the parsed `INT-xxx` declarations from Workstream A inside compositional verification.
- Add semantic checks for:
  - `Kind: call`
  - `Kind: implements`
- For `call`:
  - resolve the declared `Source`
  - resolve the declared `Target`
  - confirm that the source implementation has a statically detectable call path or cross-module invocation to the target
- For `implements`:
  - confirm explicit or structurally substitutable interface implementation
  - reuse existing protocol/signature compatibility logic where possible
- Emit integration-specific finding types so reports distinguish:
  - missing tag coverage
  - broken semantic integration

**Requirements covered**

- REQ-012 through REQ-015

**Acceptance criteria**

- Baseline integration findings are still present in lower-level runs because they come from Level 1.
- A tagged but semantically missing call integration fails the deeper compositional stage.
- A tagged but incompatible `implements` integration fails the deeper compositional stage.
- Findings clearly separate traceability failures from semantic integration failures.

---

## Workstream C — Dead Code Analysis

**Goal:** Add static dead-code reporting with explicit user-review guidance.

**Primary files**

- [src/serenecode/ports/](/Users/helgster/Projects/serenecode/src/serenecode/ports)
- [src/serenecode/adapters/](/Users/helgster/Projects/serenecode/src/serenecode/adapters)
- [src/serenecode/core/pipeline.py](/Users/helgster/Projects/serenecode/src/serenecode/core/pipeline.py)
- [src/serenecode/models.py](/Users/helgster/Projects/serenecode/src/serenecode/models.py)
- [pyproject.toml](/Users/helgster/Projects/serenecode/pyproject.toml)

**Likely new files**

- [src/serenecode/ports/dead_code_analyzer.py](/Users/helgster/Projects/serenecode/src/serenecode/ports/dead_code_analyzer.py)
- [src/serenecode/adapters/vulture_adapter.py](/Users/helgster/Projects/serenecode/src/serenecode/adapters/vulture_adapter.py)

**Likely test files**

- [tests/integration/test_tools.py](/Users/helgster/Projects/serenecode/tests/integration/test_tools.py)
- [tests/unit/test_pipeline.py](/Users/helgster/Projects/serenecode/tests/unit/test_pipeline.py)
- new adapter-focused tests under [tests/integration/](/Users/helgster/Projects/serenecode/tests/integration)

**Planned changes**

- Add a dedicated port for dead-code analysis so the pipeline stays hexagonal.
- Add a vulture-backed adapter.
- Integrate dead-code analysis as a static pipeline step that runs without `--allow-code-execution`.
- Treat unavailable backend or crashes as visible skipped findings.
- Add a suppression mechanism for legitimate false positives.
- Normalize findings so they include:
  - symbol name
  - file
  - line
  - optional confidence/severity when available
  - a suggestion that explicitly says to ask the user whether to remove or allowlist the code
- Scope default dead-code reporting to shipped source rather than tests.

**Initial design choice**

- Run dead-code analysis alongside Level 1 structural/spec checks rather than introducing a new verification level.
- Because it is part of Level 1, dead-code review is present in every run from Level 1 through Level 6.

**Requirements covered**

- REQ-016 through REQ-020
- REQ-026
- REQ-030

**Acceptance criteria**

- Likely dead code is reported as a structured finding.
- Findings tell the coding agent to ask the user before removal.
- Backend failures show up as skipped, not silent omission.
- Those findings are visible in every `serenecode check` run from Level 1 through Level 6.

---

## Workstream D — MCP Tools and Resources

**Goal:** Expose the new integration and dead-code capabilities to AI agents through MCP without breaking existing REQ-centric flows.

**Primary files**

- [src/serenecode/mcp/tools.py](/Users/helgster/Projects/serenecode/src/serenecode/mcp/tools.py)
- [src/serenecode/mcp/server.py](/Users/helgster/Projects/serenecode/src/serenecode/mcp/server.py)
- [src/serenecode/mcp/resources.py](/Users/helgster/Projects/serenecode/src/serenecode/mcp/resources.py)
- [src/serenecode/mcp/schemas.py](/Users/helgster/Projects/serenecode/src/serenecode/mcp/schemas.py)

**Test files**

- [tests/integration/test_tools.py](/Users/helgster/Projects/serenecode/tests/integration/test_tools.py)
- [tests/integration/test_resources.py](/Users/helgster/Projects/serenecode/tests/integration/test_resources.py)
- [tests/integration/test_server.py](/Users/helgster/Projects/serenecode/tests/integration/test_server.py)

**Planned changes**

- Extend MCP spec validation behavior so `serenecode_validate_spec` understands `INT-xxx` declarations and their validation errors.
- Keep REQ-specific tools working.
- Add new MCP capabilities for integrations, preferably as additive tools:
  - `serenecode_list_integrations`
  - `serenecode_integration_status`
- Add a dead-code MCP tool, for example:
  - `serenecode_dead_code`
- Add or extend resources so agents can fetch integration metadata without a write action, for example:
  - `serenecode://integrations`
  - possibly a generalized `serenecode://spec-items`
- Ensure all responses remain JSON-friendly and structured enough for agent automation.

**Backward-compatibility rule**

- Existing `serenecode_list_reqs`, `serenecode_req_status`, and `serenecode_orphans` continue to work for REQ-only workflows.

**Requirements covered**

- REQ-015
- REQ-024 through REQ-027
- REQ-029

**Acceptance criteria**

- An MCP client can inspect integration-point status without scraping prose.
- An MCP client can retrieve dead-code findings with clear user-confirmation guidance.
- Existing REQ-only tool callers do not break.

---

## Workstream E — Templates, Root Guidance, and README

**Goal:** Make the shipped guidance consistent everywhere SereneCode teaches its workflow.

**Primary files**

- [SERENECODE.md](/Users/helgster/Projects/serenecode/SERENECODE.md)
- [README.md](/Users/helgster/Projects/serenecode/README.md)
- [src/serenecode/templates/content.py](/Users/helgster/Projects/serenecode/src/serenecode/templates/content.py)
- [src/serenecode/init.py](/Users/helgster/Projects/serenecode/src/serenecode/init.py)

**Bundled example project**

- See the SereneCode-format sample under [`examples/`](examples/) (paths vary by checkout layout).

**Test files**

- [tests/unit/test_templates_content.py](/Users/helgster/Projects/serenecode/tests/unit/test_templates_content.py)
- [tests/e2e/test_init_command.py](/Users/helgster/Projects/serenecode/tests/e2e/test_init_command.py)

**Planned changes**

- Update the shared spec-traceability section in [src/serenecode/templates/content.py](/Users/helgster/Projects/serenecode/src/serenecode/templates/content.py) so every generated `SERENECODE.md` variant inherits the new workflow.
- Update the repository root [SERENECODE.md](/Users/helgster/Projects/serenecode/SERENECODE.md) so contributors building SereneCode itself follow the same rules.
- Update [README.md](/Users/helgster/Projects/serenecode/README.md) to explain:
  - what `INT-xxx` is for
  - how it differs from `REQ-xxx`
  - how to tag implementation and tests
  - what MCP tools/resources support this workflow
  - how dead-code findings should be handled
- Update example guidance if any shipped example `SERENECODE.md` content becomes stale.

**Requirements covered**

- REQ-021 through REQ-023
- REQ-028 through REQ-030

**Acceptance criteria**

- `serenecode init` generated docs mention integration points and dead-code review in every template path that includes spec guidance.
- Root docs and README do not overclaim behavior the code does not yet implement.

---

## Workstream F — CLI and Reporting Surface

**Goal:** Ensure the CLI and report output present the new findings clearly.

**Primary files**

- [src/serenecode/cli.py](/Users/helgster/Projects/serenecode/src/serenecode/cli.py)
- [src/serenecode/reporter.py](/Users/helgster/Projects/serenecode/src/serenecode/reporter.py)
- [src/serenecode/models.py](/Users/helgster/Projects/serenecode/src/serenecode/models.py)

**Test files**

- [tests/unit/test_reporter.py](/Users/helgster/Projects/serenecode/tests/unit/test_reporter.py)
- [tests/e2e/test_check_command.py](/Users/helgster/Projects/serenecode/tests/e2e/test_check_command.py)

**Planned changes**

- Update `serenecode spec` help text and output wording so it explicitly mentions integration-point validation where applicable.
- Ensure human, JSON, and HTML reports can show:
  - integration traceability findings
  - semantic integration failures
  - dead-code advisory findings
- Make the reporting language explicit that integration traceability and dead-code review are baseline checks present in every verification run, while deeper semantic integration evidence comes from higher levels.
- Preserve current output stability for existing findings where practical.

**Requirements covered**

- REQ-011
- REQ-014
- REQ-015
- REQ-019

**Acceptance criteria**

- CLI output is understandable without reading source code.
- JSON output remains structured enough for machine consumers and MCP bridges.

---

## Implementation Order

### Phase 1 — Core traceability generalization

- Complete Workstream A first.
- Keep all changes backward compatible.
- Land tests for mixed REQ/INT parsing before touching docs.
- Ensure the pipeline wiring makes these baseline findings appear in every requested verification level.

### Phase 2 — MCP and reporting skeleton

- Extend MCP and reporting surfaces so new data can be observed early.
- Avoid shipping semantic integration logic that has no user-facing visibility.

### Phase 3 — Semantic integration checks

- Implement Workstream B after the parser and reporting surfaces exist.
- Reuse existing compositional helpers instead of creating a parallel call-resolution system.

### Phase 4 — Dead-code analysis

- Implement Workstream C after the reporting path is ready.
- Keep the first iteration intentionally conservative and advisory.

### Phase 5 — Documentation and templates

- Update shared template text, root guidance, MCP descriptions, and README after behavior is real.
- Only document user-visible capabilities that actually landed.

### Phase 6 — End-to-end verification

- Run focused tests for touched modules.
- Run repo-level structural/spec checks.
- Run broader test coverage before considering the feature complete.

---

## Verification Plan

### Fast checks after each workstream

```bash
uv run pytest tests/unit/checker/test_spec_traceability.py
uv run pytest tests/unit/checker/test_compositional.py
uv run pytest tests/integration/test_tools.py
uv run pytest tests/unit/test_templates_content.py
uv run serenecode spec SPEC.md
uv run serenecode check src/ --structural
```

### Broader verification before completion

```bash
uv run pytest
uv run serenecode check src/ --level 4 --allow-code-execution
```

If runtime is reasonable after the feature stabilizes, we should also run:

```bash
uv run serenecode check src/ --level 6 --allow-code-execution
```

---

## Open Design Calls to Keep in Mind While Implementing

These do not block the plan, but we should make them deliberately when we get there:

- whether dead-code allowlisting lives only in source comments, only in config, or in both
- whether MCP exposes integration support through new dedicated tools or through generalized spec-item tools
- whether integration parsing lives entirely in [src/serenecode/checker/spec_traceability.py](/Users/helgster/Projects/serenecode/src/serenecode/checker/spec_traceability.py) or is split into a separate pure parser module
- how strict the first `Kind: call` semantic check should be for indirect calls, aliasing, and dependency injection patterns
