"""Mypy adapter for static type checking (Level 2).

This adapter implements the TypeChecker protocol by running mypy
as a subprocess and parsing its output into structured TypeIssue
objects.

This is an adapter module — it handles I/O (subprocess execution)
and is exempt from full contract requirements.
"""

from __future__ import annotations

import re
import subprocess
import sys
import os

import icontract

from serenecode.contracts.predicates import is_positive_int
from serenecode.core.exceptions import ToolNotInstalledError
from serenecode.ports.type_checker import TypeIssue

_MYPY_OUTPUT_PATTERN = re.compile(
    r"^(.+?):(\d+)(?::(\d+))?: (error|warning|note): (.+?)(?:\s+\[(.+?)\])?$"
)


@icontract.invariant(lambda self: is_positive_int(self._timeout), "timeout must be positive")
class MypyTypeChecker:
    """Type checker implementation using mypy.

    Runs mypy as a subprocess with strict mode and parses
    its output into structured TypeIssue objects.
    """

    @icontract.require(lambda timeout: is_positive_int(timeout), "timeout must be positive")
    @icontract.ensure(lambda result: result is None, "result must be None")
    def __init__(self, timeout: int = 120) -> None:
        """Initialize the checker.

        Args:
            timeout: Maximum seconds for mypy to run.
        """
        self._timeout = timeout

    @icontract.require(lambda file_paths: isinstance(file_paths, list), "file_paths must be a list")
    @icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
    def check(
        self,
        file_paths: list[str],
        strict: bool = True,
        search_paths: tuple[str, ...] = (),
    ) -> list[TypeIssue]:
        """Run mypy type checking on the given files.

        Args:
            file_paths: Paths to Python files to check.
            strict: Whether to use strict mode.
            search_paths: Import roots needed to resolve project-local modules.

        Returns:
            List of type issues found.

        Raises:
            ToolNotInstalledError: If mypy is not installed.
        """
        if not file_paths:
            return []

        cmd = [sys.executable, "-m", "mypy"]
        if strict:
            cmd.append("--strict")
        cmd.extend([
            "--no-error-summary",
            "--show-error-codes",
            "--no-color",
        ])
        cmd.extend(file_paths)

        try:
            from serenecode.adapters import safe_subprocess_env

            extra: dict[str, str] = {}
            if search_paths:
                combined = list(search_paths)
                existing_mypypath = os.environ.get("MYPYPATH", "")
                if existing_mypypath:
                    combined.append(existing_mypypath)
                extra["MYPYPATH"] = os.pathsep.join(combined)
            env = safe_subprocess_env(extra_paths=extra)
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=env,
            )
        except FileNotFoundError as exc:
            raise ToolNotInstalledError(
                "mypy is not installed. Install with: pip install mypy"
            ) from exc
        except subprocess.TimeoutExpired:
            return [TypeIssue(
                file="<timeout>",
                line=0,
                column=0,
                severity="error",
                message=f"mypy timed out after {self._timeout}s",
            )]
        combined_output = "\n".join(
            chunk for chunk in (result.stdout, result.stderr) if chunk
        )
        issues = self._parse_output(combined_output)
        if result.returncode == 0:
            return issues

        stderr_text = result.stderr.strip()
        if "No module named mypy" in stderr_text:
            raise ToolNotInstalledError(
                "mypy is not installed. Install with: pip install mypy"
            )

        if issues:
            return issues

        fallback_message = stderr_text or result.stdout.strip() or (
            f"mypy failed with exit code {result.returncode}"
        )
        return [TypeIssue(
            file="<mypy>",
            line=0,
            column=0,
            severity="error",
            message=fallback_message,
        )]

    @icontract.require(lambda output: isinstance(output, str), "output must be a string")
    @icontract.ensure(lambda result: isinstance(result, list), "result must be a list")
    def _parse_output(self, output: str) -> list[TypeIssue]:
        """Parse mypy stdout into TypeIssue objects.

        Args:
            output: mypy stdout content.

        Returns:
            List of parsed type issues.
        """
        issues: list[TypeIssue] = []

        # Loop invariant: issues contains parsed results for lines[0..i]
        for line in output.splitlines():
            match = _MYPY_OUTPUT_PATTERN.match(line.strip())
            if match:
                issues.append(TypeIssue(
                    file=match.group(1),
                    line=int(match.group(2)),
                    column=int(match.group(3)) if match.group(3) else 0,
                    severity=match.group(4),
                    message=match.group(5),
                    code=match.group(6),
                ))

        return issues
