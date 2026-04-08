# Verification levels — setup and semantics

**How to run checks:** For interactive editing with an AI assistant, prefer the **MCP tools** (`serenecode_check_function`, `serenecode_check_file`) scoped to what you just changed. Use the **`serenecode check` CLI** for CI, release gates, and full-tree batch runs. Run **`serenecode doctor`** to confirm the optional MCP install and IDE registration. CLI output ends with a one-line reminder pointing at MCP for per-symbol follow-up.

SereneCode checks stack from fast structural rules (Level 1) through types, coverage, property tests, symbolic search, and compositional analysis. Higher levels depend on how you lay out packages and how you write `SPEC.md` integrations. This page is a short “before you invest in Level 4 or 6” checklist so later stages do not surprise you.

## Level 4 (Hypothesis / property tests)

- **Domain model sampling:** Tailored Hypothesis strategies for “example-style” frozen dataclass models apply when the class’s `__module__` is `serenecode.models`, `core.models`, or **any** dotted path ending in **`.core.models`** (for example `myproject.core.models`). Other layouts still work if generic constructor sampling succeeds, or you narrow contracts, or you supply custom strategies.
- **“Skipped” / precondition messages:** If property testing reports that inputs could not be generated or preconditions filter almost everything, read the finding text: it may point at module layout vs built-in strategies, not only at “too strict” contracts.

## Level 6 (compositional) — `INT-xxx` with `Kind: call`

- **What “call” means:** The checker looks at the **body** of the tagged function or class (the symbol that carries `Implements: INT-xxx`). It looks for:
  - a **call** whose callee matches the `Target` string (simple name, dotted name, or suffix match on the last segment), or
  - an **`isinstance(..., Type)`** where the type expression matches that same target (including `pkg.sub.Type` and tuple-of-types forms).
- **Comma-separated targets:** If `Target` contains commas, **every** listed target must appear (logical **AND**). Prefer one integration per boundary if that keeps the spec easier to read.
- **Markdown in fields:** `Source`, `Target`, and related INT lines may use backticks; the parser strips them so `` `module.Type` `` matches `module.Type`.

## Spec ergonomics

- **Narrative vs traceability:** PRDs, `README` sections, and `*_SPEC.md` files are inputs. REQ/INT traceability and `serenecode check --spec` apply only to project-root **SPEC.md**, which must include a `**Source:** …` line (see SERENECODE.md). Run `serenecode doctor` to see whether SPEC.md and narrative-looking files were detected at the project root.
- Use **one primary target per comma segment**; avoid stuffing unrelated names into a single `Target` line unless you intend AND semantics.
- Align **dotted names** with how types appear in code (`from pkg import X as Y` is easier to reason about when `Target` uses the same simple name the implementation calls).

## Related reading

- [SECURITY.md](SECURITY.md) — trust model for `--allow-code-execution` (required for Levels 3–6 as implemented today).
- Project `SERENECODE.md` — conventions the structural checker enforces.
