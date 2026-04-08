# SereneCode Feature Spec — Integration Traceability and Dead Code Review

**Purpose:** Extend SereneCode so it can detect integration gaps that AI coding agents often miss, and so it can surface likely dead code for explicit user review instead of letting it silently accumulate.

This spec intentionally uses the current `REQ-xxx` format so it can be validated by today's SereneCode tooling. The feature described here adds a second traceability namespace, `INT-xxx`, for integration points.

---

## Integration Point Specification

### REQ-001: Existing REQ-only specs remain valid

SereneCode must continue to accept and validate existing specs that contain only `REQ-xxx` entries. Projects that do not use integration-point tracking must not be forced to add `INT-xxx` entries, new metadata fields, or new code/test tags.

### REQ-002: Specs may declare integration-point identifiers

A spec may declare integration points using identifiers in the form `INT-001`, `INT-002`, and so on. `INT-xxx` identifiers represent expected interactions between components, modules, classes, or functions that are important enough to track explicitly.

### REQ-003: Every integration point has a structured declaration

Each `INT-xxx` entry in the spec must include:

- A short heading description.
- A `Kind` field.
- A `Source` field naming the initiating component, class, or function.
- A `Target` field naming the depended-on component, class, or function.

If any of these fields are missing, the integration point is invalid and must be reported as a spec-level failure.

### REQ-004: Integration identifiers are unique and sequential

Within a spec, `INT-xxx` identifiers must be unique and sequential with no numbering gaps, using the same zero-padded numbering style already used for `REQ-xxx` identifiers.

### REQ-005: Supported integration kinds are explicit and finite

The initial supported integration kinds are:

- `call`: one implementation element must call or invoke another.
- `implements`: a class must implement or remain substitutable for an interface or protocol.

If a spec declares an unsupported `Kind`, SereneCode must report that integration point as invalid rather than silently ignoring it.

### REQ-006: Integration points may reference behavioral requirements

An `INT-xxx` entry may include a `Supports` field listing one or more `REQ-xxx` identifiers. This expresses that the integration point exists in service of those behavioral requirements. Invalid or orphaned `REQ-xxx` references in `Supports` must be reported.

---

## Integration Traceability

### REQ-007: Implementation tags may reference both REQ and INT identifiers

Code docstrings may reference both behavioral requirements and integration points in the existing `Implements:` tag. A single implementation element may implement multiple `REQ-xxx` identifiers, multiple `INT-xxx` identifiers, or a mixture of both.

### REQ-008: Test tags may reference both REQ and INT identifiers

Test docstrings may reference both behavioral requirements and integration points in the existing `Verifies:` tag. A single test may verify multiple `REQ-xxx` identifiers, multiple `INT-xxx` identifiers, or a mixture of both.

### REQ-009: Every integration point must be implemented and tested

For every `INT-xxx` entry in the spec, SereneCode must verify that at least one implementation location references it through `Implements:` and at least one test location references it through `Verifies:`.

### REQ-010: Orphan integration references are failures

If code or tests reference an `INT-xxx` identifier that is not declared in the spec, SereneCode must report an orphan-reference failure and point to the first detected location.

### REQ-011: Traceability output distinguishes integration coverage states

When reporting integration status, SereneCode must distinguish at least these states:

- implemented and tested
- implemented but untested
- tested but not implemented
- neither implemented nor tested
- orphan reference

The output must identify the relevant integration identifier and at least one concrete code or test location for the finding.

---

## Semantic Integration Verification

### REQ-012: Call integrations are checked semantically

For an integration point whose `Kind` is `call`, SereneCode must do more than validate tags. It must analyze the implementation locations referenced by `Implements:` and fail the integration if no statically detectable call, invocation, or resolved cross-module reference from the declared `Source` to the declared `Target` exists.

### REQ-013: Interface integrations are checked semantically

For an integration point whose `Kind` is `implements`, SereneCode must verify that the declared implementing class explicitly implements, inherits from, or remains substitutable for the declared target interface or protocol. Signature incompatibilities must be reported as integration failures.

### REQ-014: Missing tags and broken semantics are reported separately

SereneCode must distinguish between:

- a traceability failure, where an `INT-xxx` entry is missing `Implements:` or `Verifies:` references, and
- a semantic integration failure, where the references exist but the declared integration is not actually present or is incompatible.

The output must make this distinction explicit so the user and coding agent can tell whether the problem is documentation drift or an actual implementation bug.

### REQ-015: Integration status is available through reporting and MCP surfaces

The human-readable report, JSON output, and relevant MCP status tools must surface integration-point findings and locations in the same way they already surface requirement traceability findings, so an AI coding agent can inspect and act on them without parsing free-form prose.

---

## Dead Code Review

### REQ-016: SereneCode reports likely dead code in project source

SereneCode must analyze project source for likely unused code, including unused functions, classes, methods, variables, or imports when the underlying analyzer can identify them. Findings must identify the symbol, file, and line number.

### REQ-017: Dead code findings require explicit disposition

When SereneCode reports likely dead code, the finding message or suggestion must instruct the coding agent to ask the user whether the code should be removed or explicitly allowlisted. SereneCode must not present likely dead code as something the agent should automatically delete without user confirmation.

### REQ-018: Dead code findings are suppressible with explicit allowlisting

SereneCode must support an explicit suppression mechanism for legitimate false positives or framework-required entry points. Once a symbol is allowlisted, future runs must not continue to report it as dead code unless the allowlist entry is removed.

### REQ-019: Dead code analysis failures are visible

If dead code analysis cannot run because its backend is unavailable, misconfigured, or crashes, SereneCode must report that state as a visible skipped finding instead of silently omitting dead code analysis.

### REQ-020: Dead code analysis does not silently examine tests as product dead code

By default, dead code findings intended to drive removal decisions must apply to shipped project source rather than test-only helpers. If test files are analyzed at all, their findings must be clearly distinguished from product-code findings.

---

## Cross-Level Applicability

### REQ-021: Integration traceability applies to every verification level

Integration-point validation and traceability must be part of every SereneCode verification run, regardless of whether the requested maximum verification level is 1, 2, 3, 4, 5, or 6. A request for a deeper level must not skip or suppress integration-point findings that would have been reported in a shallower run.

### REQ-022: Dead-code review applies to every verification level

Dead-code analysis and its user-review guidance must be part of every SereneCode verification run, regardless of whether the requested maximum verification level is 1, 2, 3, 4, 5, or 6. A request for a deeper level must not skip or suppress dead-code findings that would have been reported in a shallower run.

### REQ-023: Higher levels may strengthen evidence but not remove baseline checks

Higher verification levels may add stronger evidence for declared integrations, including deeper compositional checks at higher levels, but the baseline integration-traceability and dead-code checks must remain present at all levels. User-facing reporting must make it clear which findings are baseline structural/static findings and which findings come from deeper verification.

---

## Generated Guidance and Documentation

### REQ-024: Every generated SERENECODE.md variant documents integration points

Every generated `SERENECODE.md` variant that includes spec traceability guidance, including the `default`, `strict`, and `minimal` templates, must explain:

- that specs may include `INT-xxx` integration-point entries,
- how to write valid `INT-xxx` entries,
- how to reference `INT-xxx` identifiers from `Implements:` and `Verifies:` tags, and
- that integration points must be both traceable and semantically satisfied.

### REQ-025: Every generated SERENECODE.md variant documents dead-code review workflow

Every generated `SERENECODE.md` variant must instruct coding agents and human contributors that likely dead code findings are advisory review items. The guidance must say that the coding agent should ask the user whether the code should be removed or allowlisted before deleting it.

### REQ-026: Shipped SERENECODE guidance stays aligned with behavior

The repository's own `SERENECODE.md`, the template generator, and any shipped example `SERENECODE.md` content must remain aligned with the actual feature behavior, terminology, and workflow for integration traceability and dead-code review. The documentation must not claim support that the implementation does not provide.

---

## MCP Tooling

### REQ-027: MCP spec-validation surfaces understand integration points

The MCP surface for spec validation must recognize and validate both `REQ-xxx` and `INT-xxx` declarations. Integration-point validation failures must be reported as structured findings rather than embedded only in prose.

### REQ-028: MCP exposes structured integration traceability status

The MCP tool surface must let an AI coding agent retrieve structured integration-point status, including:

- declared integration identifiers,
- implementation locations,
- verification locations, and
- derived status such as complete, implemented-only, tested-only, missing, or orphaned.

This may be provided by extending existing spec-traceability tools or by adding dedicated integration-oriented tools, but the capability must be present.

### REQ-029: MCP exposes structured dead-code findings

The MCP tool surface must let an AI coding agent retrieve likely dead-code findings as structured data with symbol name, file, line, confidence or severity information when available, and guidance that user confirmation is required before removal.

### REQ-030: Existing REQ-focused MCP workflows remain backward compatible

Existing REQ-focused MCP workflows must continue to function for projects that do not use integration points. Any extensions for `INT-xxx` support must preserve backward compatibility for current tool callers or provide a clearly documented migration path.

---

## README and User-Facing Documentation

### REQ-031: README documents the integration-point workflow

`README.md` must document the purpose of `INT-xxx` integration points, how they differ from `REQ-xxx` behavioral requirements, how they are tagged from implementation and tests, and what kinds of failures SereneCode reports when an integration is missing, orphaned, or semantically broken.

### REQ-032: README documents MCP support for integrations and dead code

`README.md` must describe the MCP support for integration traceability and dead-code review, including the relevant tool names or tool categories an AI coding agent should use during authoring and verification.

### REQ-033: README documents dead-code review as a user decision

`README.md` must explain that likely dead code is reported for review, that false positives are possible, that allowlisting is supported, and that the coding agent should ask the user before removing suspected dead code.

---

## Example Integration Entry Format

The intended `INT-xxx` format is illustrated here for clarity:

```markdown
### INT-001: Checkout submits payment through the payment gateway
Kind: call
Source: CheckoutService.checkout
Target: PaymentGateway.charge
Supports: REQ-003, REQ-004
```

This example is explanatory prose, not a `REQ-xxx` requirement. The behavior SereneCode must implement is defined by the requirements above.
