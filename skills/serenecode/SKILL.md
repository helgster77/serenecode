---
name: serenecode
description: Verify Python code against a structured spec (REQ/INT IDs), mypy types, and icontract contracts using SereneCode, then fix findings level by level. Use when the user asks to verify, contract-check, or trace Python code to requirements, mentions SereneCode, SERENECODE.md, or SPEC.md with REQ-xxx IDs, or wants AI-written Python checked before merging.
---

# SereneCode verification workflow

SereneCode is an open-source (MIT) Python verifier: https://github.com/helgster77/serenecode.
This skill only tells you how to drive it. The checks themselves come from the
`serenecode` package, which must be installed in the project's environment.
The skill describes the latest release on PyPI.

## 1. Confirm the tool is available

Run `serenecode doctor`. If the command is missing, **ask the user before installing
anything**. Then suggest installing it into the environment that holds the project's
dependencies:

```bash
python -m pip install 'serenecode[mcp]'
```

If the project has no `SERENECODE.md`, offer to run `serenecode init`. Don't run it
unprompted, because it writes `SERENECODE.md`, a section in `CLAUDE.md`, and
placeholder `SPEC.md` files.

Once `SERENECODE.md` exists, read it before you write or change code. It holds the
project's conventions (preset: minimal / default / strict) and they take precedence
over this skill.

## 2. Spec first

- Requirements live in a project-root `SPEC.md` as `REQ-xxx` entries, with optional
  `INT-xxx` integration points. Validate the spec with `serenecode spec SPEC.md`.
- Tag the implementing function or class with `Implements: REQ-001` and the test
  with `Verifies: REQ-001`. Put tags in **function or class docstrings only**. A tag
  in a module docstring is ignored and reported as misplaced.

## 3. Write enforceable contracts

- Public functions that take caller-supplied inputs need `@icontract.require`.
  Public functions need `@icontract.ensure`. Classes with state need
  `@icontract.invariant`.
- Write conditions that constrain behavior, for example
  `lambda items, result: min(items) <= result <= max(items)`. Never write
  tautologies such as `lambda result: True`.
- A condition can only name parameters that the signature has. Don't write
  conditions over `*args`/`**kwargs`, and don't use `lambda result: ...` on a
  function annotated `-> None`.
- If a parameter's type already makes every value valid (a `bool`, a closed
  `Enum`), waive the precondition with `# no-precondition: <reason>` instead of
  writing a type-shaped tautology.
- Keep decorator stacks directly above their `def`.

## 4. Verify after every change

Prefer the MCP tools while editing:

- `serenecode_check_function` / `serenecode_check_file`: scoped findings for the
  code you just changed.
- `serenecode_verify_fixed`: confirm that a specific finding is gone.
- `serenecode_orphans`, `serenecode_req_status`: traceability gaps.

To register the MCP server in Claude Code, use one of these. Choose the second only if
the user agrees to let L3–L6 run their tests and generated inputs:

```bash
claude mcp add serenecode -- serenecode mcp
claude mcp add serenecode -- serenecode mcp --allow-code-execution
```

The CLI equivalents, for batch or CI runs:

```bash
serenecode check src/ --structural                              # L1, seconds
serenecode check src/ --level 4 --allow-code-execution          # + coverage, Hypothesis
serenecode check src/ --level 6 --allow-code-execution          # + CrossHair, architecture
```

Use the depth that the project's preset specifies (minimal L2, default L4, strict L6).

## 5. Fix findings by level

| Level | What failed | Fix |
|---|---|---|
| L1 structural | Missing or unenforceable contracts, annotations, misplaced tags | Add or correct the decorator, annotation, or tag. Never delete a contract to pass. |
| L2 types | mypy `--strict` error | Fix the annotation or expression, using the reported error code. |
| L3 coverage | Failing tests or per-function coverage below threshold | Fix the failing tests first, then add tests for the reported uncovered lines. |
| L4 properties | Hypothesis counterexample | Fix the implementation, or add a precondition if those inputs are genuinely invalid. |
| L5 symbolic | CrossHair counterexample | Same as L4. |
| L6 compositional | Dependency direction, cycles, interface mismatch | Fix the architecture or the module-boundary contracts. |

Rerun the check after each fix until the verdict is `complete`.

## 6. Report honestly

- Report the aggregate verdict (`complete` / `failed` / `incomplete`) and the level
  achieved. Don't report only the passing records.
- `skipped` means a check did not run, so investigate why. `exempt` items are out of
  scope or are advisories, so list them.
- Passing L4 or L5 means no counterexample was found within the bounds. It does not
  prove correctness. Don't claim more than that.
