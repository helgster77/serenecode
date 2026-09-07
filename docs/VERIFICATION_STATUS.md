# Verification record — 7 September 2026

This records local measurements of the working tree containing the seven defect
fixes and documentation/template corrections. The base commit was
`a2fb95fb76dc1699bf5f198d021d14dfcf87a1bd`; changes were uncommitted. Package metadata still says
`0.5.1`. This is not a claim about the published PyPI artifact or remote CI.
The [machine-readable snapshot](verification-2026-09-07.json) contains the counts,
commands, timestamps, and tool versions. Refresh this record when the inputs change;
it is a dated result, not a continuously updated badge.

## Framework

```bash
.venv/bin/serenecode check src --level 6 --allow-code-execution --format json
```

The complete L1–L6 run exited **0**, achieved **L6**, and reported **complete** at
`2026-09-07T21:56:33.779249+00:00`. Wall time: **9m 37.7s** on this machine.
L3 ran the full repository test suite under coverage and accepted its successful
pytest outcome. Timing depends on the machine, dependencies, and analysis budgets.

| Result records | Count |
|---|---:|
| Total (`total_functions`) | 1,139 |
| Passed | 800 |
| Failed | 0 |
| Skipped | 0 |
| Exempt, including advisories | 339 |
| Advisory subset of exempt | 101 |

These are **records, not unique functions or pytest test counts**. A symbol may
appear at several levels; module and spec records also count. Advisories are
included in exemptions. A complete verdict includes exclusions and does not imply
that every function was exercised by every backend. Ordinary MCP `findings` omit
passing records and non-advisory exemptions; CLI JSON retains them.

`pytest --collect-only -q` collected **1770 tests**. Collection is not a
pass count. The CLI does not retain pytest's successful pass/skip breakdown in its
JSON, so this record does not invent one. The strict self-check integration test
starts at L4 and covers only L4–L6; the command above separately exercises L1–L6
using the repository's default configuration.

## Shipped examples

| Check | Result |
|---|---|
| `pytest -q` in `examples/dosage-regular` | 59 passed |
| `pytest -q` in `examples/dosage-serenecode` | 67 passed |
| `serenecode check src --level 6 --allow-code-execution --format json` in `examples/dosage-serenecode` | Exit 0, complete, L6; 47 passed records, 0 failed, 0 skipped, 7 exempt including 4 advisories |
| AST count of icontract decorators in the SereneCode example's source | 42: 10 preconditions, 13 postconditions, 19 invariants |

The example's final L6 run was recorded at `2026-09-07T21:49:45.037184+00:00` and took
13.0s. Only `adjust_for_renal_function` passed L5;
`calculate_dose`, `check_daily_safety`, and `check_contraindications` were exempt
from symbolic analysis. Those functions did pass L3 and L4. The written renal
contracts do not specify every tier multiplier. None of these results establishes
clinical validity or exact decimal arithmetic.

## Other validation

- `mypy src examples/dosage-serenecode/src`: passed on **63 source files**.
- Template/config/init, symbolic-classification, and Hypothesis adapter tests:
  **157 passed**.
- Final diagnostic/documentation wording corrections: **55 reporter/property
  tests passed** after the full run; verification logic was unchanged.
- Fresh wheel installation with `[mcp]`: the real L3 smoke check passed, pytest
  and pytest-cov were installed, and the wheel contained `serenecode/py.typed`.
- All three generated convention documents parsed back to their intended preset,
  including every exemption and module-health threshold.
- Checked-in example `SERENECODE.md` and `CLAUDE.md` matched their generators.
- The arithmetic teaching examples passed bounded property and boundary checks.
- Root spec validation passed with **36 requirements and 4 integration points**;
  example spec validation passed with **25 requirements**. These count valid
  references/format, not proven requirements.

The focused test command was:

```bash
.venv/bin/pytest -q tests/unit/test_templates_content.py tests/unit/test_config.py \
  tests/e2e/test_init.py tests/e2e/test_init_command.py \
  tests/unit/checker/test_symbolic.py tests/integration/test_hypothesis_adapter.py
```

The wheel check used `uv build --wheel`, a new virtual environment, installation
of that wheel with `[mcp]`, and `tests/smoke/check_installed_wheel.py`. The clean
wheel environment resolved dependencies independently of the repository lockfile.

## Environment and interpretation

Python **3.13.12**, Darwin arm64.
The JSON snapshot records installed backend versions. CI is configured to check
Python 3.10 and 3.13, but this record describes local Python 3.13 evidence only.

Historical measurements in [PROJECT_REVIEW.md](PROJECT_REVIEW.md) describe the
pre-fix checkout and remain labeled historical. Product proposals there are not
claims of implemented features. See [VERIFICATION_LEVELS.md](VERIFICATION_LEVELS.md)
for sampling bounds, configuration behavior, scope, and the meaning of completion.
