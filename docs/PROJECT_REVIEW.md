# SereneCode project review — historical baseline and follow-up

Original review: 7 September 2026, commit `a2fb95f`, before the fixes.
The original measurements below are historical and are not current-tree counts.

**Implementation follow-up:** All seven reproduced defects below are fixed in
the working tree. Coverage preserves pytest failures and resolves report paths
against the target project. MCP validates symbol identity, retains blockers,
requires evidence for the selected target, and confirms fixes only after a
passing check. Verification levels preserve earlier gaps, scoped checks receive
project implementation context, the standard installation includes the coverage
runner, and test files no longer require production contracts or tests of tests.

Regression coverage was added in
[scope tests](../tests/integration/test_scope.py),
[CLI trust tests](../tests/e2e/test_verification_trust.py),
[context tests](../tests/integration/test_traceability_context.py),
and the pipeline tests. CI now checks a clean wheel installation with a real L3
run. Deep self-check failures now include their underlying findings.

Current measurements and commands are maintained in the dated
[verification record](VERIFICATION_STATUS.md). The original review below describes
the pre-fix checkout, including failures and installation limitations that have
since been corrected. Product proposals remain future work unless noted above.

## Original review (before the fixes)


SereneCode has a useful core idea: give coding assistants a repeatable way to connect requirements, implementation, tests, and executable contracts. The highest-value next release would make that feedback dependable, fast, and easy to adopt in an existing Python project. The reviewed implementation had several reproducible cases where the result either overstates verification or gives misleading feedback.

This review examined the README, specification, security documentation, configuration and initialization flows, CLI, MCP tools, pipeline, source discovery, result reporting, verification adapters, and representative tests. It also ran the test suite, strict mypy, the Level 2 self-check, a clean package installation, and small temporary projects exercising the public CLI and MCP functions. Production code was left unchanged during that review; the subsequent fix pass changed it.

The existing foundation is worth preserving. The ports and adapters separate orchestration from tool execution; frozen result dataclasses give the CLI and MCP a common representation; the code distinguishes failures, skips, and exemptions; and the documented execution consent model is explicit. These are useful building blocks for a reliable verification tool.

**Verified problems, ordered by practical impact**

1. **P1 — A failing test suite can produce a successful verification command.** In a temporary project, `square(2.0)` correctly returned `4.0`, while its test asserted `99.0`. Direct pytest exited 1. `serenecode check src --level 3 --allow-code-execution --format json` nevertheless exited 0 with `passed: true`. The coverage adapter accepts the generated JSON without checking the subprocess exit status when the file exists. Coverage measures which code ran; a failing assertion can still execute every line. Preserve the test outcome alongside coverage, fail the verification gate on test failures, and distinguish collection errors and execution failures from low coverage. [Coverage adapter](../src/serenecode/adapters/coverage_adapter.py). Pytest explicitly defines exit 1 as executed tests containing failures. [Pytest exit codes](https://docs.pytest.org/en/stable/reference/exit-codes.html).

2. **P1 — MCP can verify a nonexistent function and confirm an unverified fix.** Calling `tool_check_function` with `not_here` against a valid file returned `passed: true`, `verdict: complete`, and zero results. The filter removes every record but preserves the file's achieved level. Separately, `tool_verify_fixed` returned `fixed: true` for an absent function even when its nested response was incomplete. Resolve and validate a qualified symbol before execution; preserve applicable file/module blockers; and confirm a fix only when the relevant check actually ran. Stable finding identifiers would be more reliable than message substrings. [Function filter](../src/serenecode/mcp/tools.py), [fix confirmation](../src/serenecode/mcp/tools.py).

3. **P1 — Later stages can erase an earlier lack of verification evidence.** With the real adapters and a constants-only module, runs starting at L4 reported incomplete at L4 and L5 with zero findings. Increasing the requested level to L6 returned `passed: true`, `level_achieved: 6`, still with zero findings. The pipeline replaces the aggregate achieved level whenever a later stage succeeds, without retaining earlier evidence gaps. Keep explicit completion state for each stage and make the overall verdict depend on every requested stage. Decide and document how a stage with no applicable targets should be reported. [Pipeline aggregation](../src/serenecode/core/pipeline.py).

4. **P2 — Scoped checks misinterpret project-wide requirements.** A fixture with two requirements implemented in two source files passed when checking `src`. Checking the first file alone reported the second requirement as having no implementation, despite the implementation being present in the other file. Checking just the function returned incomplete while hiding the missing-implementation explanation. The scoped pipeline loads the whole spec and project tests but only the selected source file. Build traceability against a project-wide source index, then select the findings relevant to the requested scope. Retain explicit reasons when another issue blocks deeper checks. [MCP pipeline inputs](../src/serenecode/mcp/tools.py), [traceability invocation](../src/serenecode/core/pipeline.py).

5. **P2 — Coverage results depend on the caller's working directory.** A passing fixture measured 100% coverage when invoked inside its project, but 0% when invoked from this repository using the same absolute target and import roots. Pytest runs with the target project as its working directory; coverage keys are later resolved against the parent process's directory. Normalize report paths against the directory where coverage ran and carry that context with cached data. This matters directly to an MCP server checking another project. [Path matching](../src/serenecode/adapters/coverage_adapter.py).

6. **P2 — The documented installation does not supply the coverage runner.** Installing this checkout with its `mcp` extra into a fresh virtual environment installed SereneCode, MCP, and coverage, but neither pytest nor pytest-cov. An L3 run then failed with `No module named pytest`. Both required runner packages are currently confined to the `dev` extra. Supply a complete verification extra or include the runner dependencies in the default install, update the quick start, and have `doctor` verify all requested backends in the actual execution environment. Add a built-wheel smoke check that runs the documented install and a tiny L3 example. [Dependencies](../pyproject.toml), [runner invocation](../src/serenecode/adapters/coverage_adapter.py).

7. **P2 — Checking a project root asks for tests of test files.** In the same fixture, checking `src` passed, while checking the project root suggested creating `test_test_math_ops.py` for `tests/test_math_ops.py`. Test-file existence checks do not exclude test sources, although an existing helper already recognizes them. Define separate source and test scopes, apply relevant rules to each, and make the default `check .` path useful for a conventional repository. [Test existence loop](../src/serenecode/core/pipeline_helpers.py).

**Changes that would make the product more useful**

The first priority is a dependable edit loop. The per-function operation still runs the file pipeline and filters afterward. Each request still creates fresh adapters, so the coverage cache only avoids repeated test runs within that request. L3 still runs the project's test directory. Introduce a real symbol index and dependency-aware scope, persistent caches keyed by source/test/configuration changes, and explicit fast and deep check profiles. Reuse current full-suite coverage when valid; clearly label narrower test selection when using it. Run execution-based work in cancellable worker processes with wall-clock budgets. Hypothesis currently runs in-process with `deadline=None`, which leaves the interactive path without a hard timeout for a hanging target. [Tool execution](../src/serenecode/mcp/tools.py), [Hypothesis settings](../src/serenecode/adapters/hypothesis_adapter.py).

The second priority is gradual adoption. Add a read-only onboarding command that detects project layout, tests, dependencies, applicable checks, and likely verification gaps. Support a baseline and a changed-code gate so an established project can improve without addressing every existing convention violation first. Put executable policy in validated `[tool.serenecode]` TOML, with explicit rule identifiers, configurable coverage thresholds, exclusions, and per-path profiles. Generate the assistant-facing Markdown from that policy. The current Markdown parser relies on prose matching and preserves preset module-health thresholds, making supported customization hard to discover. [Configuration parsing](../src/serenecode/config.py).

The third priority is clear evidence. Report stage outcomes, unique symbols, actual executed targets, exemptions and their reasons, test outcomes, analysis bounds, and the source revision or content hashes. Keep requirement references separate from evidence that the requirement's behavior was tested. The current `total_functions` counts result records, including module checks and requirement records, so it is not a count of distinct verified functions. In the L2 self-check there were 334 records across 254 distinct file/name pairs; some of those pairs are modules or requirements. A small evidence matrix would communicate assurance better than a single maximum level. This is particularly relevant because L6 is an architectural check and does not establish semantic compatibility across contracts. [Summary construction](../src/serenecode/models.py).

The fourth priority is working well beyond the repository's own examples. Introduce a documented strategy registration hook, generic dataclass support, and configuration of generated domains. The production strategies currently include specialized handling for SereneCode's own types and `Patient`/`Drug` names in `*.core.models`. Basic integer sampling also defaults to −1000 through 1000, and floats exclude NaN and infinity. Those choices should be visible in evidence and overridable. Hypothesis already offers type-strategy registration as a foundation. [Current strategies](../src/serenecode/adapters/hypothesis_strategies.py), [specialized model handling](../src/serenecode/adapters/hypothesis_strategies.py), [Hypothesis strategy registration](https://hypothesis.readthedocs.io/en/latest/reference/strategies.html#hypothesis.strategies.register_type_strategy).

Broaden the example corpus with ordinary business logic, a small service, and an existing project adopted incrementally. Include deliberately faulty variants and measure which failures are detected, which are missed, and how long checks take. Detection rate, false positives, and useful feedback time would provide more meaningful validation than a growing number of passing self-tests. Later, add detection of weakened contracts, removed assertions, and lost requirement coverage in a diff; this directly supports the project's intended assistant workflow.

Maintainability should support those product changes. The baseline tree had 56 Python source files and 22,866 source lines, with 12 files at 800 or more lines. Its L2 self-check reported 100 module-health advisories: 72 function-length, 20 file-length, and 8 parameter-count findings. Extract cohesive responsibilities around symbol resolution, check planning, execution results, traceability indexing, and reporting. Review contracts for meaningful behavioral guarantees; many type/shape-only contracts add significant scaffolding. Promote the current feature-specific root SPEC into a product-wide behavioral specification covering verdicts, scopes, installation, and execution outcomes.

**Suggested release sequence**

| Order | Deliverable | Evidence that it is ready |
|---|---|---|
| 1 | Trust and installation fixes | All seven fixtures above have explicit, correct outcomes; the documented wheel install completes a real L3 check. |
| 2 | Reliable scoped MCP checks | A symbol check retains project context, explains blockers, and avoids an unnecessary full test run when valid cached evidence exists. |
| 3 | Adoption and policy configuration | A conventional existing project can run `check .`, establish a baseline, and gate a change using explicit configuration. |
| 4 | General strategies and measured effectiveness | Multiple independent example projects produce reproducible counterexamples, transparent exclusions, and recorded execution times. |

**Historical validation recorded during the original review**

- Python 3.13.12; SereneCode 0.5.1; mypy 1.19.1; Hypothesis 6.151.9; CrossHair 0.0.102; pytest 9.0.2; pytest-cov 7.1.0; coverage 7.13.5 in the existing environment.
- `mypy src examples/dosage-serenecode/src`: passed, 61 source files.
- `serenecode check src --level 2 --format json`: passed, 201 passing records, 133 exemptions including 100 advisories.
- Full `pytest -q`: 1,732 passed, 16 skipped, one failure, one warning, 210 seconds. The failing test was `tests/integration/test_example_projects.py::test_serenecode_repo_passes_strict_level_6`; the reported achieved level was 4. A focused rerun of that test passed in 237 seconds. The initial failure therefore did not reproduce in isolation; its cause remains unconfirmed. Improve this test's failure output to retain the individual findings, tool versions, and execution budgets so future failures can be diagnosed.
- Clean isolated installation with the `mcp` extra: package installation succeeded; pytest and pytest-cov were absent; a real L3 command failed because pytest was missing.
- Temporary fixtures reproduced the failing-test success, absent-symbol success, false fix confirmation, lost deep-stage evidence, scoped traceability failure, working-directory coverage discrepancy, and requests for tests of test files.
- A separate whole-tree CLI L6 run was not performed during the original review; the follow-up verification record includes that run. The strict self-check test starts at L4, so it is not a substitute for a complete L1–L6 CLI run.
