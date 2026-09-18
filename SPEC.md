# Module Health Checks — Specification

**Purpose:** Extend SereneCode with structural checks that detect overgrown files, functions, classes, and parameter lists — common AI coding agent failure modes — and provide actionable refactoring guidance.

**Source:** Implementation plan derived from codebase exploration (2026-04-13).

**Scope:** This specification covers the module-health feature (REQ-001–REQ-036, INT-001–INT-004) and the contract-enforcement and tooling corrections of REQ-037–REQ-047 and INT-005, not the entire SereneCode product. References and presentation descriptions were reconciled with the implementation on 7 September 2026. Product behavior and verification limits are documented in [README.md](README.md) and [verification semantics](docs/VERIFICATION_LEVELS.md). Tags establish traceability, not proof of every acceptance criterion.

---

## Configuration

### REQ-001: ModuleHealthConfig dataclass

A `ModuleHealthConfig` frozen dataclass with the following fields. Class invariants check positive thresholds and warn/error ordering while contracts are enabled; annotations specify field types:

- `enabled`: bool. When False, all module health checks are skipped.
- `file_length_warn`: int, lines above which a file-length advisory is emitted. Must be > 0.
- `file_length_error`: int, lines above which a file-length error is emitted. Must be > `file_length_warn`.
- `function_length_warn`: int, function-span lines above which a function-length advisory is emitted. Must be > 0.
- `function_length_error`: int, function-span lines above which a function-length error is emitted. Must be > `function_length_warn`.
- `parameter_count_warn`: int, non-receiver parameters above which an advisory is emitted. Must be > 0.
- `parameter_count_error`: int, non-receiver parameters above which an error is emitted. Must be > `parameter_count_warn`.
- `class_method_count_warn`: int, methods above which a class-size advisory is emitted. Must be > 0.
- `class_method_count_error`: int, methods above which a class-size error is emitted. Must be > `class_method_count_warn`.

### REQ-002: ModuleHealthConfig added to SerenecodeConfig

`SerenecodeConfig` gains a `module_health: ModuleHealthConfig` field. All existing composition roots (`default_config`, `strict_config`, `minimal_config`, `_apply_content_overrides`) must propagate this field.

### REQ-003: Template-specific default thresholds

Each template preset provides different thresholds:

| Metric                | Default (warn / error) | Strict (warn / error) | Minimal (warn / error) |
|-----------------------|------------------------|-----------------------|------------------------|
| File length (lines)   | 500 / 1000             | 400 / 700             | 750 / 1500             |
| Function length (lines) | 50 / 100             | 30 / 60               | 75 / 150               |
| Parameter count       | 5 / 8                  | 4 / 6                 | 7 / 10                 |
| Class method count    | 15 / 25                | 10 / 18               | 20 / 35                |

---

## Advisory Generalization

### REQ-004: ADVISORY_FINDING_TYPES constant

A module-level `frozenset[str]` in `models.py` enumerating all finding types that use the advisory pattern (EXEMPT status, visible in output, do not block verification unless `--fail-on-advisory`). Initial members: `"dead_code"`, `"file_length"`, `"function_length"`, `"parameter_count"`, `"class_method_count"`.

### REQ-005: Generalized advisory counting in make_check_result

`make_check_result()` must count advisory results by checking `detail.finding_type in ADVISORY_FINDING_TYPES` instead of hardcoding `"dead_code"`. The `advisory_count` field in `CheckSummary` must reflect all advisory types.

### REQ-006: Generalized advisory display in reporter

The human and HTML reporters must classify advisory results using `ADVISORY_FINDING_TYPES` membership instead of hardcoding `"dead_code"`. The summary label must read `"advisory"` (not `"advisory (dead code)"`).

### REQ-007: Generalized advisory inclusion in MCP wire format

The `to_check_response` projection in `schemas.py` must include EXEMPT results with any `finding_type in ADVISORY_FINDING_TYPES` in the wire findings, not only `"dead_code"`.

---

## File Length Check

### REQ-008: File length check counts total lines

`check_file_length` counts lines as `len(source.splitlines())` for each source file. Test files (identified by `_is_test_file_path`) are excluded.

### REQ-009: File length error when exceeding error threshold

When line count exceeds `config.module_health.file_length_error`, a `FunctionResult` with `status=FAILED`, `function="<module>"`, `line=1`, `finding_type="file_length"` is emitted. The message must include the actual line count and the threshold.

### REQ-010: File length advisory when exceeding warn threshold

When line count exceeds `config.module_health.file_length_warn` but not the error threshold, a `FunctionResult` with `status=EXEMPT`, `finding_type="file_length"` is emitted (advisory pattern). The message must include the actual line count and both thresholds.

### REQ-011: File length check runs on exempt modules

Unlike structural policy checks, file length runs on all source files including modules exempt from contract/type checks. This is because exempt modules (adapters, CLI, MCP tools) are often the largest files.

### REQ-012: File length suggestions are agent-actionable

The `suggestion` field for file-length findings must include concrete refactoring strategies: extracting classes into standalone modules, grouping related functions by shared prefix or domain concept, and identifying comment-banner section boundaries.

---

## Function Length Check

### REQ-013: Function length measured by line span

`check_function_length` measures each function's length as `node.end_lineno - node.lineno + 1` using AST `end_lineno`. Both `FunctionDef` and `AsyncFunctionDef` at module level and as class methods are checked.

### REQ-014: Function length error when exceeding error threshold

When function length exceeds `config.module_health.function_length_error`, a `FunctionResult` with `status=FAILED`, `function=node.name`, `finding_type="function_length"` is emitted.

### REQ-015: Function length advisory when exceeding warn threshold

When function length exceeds `config.module_health.function_length_warn` but not the error threshold, an advisory `FunctionResult` with `status=EXEMPT`, `finding_type="function_length"` is emitted.

### REQ-016: Function length suggestions reference extraction patterns

The suggestion must mention: extracting comment-delimited sections, pulling nested loops/conditionals into helpers, and converting setup/teardown into context managers.

---

## Parameter Count Check

### REQ-017: Parameter count excludes self and cls

`check_parameter_count` counts non-receiver parameters (excluding `self`/`cls`) for each function. Both positional, keyword-only, `*args`, and `**kwargs` are counted.

### REQ-018: Parameter count error when exceeding error threshold

When parameter count exceeds `config.module_health.parameter_count_error`, a `FunctionResult` with `status=FAILED`, `finding_type="parameter_count"` is emitted.

### REQ-019: Parameter count advisory when exceeding warn threshold

When parameter count exceeds `config.module_health.parameter_count_warn` but not the error threshold, an advisory `FunctionResult` is emitted.

### REQ-020: Parameter count suggestions reference Parameter Object pattern

The suggestion must mention grouping related parameters into a dataclass, TypedDict, or config object using the Parameter Object pattern.

---

## Class Method Count Check

### REQ-021: Class method count includes all def nodes in class body

`check_class_method_count` counts direct `FunctionDef` and `AsyncFunctionDef` children of each top-level `ClassDef` (not nested classes).

### REQ-022: Class method count error when exceeding error threshold

When method count exceeds `config.module_health.class_method_count_error`, a `FunctionResult` with `status=FAILED`, `function=class_name`, `finding_type="class_method_count"` is emitted.

### REQ-023: Class method count advisory when exceeding warn threshold

When method count exceeds `config.module_health.class_method_count_warn` but not the error threshold, an advisory `FunctionResult` is emitted.

### REQ-024: Class method count suggestions reference SRP extraction

The suggestion must mention: extracting cohesive groups of methods sharing a prefix, methods accessing a subset of attributes, and methods that could be standalone functions.

---

## Split Suggestions

### REQ-025: AST-based split point identification

A helper `suggest_split_points` analyzes a file's AST and source to identify natural module boundaries:

- Top-level classes with their line span and method count.
- Groups of top-level functions sharing a common prefix (e.g., `parse_header`, `parse_body` -> `parse_*`).
- Banner comments (lines matching patterns like `# --- Section ---` or `# ====`) that suggest logical boundaries.

### REQ-026: Split suggestions included in file-length findings

When file-length advisory or error findings are emitted, the suggestion field must include the output of `suggest_split_points` formatted as concrete split candidates in the suggestion text, using line locations where available.

### REQ-027: Graceful fallback when no split points found

If `suggest_split_points` identifies no clear boundaries, the file-length finding falls back to the generic refactoring suggestion without split points.

---

## Pipeline Integration

### REQ-028: Module health checks run in Level 1 pipeline block

All four checks (`check_file_length`, `check_function_length`, `check_parameter_count`, `check_class_method_count`) are called within the Level 1 block of `run_pipeline`, after dead-code analysis. They are guarded by `config.module_health.enabled`.

### REQ-029: Module health checks apply at all verification levels

Since the checks are part of Level 1 and Level 1 runs for all levels 1-6 (unless `--verify` skips it), module health checks run by default at every verification level.

### REQ-030: Module health check results participate in early termination

If any module health check produces a FAILED result and `early_termination=True`, the pipeline stops at Level 1 (consistent with other Level 1 failures).

---

## CLI

### REQ-031: --skip-module-health flag

The `serenecode check` command accepts a `--skip-module-health` boolean flag. When set, `config.module_health.enabled` is overridden to `False` via `dataclasses.replace` before the pipeline runs.

### REQ-032: --fail-on-advisory applies to module health advisories

The existing `--fail-on-advisory` flag must trigger exit code 11 for any advisory, including module health warnings. The help text and exit message must not hardcode "dead-code."

---

## MCP Tool

### REQ-033: serenecode_module_health tool returns file metrics

A new MCP tool `tool_module_health(path: str)` reads a single Python file and returns a dict containing:

- `file`: the file path.
- `metrics`: `line_count`, `function_count`, `class_count`, `largest_function` (name, lines, line), `max_parameters` (name, count, line), `largest_class` (name, method_count, line).
- `status`: per-metric status (`"ok"`, `"warning"`, `"error"`) derived from `ModuleHealthConfig` thresholds.
- `split_suggestions`: output of `suggest_split_points`.

### REQ-034: serenecode_module_health does not run the verification pipeline

The tool parses the AST and computes metrics directly, without calling `run_pipeline`. It does not require `--allow-code-execution`. It loads config via `_load_config` for threshold comparison.

### REQ-035: serenecode_module_health registered in MCP server

The tool is registered in `build_server()` with a description emphasizing proactive use during editing to monitor module structure.

---

## Templates

### REQ-036: Module health documented in all templates

Each template in `content.py` (default, strict, minimal) includes a "Module Health" section documenting the four metrics, their warn/error thresholds, the advisory/error behavior, and the `--skip-module-health` flag.

---

---

## Contract Binding Validation

icontract binds a condition's parameters by name at call time, against the
decorated function's signature. A condition naming something the signature
cannot supply is therefore not a contract at all: it is silently evaluated
against the wrong value, or it raises `TypeError` the first time it runs.
Neither is visible at import time, so Level 1 resolves the binding statically.

### REQ-037: check_contract_bindings resolves conditions against the signature

A `check_contract_bindings(tree, aliases, file_path)` check in
`checker/contract_binding.py` inspects every `require`/`ensure` decorator whose
first argument is a lambda, and resolves each of that lambda's mandatory
parameter names against the decorated function's signature. It runs on every
function `_iter_checked_functions` yields, including private helpers and
properties: a contract that exists and cannot be enforced is a defect
independent of the function's visibility. Findings are reported as
`FunctionResult` with `status=FAILED` and `finding_type="violation"`.

A mandatory parameter is one without a default. Condition parameters carrying
a default are excluded, because icontract leaves them at their default and
that is the established idiom for capturing a constant inside a condition.
`_ARGS` and `_KWARGS` are always bound by icontract; `result` and `OLD` are
bound in postconditions only.

### REQ-038: condition over a VAR_POSITIONAL parameter is a violation

When a condition parameter resolves to the decorated function's `*args`
parameter, a FAILED finding is emitted. icontract never binds the variadic
tuple: the name receives the first extra positional argument, and the
condition raises `TypeError` when no extra argument is passed. The message
must state this, and the suggestion must name icontract's `_ARGS` placeholder
as the supported way to constrain variadic arguments.

### REQ-039: condition over a VAR_KEYWORD parameter is a violation

When a condition parameter resolves to the decorated function's `**kwargs`
parameter, a FAILED finding is emitted. icontract binds the keyword mapping to
`_KWARGS` only, so the condition raises `TypeError` on every call. The
suggestion must name `_KWARGS`.

### REQ-040: condition parameter absent from the signature is a violation

When a mandatory condition parameter matches no signature parameter and is not
an icontract-injected name, a FAILED finding is emitted: icontract raises
`TypeError` when the condition runs. The suggestion must mention both a
misspelling and a decorator stack separated from its `def`, because an edit
that inserts a function between a decorator stack and its `def` produces
exactly this finding.

### REQ-041: postcondition using `result` on a `-> None` function is a violation

When a function's return annotation is `None` and an `ensure` condition
references `result` anywhere other than as an operand of a comparison against
`None`, a FAILED finding is emitted. icontract binds `result` to `None`, so
the condition raises `TypeError` when it runs. `result is None` and
`result == None` guards are accepted.

### REQ-042: binding check runs inside the Level 1 structural block

`_run_all_structural_checks` calls `check_contract_bindings` alongside
`check_contracts`, inside the non-test-file branch, so binding findings appear
at every verification level that runs Level 1 and participate in early
termination like any other Level 1 failure.

---

## CLI and Output Corrections

### REQ-043: --format json writes only the JSON document to stdout

With `--format json`, stdout carries the JSON document and nothing else:
progress lines, the wall-time trailer, and every diagnostic go to stderr, so
that `serenecode check ... --format json` can be piped straight into a JSON
parser.

### REQ-044: serenecode init runs unattended

`serenecode init` accepts `--level {minimal,default,strict}`,
`--spec {existing,generate}`, `--mcp/--no-mcp`, and `--yes`. A prompt is
skipped when its flag is supplied; `--yes` answers every remaining prompt with
its recommended default. When no prompt remains, `init` completes without
reading stdin. Overwriting an existing `SERENECODE.md` or `CLAUDE.md` requires
`--yes`: without it and with no interactive session to ask, the existing file
is left in place and the reason is printed.

### REQ-045: dead-code advisories are suppressed for unreachable-by-name definitions

A dead-code advisory for a `function` or `method` symbol is suppressed when
the definition carries a decorator that hands it to something else — any
decorator outside a small inert allowlist such as `property`, `staticmethod`,
`abstractmethod` and the icontract decorators — or when the definition
overrides a method declared by a base class resolvable within the scanned
source set, or carries `@override`. Suppression is keyed on
`(file path, symbol name, line)` for every line a backend might attribute to
the definition: the `def` line and each decorator line. Advisories for
variables, attributes, imports, and classes are unaffected.

### REQ-046: exempt summary names its advisory subset

The human summary renders the advisory count inside the exempt figure —
`57 exempt (33 advisory)` — because advisories are a subset of the exempt
count rather than a separate bucket. When no advisories are present the
exempt figure is rendered alone.

### REQ-047: dead-code findings use the caller's path spelling

Vulture absolutizes every path it is given, so its findings must be mapped
back to the matching `SourceFile.file_path` before being reported. A single
report therefore spells each file one way, whether the finding came from the
structural checker or the dead-code backend. Matching is textual — a core
module cannot touch the filesystem — and a path that matches nothing is
reported unchanged.

## INT-005: Contract binding check integration

Kind: call
Source: _run_all_structural_checks
Target: check_contract_bindings

**Components:** `_run_all_structural_checks` (structural.py),
`check_contract_bindings` / `check_function_contract_bindings`
(contract_binding.py), `resolve_icontract_aliases` / `_iter_checked_functions`
(structural_helpers.py)

**Flow:**
1. `check_structural` resolves icontract aliases for the module.
2. `_run_all_structural_checks` runs `check_contracts` for contract presence.
3. It then runs `check_contract_bindings` over the same tree.
4. `check_contract_bindings` iterates checkable functions and delegates each to
   `check_function_contract_bindings`, which classifies every mandatory
   condition parameter against the signature.
5. Functions with at least one finding are appended as FAILED results.

**Contracts at boundary:** `check_contract_bindings` requires an `ast.Module`
and resolved `IcontractNames`, and ensures a list of `FunctionResult`.

**Invariant:** A function with no contracts, or with contracts whose every
mandatory parameter binds to a by-name signature parameter, produces no
result — the check adds findings, never passing records.

## INT-001: Pipeline integration flow

Kind: call
Source: _run_module_health_checks
Target: check_file_length

**Components:** `run_pipeline` / `_run_module_health_checks` (pipeline.py), `check_file_length`, `check_function_length`, `check_parameter_count`, `check_class_method_count` (module_health.py), `ModuleHealthConfig` (config.py)

**Flow:**
1. `run_pipeline` enters Level 1 block.
2. After structural checks + dead-code analysis, checks `config.module_health.enabled`.
3. If enabled, delegates to `_run_module_health_checks`, which calls all four `check_*` functions, passing `source_files` and `config`.
4. Each function iterates source files, skips test files, parses AST as needed, applies thresholds.
5. Results (FAILED or EXEMPT advisory) are appended to `level_1_results`.
6. Early termination triggers if any FAILED result exists.

**Contracts at boundary:** Each check function has icontract preconditions on inputs and postconditions ensuring returned list contains only valid `FunctionResult` objects.

## INT-002: Advisory result propagation

Kind: call
Source: make_check_result
Target: result_is_advisory

**Components:** `make_check_result` / `result_is_advisory` (models.py), `format_human` / `format_html` (reporter.py), `to_check_response` (schemas.py), `ADVISORY_FINDING_TYPES` (models.py)

**Flow:**
1. Module health checks emit `FunctionResult` with `status=EXEMPT` and `finding_type in ADVISORY_FINDING_TYPES`.
2. `make_check_result` counts these as `advisory_count` by calling `result_is_advisory`, which applies the shared `ADVISORY_FINDING_TYPES` set.
3. Reporter classifies them as advisory (visible, distinct from plain exempt) via same set.
4. MCP schemas include them in wire findings via same set.
5. CLI `--fail-on-advisory` triggers exit 11 when `advisory_count > 0`.

**Invariant:** `advisory_count` is always consistent across all consumers because classification derives from the single `ADVISORY_FINDING_TYPES` constant.

## INT-003: CLI config override for --skip-module-health

Kind: call
Source: check
Target: run_pipeline

**Components:** `check` (cli.py), `SerenecodeConfig` (config.py), `ModuleHealthConfig` (config.py), `run_pipeline` (pipeline.py)

**Flow:**
1. CLI parses `--skip-module-health` flag.
2. If set, uses `dataclasses.replace` to create a new `SerenecodeConfig` with `module_health.enabled=False`.
3. Modified config is passed to `run_pipeline`.
4. Pipeline checks `config.module_health.enabled` and skips all four check functions.

**Postcondition:** When `--skip-module-health` is set, no `FunctionResult` with `finding_type in {"file_length", "function_length", "parameter_count", "class_method_count"}` appears in the output.

## INT-004: MCP module_health tool standalone analysis

Kind: call
Source: tool_module_health
Target: _load_config

**Components:** `tool_module_health` (tools.py), `_load_config` (tools.py), `suggest_split_points` (core/module_health.py), `ModuleHealthConfig` (config.py)

**Flow:**
1. Tool receives file path, reads source via `LocalFileReader`.
2. Loads config via `_load_config` (cached, mtime-aware).
3. Parses AST, computes metrics (line count, function sizes, parameter counts, class sizes).
4. Compares each metric against `ModuleHealthConfig` thresholds to derive status.
5. Calls `suggest_split_points(source)` for split candidates when the file exceeds the warning threshold.
6. Returns structured dict with metrics, status, and suggestions.

**Postcondition:** Response always contains all metric fields even when file is empty or has no functions/classes (values are 0 / empty).
