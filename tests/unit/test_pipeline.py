"""Tests for the verification pipeline orchestrator."""

from __future__ import annotations

from serenecode.config import default_config, minimal_config
from serenecode.core.pipeline import SourceFile, run_pipeline
from serenecode.models import CheckStatus


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
        result = run_pipeline((sf,), level=1, config=default_config())
        assert result.passed is True

    def test_invalid_source_fails(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline((sf,), level=1, config=default_config())
        assert result.passed is False

    def test_empty_files_passes(self) -> None:
        result = run_pipeline((), level=1, config=default_config())
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
        result = run_pipeline((valid, invalid), level=1, config=default_config())
        assert result.passed is False
        assert result.summary.total_functions > 0

    def test_exempt_module_skipped(self) -> None:
        sf = SourceFile(
            file_path="adapters/test.py",
            module_path="adapters/test.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline((sf,), level=1, config=default_config())
        assert result.passed is True  # exempt modules produce no results

    def test_level_requested_recorded(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=3, config=default_config())
        assert result.level_requested == 3


class TestPipelineEarlyTermination:
    """Tests for early termination behavior."""

    def test_stops_at_level_1_failure(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        # Even though level=4, should stop at 1 due to failures
        result = run_pipeline((sf,), level=4, config=default_config())
        assert result.passed is False

    def test_no_early_termination_runs_all(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_invalid_source(),
        )
        result = run_pipeline(
            (sf,), level=1, config=default_config(),
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
        result = run_pipeline((sf,), level=2, config=default_config())
        assert result.passed is True

    def test_level_3_with_no_adapter_skips(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=3, config=default_config())
        assert result.passed is True

    def test_level_4_with_no_adapter_skips(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=4, config=default_config())
        assert result.passed is True

    def test_duration_recorded(self) -> None:
        sf = SourceFile(
            file_path="test.py",
            module_path="test.py",
            source=_make_valid_source(),
        )
        result = run_pipeline((sf,), level=1, config=default_config())
        assert result.summary.duration_seconds >= 0
