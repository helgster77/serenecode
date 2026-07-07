"""Coverage-gap tests for pipeline symbolic helpers and pipeline_helpers.

Targets:
- ``_verify_one_module`` — non-importable module and exception branches.
- ``_process_symbolic_result`` — error branch (tool error vs generic error).
- ``_make_dead_code_skipped_result`` — result construction.
"""

from __future__ import annotations

from dataclasses import dataclass

from serenecode.core.exceptions import ToolNotInstalledError
from serenecode.core.pipeline import (
    SourceFile,
    _process_symbolic_result,
    _verify_one_module,
)
from serenecode.checker.symbolic import transform_symbolic_results
from serenecode.core.pipeline_helpers import _make_dead_code_skipped_result
from serenecode.models import CheckStatus
from serenecode.ports.symbolic_checker import SymbolicFinding


@dataclass
class _RaisingSymbolicChecker:
    """Fake symbolic checker whose verify_module always raises."""

    def verify_module(
        self,
        module_path: str,
        per_condition_timeout: int | None = None,
        per_path_timeout: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[SymbolicFinding]:
        raise RuntimeError("solver exploded")


@dataclass
class _VerifiedSymbolicChecker:
    """Fake symbolic checker that returns a single verified finding."""

    def verify_module(
        self,
        module_path: str,
        per_condition_timeout: int | None = None,
        per_path_timeout: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[SymbolicFinding]:
        return [SymbolicFinding(
            function_name="f",
            module_path=module_path,
            outcome="verified",
            message="all paths verified",
        )]


def _make_sf(importable_module: str | None) -> SourceFile:
    return SourceFile(
        file_path="src/pkg/mod.py",
        module_path="pkg.mod",
        source="x = 1\n",
        importable_module=importable_module,
    )


class TestVerifyOneModule:
    """Tests for _verify_one_module branch coverage."""

    def test_not_importable_returns_tool_not_installed_error(self) -> None:
        sf = _make_sf(None)
        result_sf, findings, error = _verify_one_module(sf, _VerifiedSymbolicChecker())
        assert result_sf is sf
        assert findings is None
        assert isinstance(error, ToolNotInstalledError)
        assert "No importable module" in str(error)

    def test_checker_exception_is_captured(self) -> None:
        sf = _make_sf("pkg.mod")
        result_sf, findings, error = _verify_one_module(sf, _RaisingSymbolicChecker())
        assert result_sf is sf
        assert findings is None
        assert isinstance(error, RuntimeError)
        assert "solver exploded" in str(error)

    def test_successful_verification_returns_findings(self) -> None:
        sf = _make_sf("pkg.mod")
        result_sf, findings, error = _verify_one_module(sf, _VerifiedSymbolicChecker())
        assert result_sf is sf
        assert error is None
        assert findings is not None
        assert len(findings) == 1
        assert findings[0].outcome == "verified"


class TestProcessSymbolicResult:
    """Tests for _process_symbolic_result error and findings branches."""

    def test_tool_not_installed_error_records_skipped(self) -> None:
        sf = _make_sf("pkg.mod")
        results: list = []
        emitted: list[str] = []
        _process_symbolic_result(
            results, sf, None, ToolNotInstalledError("crosshair missing"),
            1, 2, emitted.append, transform_symbolic_results,
        )
        assert len(results) == 1
        assert results[0].status == CheckStatus.SKIPPED
        assert results[0].details[0].finding_type == "unavailable"
        assert "skipped" in results[0].details[0].message
        assert len(emitted) == 1
        assert "[1/2] Skipped pkg.mod" in emitted[0]

    def test_generic_error_records_failed(self) -> None:
        sf = _make_sf("pkg.mod")
        results: list = []
        emitted: list[str] = []
        _process_symbolic_result(
            results, sf, None, ValueError("bad module"),
            2, 2, emitted.append, transform_symbolic_results,
        )
        assert len(results) == 1
        assert results[0].status == CheckStatus.FAILED
        assert results[0].details[0].finding_type == "error"
        assert "failed" in results[0].details[0].message
        assert "bad module" in results[0].details[0].message

    def test_findings_branch_extends_results(self) -> None:
        sf = _make_sf("pkg.mod")
        results: list = []
        emitted: list[str] = []
        findings = [SymbolicFinding(
            function_name="f",
            module_path="pkg.mod",
            outcome="verified",
            message="all paths verified",
        )]
        _process_symbolic_result(
            results, sf, findings, None, 1, 1, emitted.append, transform_symbolic_results,
        )
        assert len(results) == 1
        assert results[0].status == CheckStatus.PASSED
        assert emitted == ["  [1/1] Done pkg.mod"]

    def test_no_error_and_no_findings_is_a_noop(self) -> None:
        """Neither error nor findings: nothing recorded, nothing emitted."""
        sf = _make_sf("pkg.mod")
        results: list = []
        emitted: list[str] = []
        _process_symbolic_result(
            results, sf, None, None, 1, 1, emitted.append, transform_symbolic_results,
        )
        assert results == []
        assert emitted == []


class TestMakeDeadCodeSkippedResult:
    """Tests for _make_dead_code_skipped_result construction."""

    def test_builds_visible_skipped_result(self) -> None:
        result = _make_dead_code_skipped_result("vulture is not installed")
        assert result.status == CheckStatus.SKIPPED
        assert result.function == "<dead_code>"
        assert result.file == "dead_code"
        assert result.details[0].finding_type == "unavailable"
        assert result.details[0].message == "vulture is not installed"
        assert result.details[0].suggestion is not None
