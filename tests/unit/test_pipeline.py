"""Tests for the verification pipeline orchestrator."""

from __future__ import annotations

from dataclasses import dataclass

from serenecode.config import default_config, minimal_config
from serenecode.core.pipeline import SourceFile, run_pipeline
from serenecode.models import CheckResult, CheckStatus, make_check_result
from serenecode.ports.property_tester import PropertyFinding
from serenecode.ports.symbolic_checker import SymbolicFinding
from serenecode.ports.type_checker import TypeIssue
from tests.conftest import assert_violation_or_skip


@dataclass
class _NoIssuesTypeChecker:
    def check(
        self,
        file_paths: list[str],
        strict: bool = True,
        search_paths: tuple[str, ...] = (),
    ) -> list[TypeIssue]:
        return []


@dataclass
class _CapturingTypeChecker:
    captured_search_paths: list[tuple[str, ...]]

    def check(
        self,
        file_paths: list[str],
        strict: bool = True,
        search_paths: tuple[str, ...] = (),
    ) -> list[TypeIssue]:
        self.captured_search_paths.append(search_paths)
        return []


@dataclass
class _RaisingPropertyTester:
    error: Exception

    def test_module(
        self,
        module_path: str,
        max_examples: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[PropertyFinding]:
        raise self.error


@dataclass
class _CapturingPropertyTester:
    captured_search_paths: list[tuple[str, ...]]

    def test_module(
        self,
        module_path: str,
        max_examples: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[PropertyFinding]:
        self.captured_search_paths.append(search_paths)
        return [
            PropertyFinding(
                function_name="square",
                module_path=module_path,
                passed=True,
                finding_type="verified",
                message="ok",
            )
        ]


@dataclass
class _EmptyPropertyTester:
    def test_module(
        self,
        module_path: str,
        max_examples: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[PropertyFinding]:
        return []


@dataclass
class _EmptySymbolicChecker:
    def verify_module(
        self,
        module_path: str,
        per_condition_timeout: int | None = None,
        per_path_timeout: int | None = None,
        search_paths: tuple[str, ...] = (),
    ) -> list[SymbolicFinding]:
        return []


def _make_valid_source() -> str:
    return '''\
"""Module docstring."""

import icontract


@icontract.require(lambda x: x >= 0, "x must be non-negative")
@icontract.ensure(lambda result: result >= 0, "result must be non-negative")
def square(x: float) -> float:
    """Square a number."""
    return x * x
'''


def _make_invalid_source() -> str:
    return '''\
"""Module docstring."""


def broken(x: int, y: int) -> int:
    """Missing contracts."""
    return x + y
'''


class TestPipelineLevel1:
    """Tests for pipeline running Level 1 only."""

    def test_valid_source_passes(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=1, start_level=1, config=default_config())
        assert result.passed is True

    def test_invalid_source_fails(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline((sf,), level=1, start_level=1, config=default_config())
        assert result.passed is False

    def test_empty_files_passes(self) -> None:
        result = run_pipeline((), level=1, start_level=1, config=default_config())
        assert result.passed is True
        assert result.summary.total_functions == 0

    def test_multiple_files(self) -> None:
        valid = SourceFile(
            file_path="valid.py",
            module_path="valid.py",
            source=_make_valid_source(),
        )
        invalid = SourceFile(
            file_path="invalid.py",
            module_path="invalid.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline((valid, invalid), level=1, start_level=1, config=default_config())
        assert result.passed is False
        assert result.summary.total_functions > 0

    def test_exempt_module_skipped(self) -> None:
        sf = SourceFile(
            file_path="adapters/test.py",
            module_path="adapters/test.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline((sf,), level=1, start_level=1, config=default_config())
        assert result.passed is True  # exempt modules produce no results

    def test_level_requested_recorded(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=3, start_level=1, config=default_config())
        assert result.level_requested == 3

    def test_rejects_non_positive_max_workers(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )

        assert_violation_or_skip(lambda: run_pipeline(
            (sf,),
            level=1,
            start_level=1,
            config=default_config(),
            max_workers=0,
        ))

    def test_uses_injected_structural_checker(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        captured: list[tuple[str, str]] = []

        def fake_structural_checker(
            source: str,
            config: object,
            module_path: str,
            file_path: str,
        ) -> CheckResult:
            captured.append((module_path, file_path))
            return make_check_result((), level_requested=1, duration_seconds=0.0)

        result = run_pipeline(
            (sf,),
            level=1,
            start_level=1,
            config=default_config(),
            structural_checker=fake_structural_checker,
        )

        assert result.passed is True
        assert captured == [("test.py", "test.py")]


class TestPipelineEarlyTermination:
    """Tests for early termination behavior."""

    def test_stops_at_level_1_failure(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        # Even though level=4, should stop at 1 due to failures
        result = run_pipeline((sf,), level=4, start_level=1, config=default_config())
        assert result.passed is False

    def test_no_early_termination_runs_all(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline(
            (sf,), level=1, start_level=1, config=default_config(),
            early_termination=False,
        )
        assert result.passed is False


class TestPipelineWithMockAdapters:
    """Tests for pipeline with mock verification adapters."""

    def test_level_2_with_no_adapter_skips(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        # level=2 but no type_checker adapter → just runs level 1
        result = run_pipeline((sf,), level=2, start_level=1, config=default_config())
        assert result.passed is False
        assert result.summary.skipped_count == 1

    def test_level_3_with_no_adapter_skips(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=3, start_level=1, config=default_config())
        assert result.passed is False
        assert result.summary.skipped_count == 2

    def test_level_4_with_no_adapter_skips(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=4, start_level=1, config=default_config())
        assert result.passed is False
        assert result.summary.skipped_count == 3

    def test_start_level_skips_structural_checks(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
            importable_module="demo.module",
        )
        property_tester = _CapturingPropertyTester(captured_search_paths=[])
        result = run_pipeline(
            (sf,),
            level=3,
            start_level=3,
            config=default_config(),
            property_tester=property_tester,
        )
        assert result.passed is True
        assert result.summary.total_functions == 1

    def test_property_import_errors_prevent_false_pass(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )
        result = run_pipeline(
            (sf,),
            level=3,
            start_level=1,
            config=default_config(),
            type_checker=_NoIssuesTypeChecker(),
            property_tester=_RaisingPropertyTester(ImportError("No module named 'pkg'")),
        )
        assert result.passed is False
        assert result.level_achieved == 2
        assert result.summary.skipped_count == 1

    def test_property_tester_receives_import_search_paths(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
            import_search_paths=("/tmp/project/src",),
        )
        property_tester = _CapturingPropertyTester(captured_search_paths=[])
        result = run_pipeline(
            (sf,),
            level=3,
            start_level=1,
            config=minimal_config(),
            type_checker=_NoIssuesTypeChecker(),
            property_tester=property_tester,
        )
        assert result.passed is True
        assert property_tester.captured_search_paths == [("/tmp/project/src",)]

    def test_type_checker_receives_import_search_paths(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            import_search_paths=("/tmp/project/src", "/tmp/project"),
        )
        type_checker = _CapturingTypeChecker(captured_search_paths=[])
        result = run_pipeline(
            (sf,),
            level=2,
            start_level=1,
            config=minimal_config(),
            type_checker=type_checker,
        )

        assert result.passed is True
        assert type_checker.captured_search_paths == [
            ("/tmp/project/src", "/tmp/project"),
        ]

    def test_duration_recorded(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=1, start_level=1, config=default_config())
        assert result.summary.duration_seconds >= 0

    def test_empty_symbolic_results_do_not_claim_level_four(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )
        property_tester = _CapturingPropertyTester(captured_search_paths=[])
        symbolic_checker = _EmptySymbolicChecker()

        result = run_pipeline(
            (sf,),
            level=4,
            start_level=1,
            config=minimal_config(),
            type_checker=_NoIssuesTypeChecker(),
            property_tester=property_tester,
            symbolic_checker=symbolic_checker,
        )

        assert result.passed is False
        assert result.level_achieved == 3

    def test_empty_property_results_do_not_claim_level_three(self) -> None:
        sf = SourceFile(
            file_path="src/pkg/mod.py",
            module_path="pkg/mod.py",
            source=_make_valid_source(),
            importable_module="pkg.mod",
        )

        result = run_pipeline(
            (sf,),
            level=3,
            start_level=1,
            config=minimal_config(),
            type_checker=_NoIssuesTypeChecker(),
            property_tester=_EmptyPropertyTester(),
        )

        assert result.passed is False
        assert result.level_achieved == 2
