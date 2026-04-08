# Security and trust model

Serenecode is a **local developer tool**. It reads files you point it at and, when you opt in, runs code from those projects on your machine. It does not implement a sandbox.

## Trust boundaries

| Mode | What happens |
|------|----------------|
| **Levels 1–2** (no `--allow-code-execution`) | Parses and type-checks source; does not import project modules for execution-based checks. |
| **Levels 3–6** or MCP with `--allow-code-execution` | Imports project modules **in-process** (`compile` / `exec`), runs **Hypothesis**, **CrossHair**, **coverage**, and may run **pytest** in a subprocess. This is equivalent to trusting the project as if you ran `pytest` and `python -m` on it. |

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

`serenecode check --fail-on-advisory` exits **11** when verification **passed** but **dead-code advisories** remain. Use this in CI if you want a non-zero exit until advisories are triaged.

## What Serenecode does not do

- It does not isolate network access, filesystem writes, or privileged operations performed by project code.
- It does not verify that third-party tools (mypy, Hypothesis, CrossHair, vulture, pytest) are free of vulnerabilities.

For regulatory or safety-critical systems, treat Serenecode as **one layer** among reviews, tests, and process controls—not as sole assurance.
