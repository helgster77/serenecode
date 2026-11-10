# Security and trust model

Serenecode is a **local developer tool**. It reads files you point it at and, when you opt in, runs code from those projects on your machine. It does not implement a sandbox.

## Trust boundaries

| Mode | What happens |
|------|----------------|
| **Levels 1–2** (no `--allow-code-execution`) | Parses and type-checks source; does not import project modules for execution-based checks. |
| **Levels 3–6** or MCP with `--allow-code-execution` | Imports project modules **in-process** (`compile` / `exec`), runs **Hypothesis**, **CrossHair**, **coverage**, and runs **pytest** in a subprocess for L3. This is equivalent to trusting the project as if you ran `pytest` and `python -m` on it. |

L1–L2 do not run the execution-based stages. mypy itself can be made to run
code by two project settings: `plugins` (Python modules mypy imports) and
`python_executable` (a program mypy starts). Level 2 looks for the project's
mypy configuration the way mypy does — `mypy.ini`, `.mypy.ini`,
`pyproject.toml` with `[tool.mypy]`, `setup.cfg` with `[mypy]`, in the working
directory and then each ancestor up to the repository root — and, when the
first one found sets either option, does not start mypy without
`--allow-code-execution`. It reports a failed `<mypy>` record naming the file
and the settings instead. A user-level mypy configuration (`~/.mypy.ini`,
`~/.config/mypy/config`) is your own and is not examined. “No
`--allow-code-execution`” is still not a sandbox, and this check covers the two
settings named here, not every way a tool's configuration could matter.

**Process isolation of CrossHair.** Importing CrossHair replaces icontract's
runtime checks with no-ops for the whole process. SereneCode therefore never
imports CrossHair into its own process; the Level 5 worker is a spawned child
and is the only place it is loaded. Contracts stay enforced in the SereneCode
process at every level, which is what lets Level 4 observe violations in a
Level 5 or Level 6 run.

**Project output.** While a check runs, anything project code prints to stdout
is sent to stderr, so it cannot corrupt a `--format json` report or the MCP
stdio stream.

**Child processes** (mypy, pytest, CrossHair CLI) receive a **filtered environment** (`safe_subprocess_env`): only an allowlisted set of variables from your shell is passed through, plus paths such as `PYTHONPATH`. This reduces accidental leakage of secrets to subprocesses; it is **not** a guarantee against malicious code, which still runs with your user privileges.

Setting **`SERENECODE_DEBUG=1`** prints the **names** of environment keys passed to subprocesses (not values) to stderr when `safe_subprocess_env` builds an environment.

## MCP server

- The server uses **stdio** and trusts the **MCP client** to pass sensible `project_root` and file paths. Paths are **not** restricted to a single workspace by Serenecode itself.
- **`--allow-code-execution`** enables the same execution surface as the CLI for Levels 3–6.

## Environment overrides (CLI)

| Variable | Effect |
|----------|--------|
| `SERENECODE_MAX_WORKERS` | Overrides `--workers` when set (integer, ≥ 1, capped at 32 in the pipeline). |
| `SERENECODE_COVERAGE_TIMEOUT` | Overrides `--coverage-timeout` when set (seconds, ≥ 1). |

## Exit code 11 (`ExitCode.ADVISORY`)

`serenecode check --fail-on-advisory` exits **11** when verification **passed** but **advisories** remain (including dead code and module-health warnings). Use this in CI if you want a non-zero exit until advisories are triaged.

## What Serenecode does not do

- It does not isolate network access, filesystem writes, or privileged operations performed by project code.
- It does not verify that third-party tools (mypy, Hypothesis, CrossHair, vulture, pytest) are free of vulnerabilities.

For regulatory or safety-critical systems, treat Serenecode as **one layer** among reviews, tests, and process controls—not as sole assurance.
